#!/usr/bin/env python3
"""Strict Wayback player-snapshot recovery probe for the 17 confirmed
unresolved Indicator episodes.

Scope
-----
By default targets all 17 CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED records
(``status == "no_audio"`` in ``indicator_enclosure_map.json``, excluding the
four protected "Two Indicators" story IDs).

Use ``--story-id`` / ``--targets`` to restrict to specific story IDs (e.g. the
five-target validation batch).

Method
------
For each target:

1. Construct the exact player URL::

       https://www.npr.org/player/embed/<story_id>/<audio_id>

2. Query the Wayback CDX API for HTTP-200 captures of that URL only (no
   wildcard, no related-URL broadening).

3. Fetch up to WAYBACK_MAX_CAPTURES (3) archived player-HTML snapshots using
   the ``id_/`` modifier (raw page, no Wayback toolbar injection).

4. Extract episode-specific audio candidates from each archived page:

   Simplecast (Simplecast era, ~2021+):
     * ``/episodes/<uuid>/audio/`` URL path — strong episode-specific signal
     * ``awEpisodeId=<uuid>`` query parameter
     * ``"episodeId"`` / ``"episodeUuid"`` JSON key with a UUID value

   Legacy NPR audio (2020-era):
     * ``ondemand.npr.org/...mp3`` / ``.m4a`` / ``.mp4``
     * ``play.podtrac.com/npr-510325/...``

   Rejected regardless of proximity to "simplecast":
     * The known Simplecast show UUID 0a4e8d3b-fe23-4948-9e39-20fcf16f9331
     * Cookie / session / analytics UUIDs (generic nearby UUIDs)
     * NPR live radio streams (``live.mp3``, ``npr.org/...live``)
     * Generic MP3 URLs not tied to this episode

5. For each candidate, perform bounded HEAD/GET validation:
   - Follow redirects
   - Require HTTP 200 or 206
   - Require audio MIME type (audio/*, mpeg, mp3)
   - Record content_length and final_url

6. Assign one of these unambiguous classifications:
   - RECOVERED_AND_VALIDATED
   - UUID_FOUND_AUDIO_UNRESOLVED
   - AUDIO_CANDIDATE_NOT_PLAYABLE
   - NO_TARGET_MEDIA_FOUND
   - NO_WAYBACK_CAPTURES
   - CDX_NETWORK_FAILURE
   - ARCHIVE_FETCH_FAILED
   - PARTIAL_ARCHIVE_FAILURE_NO_MEDIA

7. Write a per-target checkpoint JSON immediately after completion so a
   workflow timeout cannot lose completed results.

Request budget (with WAYBACK_MAX_CAPTURES=3, MAX_RETRIES=3):
  1 CDX request         × 3 attempts  =  3 attempts
  ≤3 archive fetches    × 3 attempts  =  9 attempts
  ≤3 audio validations  × 2 attempts  =  6 attempts (HEAD+GET fallback)
  Per-target maximum:                   18 attempts
  5-target batch maximum:               90 attempts

Safety
------
This script is **read-only with respect to production files**.  It never
modifies ``theindicator_feed.xml``, ``indicator_history.json``, or
``indicator_enclosure_map.json``.  A SHA-256 hash-guard assertion enforces
this before the run, after each target, and at final completion.

Output
------
Per-target checkpoints: ``data/recovery/wayback_player_snapshots/checkpoint_<story_id>.json``
Summary report:         ``data/recovery/wayback_player_snapshots/wayback_player_snapshot_report.json``
"""
from __future__ import annotations

import argparse
import json
import random
import re
import socket
import ssl
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
OUTPUT_DIR = REPO_ROOT / "data" / "recovery" / "wayback_player_snapshots"
OUTPUT_FILE = OUTPUT_DIR / "wayback_player_snapshot_report.json"

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

# The Simplecast show-level UUID for The Indicator.  Episode UUIDs are
# distinct and must NOT match this value.
SIMPLECAST_SHOW_UUID = "0a4e8d3b-fe23-4948-9e39-20fcf16f9331"

