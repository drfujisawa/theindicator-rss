#!/usr/bin/env python3
"""Strict Wayback player-snapshot recovery pass for the 17 confirmed
unresolved Indicator episodes.

Scope
-----
Targets exactly the 17 CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED records
(``status == "no_audio"`` in ``indicator_enclosure_map.json``, excluding the
four protected "Two Indicators" story IDs).

Method
------
For each target:

1. Construct the exact player URL::

       https://www.npr.org/player/embed/<story_id>/<audio_id>

2. Query the Wayback CDX API for HTTP-200 captures of that URL only.

3. Fetch up to WAYBACK_MAX_CAPTURES archived player-HTML snapshots using the
   ``id_/`` modifier (raw page, no Wayback toolbar injection).

4. Extract Simplecast episode UUIDs **directly from the player HTML**.  The
   known-good June 22 player page embeds its Simplecast UUID in the bootstrap
   HTML; we match the same patterns here:

   * ``simplecastaudio.com`` URL containing a UUID path segment
   * ``"episodeId"`` / ``"episode_id"`` JSON key with a UUID value
   * Bare UUID adjacent to "simplecast" in a 120-character window

   Extraction is strictly scoped to the archived player page HTML and never
   broadened to generic NPR story pages or external resources.

5. Extract ``simplecastaudio.com`` audio URLs from the same HTML.

6. Record structured results per snapshot and per target.

Safety
------
This script is **read-only with respect to production files**.  It never
modifies ``theindicator_feed.xml``, ``indicator_history.json``, or
``indicator_enclosure_map.json``.  A hash-guard assertion enforces this.

Output
------
``data/recovery/indicator_wayback_player_snapshot_probe.json``
"""
from __future__ import annotations

import json
import re
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
ENCLOSURE_MAP = REPO_ROOT / "indicator_enclosure_map.json"
OUTPUT_FILE = REPO_ROOT / "data" / "recovery" / "indicator_wayback_player_snapshot_probe.json"

PRODUCTION_FILES = (
    REPO_ROOT / "theindicator_feed.xml",
    REPO_ROOT / "indicator_history.json",
    REPO_ROOT / "indicator_enclosure_map.json",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TWO_INDICATORS_STORY_IDS: frozenset[str] = frozenset(
    {"1013954358", "1029846068", "1034085667", "1038307729"}
)

TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
BACKOFF_SECONDS = 1.5
# Maximum archived player-HTML pages to fetch per target.
WAYBACK_MAX_CAPTURES = 8
# Maximum bytes of archived HTML to read (avoids giant pages).
MAX_TEXT_BYTES = 300_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IndicatorWaybackPlayerSnapshotProbe/2.0; "
        "+https://github.com/drfujisawa/theindicator-rss)"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Encoding": "identity",
}

# Compiled patterns for Simplecast UUID and audio URL extraction
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# simplecastaudio.com URL with a UUID path segment (the canonical audio CDN)
_SIMPLECAST_AUDIO_URL_RE = re.compile(
    r"https?://[^\s\"'<>\\]*simplecastaudio\.com[^\s\"'<>\\]*"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"[^\s\"'<>\\]*",
    re.IGNORECASE,
)

# "episodeId" or "episode_id" JSON key immediately followed by a UUID value
_EPISODE_ID_KEY_RE = re.compile(
    r'"episode[_]?[Ii]d"\s*:\s*"'
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r'"',
    re.IGNORECASE,
)

# Simplecast CDN URL (no UUID required — captures any simplecastaudio.com path)
_SIMPLECAST_CDN_URL_RE = re.compile(
    r"https?://[^\s\"'<>\\]*simplecastaudio\.com[^\s\"'<>\\]*",
    re.IGNORECASE,
)

# NPR player bootstrap blob patterns — "episodeUuid", "uuid", etc.
_UUID_KEY_PATTERNS = [
    re.compile(
        r'"(?:episode[_]?[Uu]uid|simplecast[_]?[Uu]uid|uuid)"\s*:\s*"'
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        r'"',
        re.IGNORECASE,
    ),
    _EPISODE_ID_KEY_RE,
]


# ---------------------------------------------------------------------------
# Production file guard
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    if not path.exists():
        return "__missing__"
    return sha256(path.read_bytes()).hexdigest()


def _capture_hashes(paths: tuple[Path, ...]) -> dict[str, str]:
    return {str(p): _hash_file(p) for p in paths}