TIMEOUT_SECONDS = 20
MAX_RETRIES = 3
BACKOFF_SCHEDULE_SECONDS = (2.0, 5.0)
BACKOFF_JITTER_SECONDS = 0.75
# Maximum archived player-HTML pages to fetch per target (design spec: 3).
WAYBACK_MAX_CAPTURES = 3
# Maximum bytes of archived HTML to read (avoids giant pages).
MAX_TEXT_BYTES = 300_000
# Maximum bytes saved in per-capture diagnostic HTML files.
MAX_CAPTURE_DIAGNOSTIC_BYTES = 80_000
# Maximum audio candidates to validate per target.
MAX_AUDIO_CANDIDATES = 3
# Expected number of confirmed-unresolved targets (no_audio, excluding Two Indicators).
EXPECTED_TARGET_COUNT = 17

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IndicatorWaybackPlayerSnapshotProbe/2.0; "
        "+https://github.com/drfujisawa/theindicator-rss)"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Encoding": "identity",
}

RETRYABLE_HTTP_STATUS_CODES = frozenset({429, 502, 503, 504})

# ---------------------------------------------------------------------------
# UUID patterns (strictly episode-specific)
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

# /episodes/<uuid>/audio/... — canonical Simplecast episode audio path
_EPISODE_AUDIO_PATH_RE = re.compile(
    r"/episodes/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/audio",
    re.IGNORECASE,
)

# awEpisodeId=<uuid> query parameter (NPR player bootstrap)
_AW_EPISODE_ID_RE = re.compile(
    r"awEpisodeId[=:]"
    r"[\"\s]*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

# "episodeId" / "episodeUuid" / "episode_id" explicit JSON key
_EPISODE_KEY_RE = re.compile(
    r'"episode(?:[_]?[Ii]d|[_]?[Uu]uid)"\s*:\s*"'
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r'"',
    re.IGNORECASE,
)

# simplecastaudio.com URL that contains an /episodes/<uuid>/audio path
_SIMPLECAST_EPISODE_AUDIO_URL_RE = re.compile(
    r"https?://[^\s\"'<>\\]*simplecastaudio\.com"
    r"[^\s\"'<>\\]*/episodes/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/audio[^\s\"'<>\\]*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Legacy audio URL patterns (2020-era NPR podcast hosting)
# ---------------------------------------------------------------------------

# ondemand.npr.org episode audio (mp3/m4a/mp4 only — not live streams)
_NPR_ONDEMAND_RE = re.compile(
    r"https?://ondemand\.npr\.org/[^\s\"'<>\\]*\.(?:mp3|m4a|mp4)[^\s\"'<>\\]*",
    re.IGNORECASE,
)

# play.podtrac.com/npr-510325/... (NPR podcast network)
_PODTRAC_RE = re.compile(
    r"https?://play\.podtrac\.com/npr-510325/[^\s\"'<>\\]*\.(?:mp3|m4a|mp4)[^\s\"'<>\\]*",
    re.IGNORECASE,
)

# Reject live radio streams — these patterns are NEVER valid episode audio
_LIVE_STREAM_RE = re.compile(
    r"(?:live\.mp3|/stream(?:s)?/|/live(?:stream)?/|npr\.org.*live|npr\.org.*stream)",
    re.IGNORECASE,
)


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


def _fetch(url: str, read_bytes: int = MAX_TEXT_BYTES, method: str = "GET") -> dict:
    """Fetch *url* with retries.  Returns a result dict.

    For audio validation, pass ``method="HEAD"`` with ``read_bytes=0`` first,
    then fall back to a partial GET if HEAD fails.
    """
    def _retry_backoff_seconds(attempt: int) -> float:
        if attempt >= MAX_RETRIES:
            return 0.0
        base_idx = min(attempt - 1, len(BACKOFF_SCHEDULE_SECONDS) - 1)
        base = BACKOFF_SCHEDULE_SECONDS[base_idx]
        jitter = random.uniform(-BACKOFF_JITTER_SECONDS, BACKOFF_JITTER_SECONDS)
        return max(0.0, base + jitter)

    def _reason_text(reason: object) -> str:
        if isinstance(reason, BaseException):
            return f"{type(reason).__name__}: {reason}"
        return str(reason)

    def _error_type_from_text(text: str) -> str:
        lowered = text.lower()
        if "ssl" in lowered and "handshake" in lowered:
            return "ssl_handshake_timeout"
        if "timed out" in lowered or "timeout" in lowered:
            return "timeout"
        if "connection reset" in lowered or "connreset" in lowered:
            return "connection_reset"
        return "network_error"

    def _is_retryable_network_reason(reason: object) -> tuple[bool, str]:
        if isinstance(reason, socket.timeout):
            return True, "timeout"
        if isinstance(reason, TimeoutError):
            return True, "timeout"
        if isinstance(reason, ConnectionResetError):
            return True, "connection_reset"
        if isinstance(reason, ssl.SSLError):
            err_text = _reason_text(reason)
            err_type = _error_type_from_text(err_text)
            return err_type in {"ssl_handshake_timeout", "timeout"}, err_type
        err_text = _reason_text(reason)
        err_type = _error_type_from_text(err_text)
        return err_type in {"timeout", "ssl_handshake_timeout", "connection_reset"}, err_type

    last_error: str = ""
    attempts: list[dict] = []
    req_headers = dict(HEADERS)
    if method == "HEAD":
        # HEAD requires stripped Accept-Encoding to avoid redirect loops
        req_headers.pop("Accept-Encoding", None)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(url, headers=req_headers, method=method)
            with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                raw = resp.read(read_bytes) if read_bytes > 0 else b""
                return {
                    "ok": True,
                    "final_url": resp.geturl(),
                    "http_status": getattr(resp, "status", None),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "content_length": resp.headers.get("Content-Length"),
                    "text": raw.decode("utf-8", errors="replace"),
                    "attempts": attempts + [
                        {
                            "attempt": attempt,
                            "status": "ok",
                            "method": method,
                            "http_status": getattr(resp, "status", None),
                        }
                    ],
                }
        except HTTPError as exc:
            last_error = f"HTTPError {exc.code}: {exc.reason}"
            retryable = exc.code in RETRYABLE_HTTP_STATUS_CODES
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "http_error",
                    "method": method,
                    "http_status": exc.code,
                    "error_type": f"http_{exc.code}",
                    "error": last_error,
                    "retryable": retryable,
                }
            )
            if not retryable:
                break
        except URLError as exc:
            last_error = f"URLError: {exc.reason}"
            retryable, error_type = _is_retryable_network_reason(exc.reason)
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "url_error",
                    "method": method,
                    "error_type": error_type,
                    "error": last_error,
                    "retryable": retryable,
                }
            )
            if not retryable:
                break
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            err_type = _error_type_from_text(last_error)
            retryable = err_type in {"timeout", "ssl_handshake_timeout", "connection_reset"}
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "exception",
                    "method": method,
                    "error_type": err_type,
                    "error": last_error,
                    "retryable": retryable,
                }
            )
            if not retryable:
                break
        if attempt < MAX_RETRIES:
            backoff_seconds = _retry_backoff_seconds(attempt)
            if attempts:
                attempts[-1]["backoff_seconds"] = backoff_seconds
            time.sleep(backoff_seconds)
    return {"ok": False, "error": last_error, "text": "", "attempts": attempts}


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
    """Return up to WAYBACK_MAX_CAPTURES timestamps sorted by proximity to
    *target_date* (YYYY-MM-DD).
    """
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
# UUID eligibility — strict episode-specific filtering
# ---------------------------------------------------------------------------


def _is_episode_uuid(uuid: str) -> bool:
    """Return True only if *uuid* is eligible as a Simplecast episode UUID.

    Rejected:
    - The known Simplecast show UUID for The Indicator
    - Any UUID that is not already tied to an episode-specific signal
      (this is enforced by only calling this after an episode-specific pattern)
    """
    return uuid.lower() != SIMPLECAST_SHOW_UUID.lower()


# ---------------------------------------------------------------------------
# Extraction — strictly scoped to player HTML
# ---------------------------------------------------------------------------


def _unique(values: list[str]) -> list[str]:
    seen_set: set[str] = set()
    seen: list[str] = []
    for v in values:
        v = v.replace("\\/", "/").replace("\\u0026", "&").strip()
        if v and v not in seen_set:
            seen_set.add(v)
            seen.append(v)
    return seen


def extract_from_player_html(html: str) -> dict:
    """Extract strictly episode-specific audio candidates from archived player HTML.

    Simplecast episode UUID eligibility rules (in order of strength):
      1. URL path ``/episodes/<uuid>/audio/`` — strongest: episode-specific CDN path
      2. ``awEpisodeId=<uuid>`` query parameter — NPR player bootstrap
      3. ``"episodeId"`` / ``"episodeUuid"`` JSON key — structured player data

    Generic UUIDs merely near the word "simplecast" are NOT extracted here
    (they could be show UUIDs, cookie tokens, analytics IDs, or session values).

    The known Simplecast show UUID (SIMPLECAST_SHOW_UUID) is always rejected.

    Legacy audio URLs (2020-era):
      - ondemand.npr.org/*.mp3 / *.m4a / *.mp4
      - play.podtrac.com/npr-510325/*.mp3 / *.m4a / *.mp4

    Live streams and generic MP3 URLs not matching the above patterns are
    silently excluded.

    Returns::

        {
            "simplecast_episode_uuids": [...],  # episode-specific, show UUID excluded
            "simplecast_audio_urls": [...],     # /episodes/<uuid>/audio CDN URLs
            "legacy_audio_urls": [...],         # ondemand.npr.org / podtrac URLs
            "episode_key_uuids": [...],         # from episodeId/episodeUuid keys
            "aw_episode_id_uuids": [...],       # from awEpisodeId parameter
        }
    """
    simplecast_episode_uuids: list[str] = []
    simplecast_audio_urls: list[str] = []
    legacy_audio_urls: list[str] = []
    episode_key_uuids: list[str] = []
    aw_episode_id_uuids: list[str] = []

    # 1. /episodes/<uuid>/audio CDN URLs — strongest Simplecast episode signal
    for m in _SIMPLECAST_EPISODE_AUDIO_URL_RE.finditer(html):
        url = m.group(0).replace("\\/", "/")
        uuid = m.group(1).lower()
        if _is_episode_uuid(uuid):
            if url not in simplecast_audio_urls:
                simplecast_audio_urls.append(url)
            if uuid not in simplecast_episode_uuids:
                simplecast_episode_uuids.append(uuid)

    # Also catch /episodes/<uuid>/audio patterns that aren't a full CDN URL
    for m in _EPISODE_AUDIO_PATH_RE.finditer(html):
        uuid = m.group(1).lower()
        if _is_episode_uuid(uuid) and uuid not in simplecast_episode_uuids:
            simplecast_episode_uuids.append(uuid)

    # 2. awEpisodeId=<uuid> (NPR player bootstrap parameter)
    for m in _AW_EPISODE_ID_RE.finditer(html):
        uuid = m.group(1).lower()
        if _is_episode_uuid(uuid):
            if uuid not in aw_episode_id_uuids:
                aw_episode_id_uuids.append(uuid)
            if uuid not in simplecast_episode_uuids:
                simplecast_episode_uuids.append(uuid)

    # 3. "episodeId" / "episodeUuid" JSON keys
    for m in _EPISODE_KEY_RE.finditer(html):
        uuid = m.group(1).lower()
        if _is_episode_uuid(uuid):
            if uuid not in episode_key_uuids:
                episode_key_uuids.append(uuid)
            if uuid not in simplecast_episode_uuids:
                simplecast_episode_uuids.append(uuid)

    # 4. Legacy NPR audio URLs — ondemand.npr.org
    for m in _NPR_ONDEMAND_RE.finditer(html):
        url = m.group(0).replace("\\/", "/")
        if not _LIVE_STREAM_RE.search(url):
            if url not in legacy_audio_urls:
                legacy_audio_urls.append(url)

    # 5. play.podtrac.com/npr-510325 podcast network URLs
    for m in _PODTRAC_RE.finditer(html):
        url = m.group(0).replace("\\/", "/")
        if not _LIVE_STREAM_RE.search(url):
            if url not in legacy_audio_urls:
                legacy_audio_urls.append(url)

    return {
        "simplecast_episode_uuids": _unique(simplecast_episode_uuids),
        "simplecast_audio_urls": _unique(simplecast_audio_urls),
        "legacy_audio_urls": _unique(legacy_audio_urls),
        "episode_key_uuids": _unique(episode_key_uuids),
        "aw_episode_id_uuids": _unique(aw_episode_id_uuids),
    }