def _assert_production_unchanged(
    before: dict[str, str], after: dict[str, str]
) -> None:
    changed = [p for p in before if before[p] != after[p]]
    if changed:
        raise RuntimeError(
            "PRODUCTION FILE MUTATION DETECTED — aborting to protect feed integrity. "
            f"Changed: {changed}"
        )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _fetch(url: str, read_bytes: int = MAX_TEXT_BYTES) -> dict:
    """Fetch *url* with retries.  Returns a result dict."""
    last_error: str = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                raw = resp.read(read_bytes)
                return {
                    "ok": True,
                    "final_url": resp.geturl(),
                    "http_status": getattr(resp, "status", None),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "text": raw.decode("utf-8", errors="replace"),
                }
        except HTTPError as exc:
            last_error = f"HTTPError {exc.code}: {exc.reason}"
            if exc.code in (404, 403, 410):
                break  # non-retryable
        except URLError as exc:
            last_error = f"URLError: {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_SECONDS)
    return {"ok": False, "error": last_error, "text": ""}


# ---------------------------------------------------------------------------
# CDX query
# ---------------------------------------------------------------------------


def _cdx_url(player_url: str) -> str:
    return (
        "https://web.archive.org/cdx/search/cdx"
        "?url=" + quote(player_url, safe="")
        + "&output=json"
        "&filter=statuscode:200"
        "&collapse=digest"
        "&fl=timestamp,original,statuscode,mimetype,digest"
        "&limit=50"
    )


def _parse_cdx(cdx_text: str, target_date: str) -> list[str]:
    """Return timestamps sorted by proximity to *target_date* (YYYY-MM-DD)."""
    try:
        rows = json.loads(cdx_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list) or len(rows) < 2:
        return []
    header = rows[0]
    if not isinstance(header, list):
        return []
    try:
        ts_idx = header.index("timestamp")
        sc_idx = header.index("statuscode")
    except ValueError:
        return []
    timestamps: list[str] = []
    for row in rows[1:]:
        if not isinstance(row, list) or len(row) <= max(ts_idx, sc_idx):
            continue
        if str(row[sc_idx]).strip() != "200":
            continue
        ts = str(row[ts_idx]).strip()
        if ts:
            timestamps.append(ts)
    target_ymd = int(target_date.replace("-", ""))
    timestamps.sort(key=lambda t: abs(int(t[:8]) - target_ymd))
    return timestamps[:WAYBACK_MAX_CAPTURES]


# ---------------------------------------------------------------------------
# Extraction — strictly scoped to player HTML
# ---------------------------------------------------------------------------


def _unique(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        v = v.replace("\\/", "/").replace("\\u0026", "&").strip()
        if v and v not in seen:
            seen.append(v)
    return seen


def extract_from_player_html(html: str) -> dict:
    """Extract Simplecast UUIDs and audio URLs from player-page HTML only.

    Extraction is strictly limited to patterns that appear in NPR embed player
    bootstrap HTML.  No generic audio URL scanning is performed.

    Returns::

        {
            "simplecast_uuids": [...],         # UUIDs found via key patterns
            "simplecast_audio_urls": [...],    # simplecastaudio.com URLs
            "uuid_near_simplecast": [...],     # UUIDs within 120 chars of "simplecast"
            "episode_id_key_uuids": [...],     # from "episodeId"/"episode_id" keys
        }
    """
    simplecast_uuids: list[str] = []
    simplecast_audio_urls: list[str] = []
    uuid_near_simplecast: list[str] = []
    episode_id_key_uuids: list[str] = []

    # 1. UUID from structured key patterns ("episodeUuid", "episodeId", etc.)
    for pattern in _UUID_KEY_PATTERNS:
        for m in pattern.finditer(html):
            uuid = m.group(1).lower()
            if uuid not in simplecast_uuids:
                simplecast_uuids.append(uuid)
            if pattern is _EPISODE_ID_KEY_RE:
                if uuid not in episode_id_key_uuids:
                    episode_id_key_uuids.append(uuid)

    # 2. simplecastaudio.com URLs containing a UUID segment
    for m in _SIMPLECAST_AUDIO_URL_RE.finditer(html):
        url = m.group(0).replace("\\/", "/")
        if url not in simplecast_audio_urls:
            simplecast_audio_urls.append(url)
        # Also capture the UUID from within the URL
        uuid_match = _UUID_RE.search(url)
        if uuid_match:
            uuid = uuid_match.group(0).lower()
            if uuid not in simplecast_uuids:
                simplecast_uuids.append(uuid)

    # 3. Any UUID within 120 characters of the word "simplecast" in the HTML
    lower_html = html.lower()
    sc_pos = 0
    while True:
        sc_pos = lower_html.find("simplecast", sc_pos)
        if sc_pos == -1:
            break
        window_start = max(0, sc_pos - 120)
        window_end = min(len(html), sc_pos + 120)
        window = html[window_start:window_end]
        for uuid_m in _UUID_RE.finditer(window):
            uuid = uuid_m.group(0).lower()
            if uuid not in uuid_near_simplecast:
                uuid_near_simplecast.append(uuid)
            if uuid not in simplecast_uuids:
                simplecast_uuids.append(uuid)
        sc_pos += len("simplecast")

    # 4. Any simplecastaudio.com URL (including ones without a UUID segment)
    for m in _SIMPLECAST_CDN_URL_RE.finditer(html):
        url = m.group(0).replace("\\/", "/")
        if url not in simplecast_audio_urls:
            simplecast_audio_urls.append(url)

    return {
        "simplecast_uuids": _unique(simplecast_uuids),
        "simplecast_audio_urls": _unique(simplecast_audio_urls),
        "uuid_near_simplecast": _unique(uuid_near_simplecast),
        "episode_id_key_uuids": _unique(episode_id_key_uuids),
    }


# ---------------------------------------------------------------------------
# Per-target probe
# ---------------------------------------------------------------------------


def probe_target(story_id: str, audio_id: str, date: str, title: str) -> dict:
    """Run the full Wayback player-snapshot probe for one episode target.

    Steps:
    1. Build the exact player URL.
    2. Query CDX for HTTP-200 captures of that URL.
    3. Fetch each archived player page (raw HTML via ``id_/``).
    4. Extract Simplecast UUIDs from each page's HTML.
    5. Return structured result.
    """
    player_url = f"https://www.npr.org/player/embed/{story_id}/{audio_id}"

    result: dict = {
        "title": title,
        "date": date,
        "story_id": story_id,
        "audio_id": audio_id,
        "player_url": player_url,
        "cdx_capture_count": 0,
        "cdx_error": None,
        "snapshots": [],
        "best_simplecast_uuid": None,
        "best_simplecast_audio_url": None,
        "recovery_status": "no_captures",
    }

    print(f"  [{date}] {title}")
    print(f"    player_url: {player_url}")

    # Step 2: CDX query
    cdx_resp = _fetch(_cdx_url(player_url), read_bytes=200_000)
    if not cdx_resp.get("ok"):
        result["cdx_error"] = cdx_resp.get("error", "unknown")
        result["recovery_status"] = "cdx_error"
        print(f"    CDX error: {result['cdx_error']}")
        return result

    timestamps = _parse_cdx(cdx_resp["text"], date)
    result["cdx_capture_count"] = len(timestamps)
    print(f"    CDX captures: {len(timestamps)}")

    if not timestamps:
        result["recovery_status"] = "no_captures"
        return result

    # Step 3+4: fetch each archived player page and extract
    found_uuids: list[str] = []
    found_audio_urls: list[str] = []

    for ts in timestamps:
        archived_url = f"https://web.archive.org/web/{ts}id_/{player_url}"
        snap: dict = {
            "timestamp": ts,
            "archived_url": archived_url,
            "fetch_status": None,
            "simplecast_uuids": [],
            "simplecast_audio_urls": [],
            "uuid_near_simplecast": [],
            "episode_id_key_uuids": [],
            "error": None,
        }

        fetch_resp = _fetch(archived_url)
        if not fetch_resp.get("ok"):
            snap["fetch_status"] = "error"
            snap["error"] = fetch_resp.get("error", "unknown")
            print(f"    {ts}: fetch error — {snap['error']}")
            result["snapshots"].append(snap)
            continue

        html = fetch_resp.get("text", "")
        snap["fetch_status"] = "ok"
        snap["content_type"] = fetch_resp.get("content_type", "")
        snap["html_length"] = len(html)

        extracted = extract_from_player_html(html)
        snap["simplecast_uuids"] = extracted["simplecast_uuids"]
        snap["simplecast_audio_urls"] = extracted["simplecast_audio_urls"]
        snap["uuid_near_simplecast"] = extracted["uuid_near_simplecast"]
        snap["episode_id_key_uuids"] = extracted["episode_id_key_uuids"]

        result["snapshots"].append(snap)

        print(
            f"    {ts}: uuids={len(snap['simplecast_uuids'])} "
            f"audio_urls={len(snap['simplecast_audio_urls'])}"
        )

        for uuid in extracted["simplecast_uuids"]:
            if uuid not in found_uuids:
                found_uuids.append(uuid)
        for url in extracted["simplecast_audio_urls"]:
            if url not in found_audio_urls:
                found_audio_urls.append(url)

    # Determine recovery status
    if found_uuids or found_audio_urls:
        result["best_simplecast_uuid"] = found_uuids[0] if found_uuids else None
        result["best_simplecast_audio_url"] = found_audio_urls[0] if found_audio_urls else None
        result["recovery_status"] = "uuid_found" if found_uuids else "audio_url_found"
    else:
        result["recovery_status"] = "not_found"

    print(f"    → status: {result['recovery_status']}")
    if result["best_simplecast_uuid"]:
        print(f"    → uuid: {result['best_simplecast_uuid']}")
    if result["best_simplecast_audio_url"]:
        print(f"    → audio_url: {result['best_simplecast_audio_url']}")

    return result


# ---------------------------------------------------------------------------
# Target loading
# ---------------------------------------------------------------------------


def load_targets() -> list[dict]:
    """Load the 17 CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED targets.

    Source: ``indicator_enclosure_map.json``, episodes with
    ``status == "no_audio"``, excluding the four "Two Indicators" story IDs.
    """
    payload = json.loads(ENCLOSURE_MAP.read_text(encoding="utf-8"))
    episodes = payload.get("episodes", {})
    targets: list[dict] = []
    for ep in episodes.values():
        if ep.get("status") != "no_audio":
            continue
        if str(ep.get("story_id", "")) in TWO_INDICATORS_STORY_IDS:
            continue
        targets.append(
            {
                "date": str(ep.get("date", "")),
                "title": str(ep.get("title", "")),
                "story_id": str(ep.get("story_id", "")),
                "audio_id": str(ep.get("audio_id", "")),
                "npr_url": str(ep.get("npr_url", "")),
            }
        )
    targets.sort(key=lambda e: (e["date"], e["story_id"]))
    return targets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("=" * 72)
    print("Wayback Player-Snapshot Recovery Probe")
    print("Scope: 17 CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED targets")
    print("=" * 72)

    # Capture production-file hashes before any work
    before_hashes = _capture_hashes(PRODUCTION_FILES)

    targets = load_targets()
    if len(targets) != 17:
        print(
            f"WARNING: expected 17 targets, found {len(targets)}. "
            "Proceeding anyway."
        )

    print(f"Loaded {len(targets)} targets.\n")

    results: list[dict] = []
    summary = {
        "total": len(targets),
        "uuid_found": 0,
        "audio_url_found": 0,
        "not_found": 0,
        "no_captures": 0,
        "cdx_error": 0,
    }

    for i, target in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}]")
        result = probe_target(
            story_id=target["story_id"],
            audio_id=target["audio_id"],
            date=target["date"],
            title=target["title"],
        )
        results.append(result)

        status = result.get("recovery_status", "")
        if status in summary:
            summary[status] += 1
        else:
            summary[status] = summary.get(status, 0) + 1

    # Build report
    report = {
        "method": "wayback-player-snapshot-strict-uuid-recovery",
        "scope": "17-confirmed-unresolved-indicator-episodes",
        "description": (
            "Strictly scoped Wayback player-HTML probe: constructs the exact "
            "NPR embed player URL per episode, queries CDX for HTTP-200 captures, "
            "fetches archived player HTML (id_/ modifier), and extracts Simplecast "
            "episode UUIDs directly from the page HTML.  No generic page resources "
            "or story-page endpoints are scanned."
        ),
        "two_indicators_excluded": sorted(TWO_INDICATORS_STORY_IDS),
        "wayback_max_captures_per_target": WAYBACK_MAX_CAPTURES,
        "summary": summary,
        "targets": results,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Final production-file guard
    after_hashes = _capture_hashes(PRODUCTION_FILES)
    _assert_production_unchanged(before_hashes, after_hashes)

    print("\n" + "=" * 72)
    print("Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nSaved: {OUTPUT_FILE}")
    print("Production files: UNCHANGED (verified)")
    print("=" * 72)


if __name__ == "__main__":
    main()