# ---------------------------------------------------------------------------
# Audio validation
# ---------------------------------------------------------------------------


def _is_audio_content_type(content_type: str | None) -> bool:
    """Return True for audio MIME types (audio/*, mpeg, mp3 substrings)."""
    ct = (content_type or "").lower()
    return ct.startswith("audio/") or "mpeg" in ct or "mp3" in ct


def validate_audio_candidate(
    candidate_url: str,
    fetch_fn: Callable[..., dict] = _fetch,
) -> dict:
    """Perform a bounded HEAD → GET validation for *candidate_url*.

    Steps:
    1. HEAD request — lightweight check
    2. If HEAD fails, GET with Range: bytes=0-8191

    Requires HTTP 200 or 206 AND audio MIME type for ``playable = True``.
    """
    head = fetch_fn(candidate_url, read_bytes=0, method="HEAD")
    if head.get("ok") and head.get("http_status") in (200, 206):
        validation_result = head
    else:
        validation_result = fetch_fn(candidate_url, read_bytes=8192, method="GET")

    playable = bool(
        validation_result.get("ok")
        and validation_result.get("http_status") in (200, 206)
        and _is_audio_content_type(validation_result.get("content_type"))
    )
    return {
        "candidate_url": candidate_url,
        "playable": playable,
        "final_url": validation_result.get("final_url"),
        "http_status": validation_result.get("http_status"),
        "content_type": validation_result.get("content_type"),
        "content_length": validation_result.get("content_length"),
        "error": validation_result.get("error") if not validation_result.get("ok") else None,
    }


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify(
    found_uuids: list[str],
    found_audio_urls: list[str],
    legacy_audio_urls: list[str],
    validated_audio: dict | None,
    cdx_capture_count: int,
    cdx_ok: bool,
    fetched_snapshot_count: int,
    failed_snapshot_count: int,
) -> str:
    """Assign an unambiguous classification for the target.

    Rules (in priority order):
    - CDX_NETWORK_FAILURE: CDX fetch failed
    - NO_WAYBACK_CAPTURES: CDX OK but no captures for the exact player URL
    - RECOVERED_AND_VALIDATED: playable audio confirmed (200/206 + audio MIME)
    - UUID_FOUND_AUDIO_UNRESOLVED: UUID extracted but no playable audio confirmed
    - AUDIO_CANDIDATE_NOT_PLAYABLE: candidates exist but none returned audio MIME
    - ARCHIVE_FETCH_FAILED: CDX had captures but none of selected archived pages fetched
    - PARTIAL_ARCHIVE_FAILURE_NO_MEDIA: some fetches failed, inspected fetches had no media
    - NO_TARGET_MEDIA_FOUND: one or more pages were fetched/inspected with no media found
    """
    if not cdx_ok:
        return "CDX_NETWORK_FAILURE"
    if cdx_capture_count == 0:
        return "NO_WAYBACK_CAPTURES"
    if validated_audio and validated_audio.get("playable"):
        return "RECOVERED_AND_VALIDATED"
    if found_uuids:
        return "UUID_FOUND_AUDIO_UNRESOLVED"
    if found_audio_urls or legacy_audio_urls:
        return "AUDIO_CANDIDATE_NOT_PLAYABLE"
    if fetched_snapshot_count == 0:
        return "ARCHIVE_FETCH_FAILED"
    if failed_snapshot_count > 0:
        return "PARTIAL_ARCHIVE_FAILURE_NO_MEDIA"
    return "NO_TARGET_MEDIA_FOUND"


def _save_capture_diagnostic_html(
    output_dir: Path,
    story_id: str,
    timestamp: str,
    html: str,
) -> Path:
    """Save a compact per-capture HTML diagnostic copy in *output_dir*."""
    output_dir.mkdir(parents=True, exist_ok=True)
    compact_html = " ".join(html.split())
    compact_bytes = compact_html.encode("utf-8")
    if len(compact_bytes) > MAX_CAPTURE_DIAGNOSTIC_BYTES:
        compact_html = compact_bytes[:MAX_CAPTURE_DIAGNOSTIC_BYTES].decode(
            "utf-8", errors="ignore"
        )
    path = output_dir / f"capture_{story_id}_{timestamp}.html"
    path.write_text(compact_html, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------


def _checkpoint_path(output_dir: Path, story_id: str) -> Path:
    return output_dir / f"checkpoint_{story_id}.json"


def _write_checkpoint(output_dir: Path, story_id: str, data: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(output_dir, story_id)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_checkpoint(output_dir: Path, story_id: str) -> dict | None:
    path = _checkpoint_path(output_dir, story_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# Per-target probe
# ---------------------------------------------------------------------------


def probe_target(
    story_id: str,
    audio_id: str,
    date: str,
    title: str,
    output_dir: Path | None = None,
    fetch_fn: Callable[..., dict] = _fetch,
    force: bool = False,
) -> dict:
    """Run the full Wayback player-snapshot probe for one episode target.

    Steps:
    1. Build the exact player URL.
    2. Check for an existing checkpoint (skip unless *force* is True).
    3. Query CDX for HTTP-200 captures of that exact URL.
    4. Fetch each archived player page (raw HTML via ``id_/``).
    5. Extract strictly episode-specific Simplecast UUIDs and legacy audio URLs.
    6. Validate each candidate (HEAD then GET fallback).
    7. Assign unambiguous classification.
    8. Write per-target checkpoint immediately.
    """
    effective_dir = output_dir or OUTPUT_DIR

    # Resume: skip if checkpoint exists and force is not set
    if not force:
        prior = _load_checkpoint(effective_dir, story_id)
        if prior is not None:
            print(f"  [{date}] {title} — skipping (checkpoint exists)")
            return prior

    player_url = f"https://www.npr.org/player/embed/{story_id}/{audio_id}"

    result: dict = {
        "title": title,
        "date": date,
        "story_id": story_id,
        "audio_id": audio_id,
        "player_url": player_url,
        "cdx_capture_count": 0,
        "cdx_ok": False,
        "cdx_error": None,
        "cdx_attempts": [],
        "snapshots": [],
        "simplecast_episode_uuids": [],
        "simplecast_audio_urls": [],
        "legacy_audio_urls": [],
        "validated_audio": None,
        "classification": "NO_WAYBACK_CAPTURES",
    }

    print(f"  [{date}] {title}")
    print(f"    player_url: {player_url}")

    # Step 3: CDX query (exact player URL, no wildcards)
    cdx_resp = fetch_fn(_cdx_url(player_url), read_bytes=200_000)
    result["cdx_attempts"] = cdx_resp.get("attempts", [])
    if not cdx_resp.get("ok"):
        result["cdx_error"] = cdx_resp.get("error", "unknown")
        result["classification"] = "CDX_NETWORK_FAILURE"
        print(f"    CDX error: {result['cdx_error']}")
        _write_checkpoint(effective_dir, story_id, result)
        return result

    result["cdx_ok"] = True
    timestamps = _parse_cdx(cdx_resp["text"], date)
    result["cdx_capture_count"] = len(timestamps)
    print(f"    CDX captures: {len(timestamps)}")

    if not timestamps:
        result["classification"] = "NO_WAYBACK_CAPTURES"
        _write_checkpoint(effective_dir, story_id, result)
        return result

    # Steps 4+5: fetch each archived player page and extract episode media
    all_uuids: list[str] = []
    all_audio_urls: list[str] = []
    all_legacy_urls: list[str] = []
    fetched_snapshot_count = 0
    failed_snapshot_count = 0

    for ts in timestamps:
        archived_url = f"https://web.archive.org/web/{ts}id_/{player_url}"
        snap: dict = {
            "timestamp": ts,
            "archived_url": archived_url,
            "fetch_status": None,
            "fetch_attempts": [],
            "simplecast_episode_uuids": [],
            "simplecast_audio_urls": [],
            "legacy_audio_urls": [],
            "episode_key_uuids": [],
            "aw_episode_id_uuids": [],
            "error": None,
        }

        fetch_resp = fetch_fn(archived_url)
        snap["fetch_attempts"] = fetch_resp.get("attempts", [])
        if not fetch_resp.get("ok"):
            snap["fetch_status"] = "error"
            snap["error"] = fetch_resp.get("error", "unknown")
            print(f"    {ts}: fetch error — {snap['error']}")
            failed_snapshot_count += 1
            result["snapshots"].append(snap)
            continue

        html = fetch_resp.get("text", "")
        snap["fetch_status"] = "ok"
        snap["content_type"] = fetch_resp.get("content_type", "")
        snap["html_length"] = len(html)
        fetched_snapshot_count += 1
        capture_path = _save_capture_diagnostic_html(effective_dir, story_id, ts, html)
        snap["capture_file"] = capture_path.name

        extracted = extract_from_player_html(html)
        snap["simplecast_episode_uuids"] = extracted["simplecast_episode_uuids"]
        snap["simplecast_audio_urls"] = extracted["simplecast_audio_urls"]
        snap["legacy_audio_urls"] = extracted["legacy_audio_urls"]
        snap["episode_key_uuids"] = extracted["episode_key_uuids"]
        snap["aw_episode_id_uuids"] = extracted["aw_episode_id_uuids"]

        result["snapshots"].append(snap)

        print(
            f"    {ts}: uuids={len(snap['simplecast_episode_uuids'])} "
            f"audio_urls={len(snap['simplecast_audio_urls'])} "
            f"legacy_urls={len(snap['legacy_audio_urls'])}"
        )

        for uuid in extracted["simplecast_episode_uuids"]:
            if uuid not in all_uuids:
                all_uuids.append(uuid)
        for url in extracted["simplecast_audio_urls"]:
            if url not in all_audio_urls:
                all_audio_urls.append(url)
        for url in extracted["legacy_audio_urls"]:
            if url not in all_legacy_urls:
                all_legacy_urls.append(url)

    result["simplecast_episode_uuids"] = all_uuids
    result["simplecast_audio_urls"] = all_audio_urls
    result["legacy_audio_urls"] = all_legacy_urls

    # Step 6: validate audio candidates
    # Build ordered candidate list: prefer full audio URLs over UUID-only,
    # then legacy URLs.
    candidates: list[str] = []
    for url in all_audio_urls:
        if url not in candidates:
            candidates.append(url)
    for url in all_legacy_urls:
        if url not in candidates:
            candidates.append(url)

    validated_audio: dict | None = None
    validation_attempts: list[dict] = []

    for cand_url in candidates[:MAX_AUDIO_CANDIDATES]:
        val = validate_audio_candidate(cand_url, fetch_fn=fetch_fn)
        validation_attempts.append(val)
        if val.get("playable"):
            validated_audio = val
            break

    result["validated_audio"] = validated_audio
    result["validation_attempts"] = validation_attempts

    # Step 7: classify
    result["classification"] = _classify(
        found_uuids=all_uuids,
        found_audio_urls=all_audio_urls,
        legacy_audio_urls=all_legacy_urls,
        validated_audio=validated_audio,
        cdx_capture_count=result["cdx_capture_count"],
        cdx_ok=result["cdx_ok"],
        fetched_snapshot_count=fetched_snapshot_count,
        failed_snapshot_count=failed_snapshot_count,
    )

    print(f"    → classification: {result['classification']}")
    if all_uuids:
        print(f"    → episode_uuids: {all_uuids[:3]}")
    if validated_audio:
        print(f"    → validated_audio: {validated_audio.get('final_url')}")

    # Step 8: write checkpoint immediately
    _write_checkpoint(effective_dir, story_id, result)

    return result


# ---------------------------------------------------------------------------
# Target loading
# ---------------------------------------------------------------------------


def load_targets(story_ids: list[str] | None = None) -> list[dict]:
    """Load CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED targets.

    Source: ``indicator_enclosure_map.json``, episodes with
    ``status == "no_audio"``, excluding the four "Two Indicators" story IDs.

    If *story_ids* is provided, only targets whose story_id is in the list
    are returned (in the same deterministic sorted order).
    """
    payload = json.loads(ENCLOSURE_MAP.read_text(encoding="utf-8"))
    episodes = payload.get("episodes", {})
    targets: list[dict] = []
    for ep in episodes.values():
        if ep.get("status") != "no_audio":
            continue
        sid = str(ep.get("story_id", ""))
        if sid in TWO_INDICATORS_STORY_IDS:
            continue
        if story_ids is not None and sid not in story_ids:
            continue
        targets.append(
            {
                "date": str(ep.get("date", "")),
                "title": str(ep.get("title", "")),
                "story_id": sid,
                "audio_id": str(ep.get("audio_id", "")),
                "npr_url": str(ep.get("npr_url", "")),
            }
        )
    targets.sort(key=lambda e: (e["date"], e["story_id"]))
    return targets


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wayback player-snapshot recovery probe for unresolved Indicator episodes.",
    )
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "--story-id",
        dest="story_ids",
        metavar="ID",
        action="append",
        help="Story ID to probe (repeatable; omit to run all 17 targets)",
    )
    target_group.add_argument(
        "--targets",
        metavar="ID1,ID2,...",
        help="Comma-separated story IDs to probe",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=str(OUTPUT_DIR),
        help="Directory for checkpoint JSON files and the summary report",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-probe targets even if a checkpoint already exists",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    output_dir = Path(args.output_dir)

    # Resolve requested story IDs
    requested_ids: list[str] | None = None
    if args.story_ids:
        requested_ids = [s.strip() for s in args.story_ids]
    elif args.targets:
        requested_ids = [s.strip() for s in args.targets.split(",") if s.strip()]

    print("=" * 72)
    print("Wayback Player-Snapshot Recovery Probe")
    if requested_ids:
        print(f"Scope: {len(requested_ids)} explicitly requested target(s)")
    else:
        print("Scope: all 17 CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED targets")
    print("=" * 72)

    # Capture production-file hashes before any work
    before_hashes = _capture_hashes(PRODUCTION_FILES)

    targets = load_targets(story_ids=requested_ids)

    if requested_ids is not None and len(targets) != len(requested_ids):
        missing = set(requested_ids) - {t["story_id"] for t in targets}
        print(f"WARNING: {len(missing)} requested story ID(s) not found in enclosure map: {missing}")

    if not targets:
        print("No targets to process.  Exiting.")
        return

    if requested_ids is None and len(targets) != EXPECTED_TARGET_COUNT:
        print(
            f"WARNING: expected {EXPECTED_TARGET_COUNT} targets, found {len(targets)}. "
            "Proceeding anyway."
        )

    print(f"Loaded {len(targets)} target(s).\n")
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    classification_counts: dict[str, int] = {}

    for i, target in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}]")

        # Check production files before each target (in addition to before/after run)
        mid_hashes = _capture_hashes(PRODUCTION_FILES)
        _assert_production_unchanged(before_hashes, mid_hashes)

        result = probe_target(
            story_id=target["story_id"],
            audio_id=target["audio_id"],
            date=target["date"],
            title=target["title"],
            output_dir=output_dir,
            force=args.force,
        )
        results.append(result)

        cl = result.get("classification", "UNKNOWN")
        classification_counts[cl] = classification_counts.get(cl, 0) + 1

    # Build report
    report = {
        "method": "wayback-player-snapshot-strict-episode-uuid-recovery",
        "scope": (
            f"{len(requested_ids)}-explicitly-requested-targets"
            if requested_ids
            else "17-confirmed-unresolved-indicator-episodes"
        ),
        "description": (
            "Strictly scoped Wayback player-HTML probe: constructs the exact "
            "NPR embed player URL per episode, queries CDX for HTTP-200 captures "
            "(exact URL, no wildcards), fetches archived player HTML (id_/ modifier), "
            "extracts strictly episode-specific Simplecast UUIDs and legacy audio URLs, "
            "validates each candidate (HEAD+GET, 200/206, audio MIME), and assigns "
            "unambiguous classifications.  A UUID alone is never RECOVERED_AND_VALIDATED."
        ),
        "two_indicators_excluded": sorted(TWO_INDICATORS_STORY_IDS),
        "simplecast_show_uuid_rejected": SIMPLECAST_SHOW_UUID,
        "wayback_max_captures_per_target": WAYBACK_MAX_CAPTURES,
        "request_budget": {
            "cdx_per_target": f"1 × {MAX_RETRIES} = {MAX_RETRIES} attempts",
            "archive_fetches_per_target": f"≤{WAYBACK_MAX_CAPTURES} × {MAX_RETRIES} = {WAYBACK_MAX_CAPTURES * MAX_RETRIES} attempts",
            "audio_validation_per_target": f"≤{MAX_AUDIO_CANDIDATES} × 2 (HEAD+GET) = {MAX_AUDIO_CANDIDATES * 2} attempts",
            "per_target_max": MAX_RETRIES + WAYBACK_MAX_CAPTURES * MAX_RETRIES + MAX_AUDIO_CANDIDATES * 2,
            "five_target_max": 5 * (MAX_RETRIES + WAYBACK_MAX_CAPTURES * MAX_RETRIES + MAX_AUDIO_CANDIDATES * 2),
        },
        "classifications": classification_counts,
        "targets": results,
    }

    report_path = output_dir / "wayback_player_snapshot_report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Final production-file guard
    after_hashes = _capture_hashes(PRODUCTION_FILES)
    _assert_production_unchanged(before_hashes, after_hashes)

    print("\n" + "=" * 72)
    print("Classifications:")
    for k, v in classification_counts.items():
        print(f"  {k}: {v}")
    print(f"\nSaved report: {report_path}")
    print(f"Checkpoints:  {output_dir}/checkpoint_<story_id>.json")
    print("Production files: UNCHANGED (verified)")
    print("=" * 72)


if __name__ == "__main__":
    main()
