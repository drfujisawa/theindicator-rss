#!/usr/bin/env python3
"""No-audio target recovery pipeline — audio-ID-first narrow pass.

This is the narrow, audio-ID-first recovery mode.  Generic feed/show-page
endpoints have been removed to prevent unrelated current-feed Simplecast
candidates from poisoning the results.

True request budget (actual HTTP attempts including retries):
  Per-target logical requests:
    ① Endpoint probes (7 exact endpoints × 2 retries)        = 14 attempts max
    ② Wayback archive fetches (≤3 captures × 2 retries)      =  6 attempts max
    ③ Candidate validation (≤3 candidates × 2 probes × 2 retries) = 12 attempts max
  Per-target maximum:                                         = 32 attempts max
  5-target batch maximum:                                     = 160 attempts max

Sleep overhead (worst case, every attempt retries once):
  7 endpoints × (0.8 s backoff) = 5.6 s per target
  3 archive fetches × 0.8 s     = 2.4 s per target
  3 candidates × 2 × 0.8 s     = 4.8 s per target
  Total sleep per target:       ≈ 12.8 s; 5 targets: ≈ 64 s

Conservative runtime at TIMEOUT_SECONDS=10 per attempt, all timing out:
  5 × 32 × 10 s = 1 600 s ≈ 27 min — safely under the 35-min workflow ceiling.
  A nominal run (1–3 s responses, few retries) completes in well under 10 min.

Batching:
  - Default batch size: 5 targets.
  - Batch n selects targets[(n-1)*batch_size : n*batch_size] from the
    deterministic (date, story_id) sorted order.  The slice is fixed — it does
    NOT shift when completed checkpoints are removed, avoiding fragile offset
    logic.  Targets with an existing checkpoint are automatically skipped within
    the batch (idempotent re-runs are safe).

Audio-ID provenance chain (Simplecast era):
  exact target player URL player/embed/<story_id>/<audio_id>
  → live or archived player bootstrap HTML
  → extracted simplecastaudio.com URL or Simplecast episode UUID
  → validated playable audio asset
  This chain scores ≥ 0.75 (≥ 0.7 threshold) and qualifies as RECOVERED_AND_VALIDATED
  even though the final Simplecast URL does not itself contain the numeric NPR IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import re
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
ENCLOSURE_MAP = REPO_ROOT / "indicator_enclosure_map.json"
HISTORY_FILE = REPO_ROOT / "indicator_history.json"
OUTPUT_DIR_DEFAULT = REPO_ROOT / "data" / "recovery" / "no_audio_targets"
PRODUCTION_FILES = (
    REPO_ROOT / "theindicator_feed.xml",
    REPO_ROOT / "indicator_history.json",
    REPO_ROOT / "indicator_enclosure_map.json",
)

# Reduced from 15 s: tighter per-attempt ceiling for exact player/archive probes.
TIMEOUT_SECONDS = 10
# Reduced from 3: fewer retries for exact player/archive probes.  The workflow
# hard ceiling handles the worst case; we prefer fast failure over long stalls.
MAX_RETRIES = 2
BACKOFF_SECONDS = 0.8
MAX_TEXT_BYTES = 400_000

# Hard cap on candidates validated per target.  Because all candidate sources
# are now episode-specific (player embed URL or its Wayback equivalent), 3 is
# sufficient: the first ranked candidate from a target-specific source wins.
MAX_CANDIDATES_PER_TARGET = 3

# Maximum number of archived player pages to fetch per target from Wayback.
# Each fetch consumes one logical request slot (with MAX_RETRIES retries).
WAYBACK_MAX_CAPTURES = 3

# Minimum provenance confidence required for RECOVERED_AND_VALIDATED
# (fixes blocking issue #1).  "high" means score >= 0.7 which requires
# at least one strong episode-specific signal:
#   story_id/audio_id in source endpoint URL (0.35) + in final URL (0.45) = 0.80 ≥ 0.7, OR
#   any single 0.7+ combination.
MINIMUM_PROVENANCE_CONFIDENCE = "high"

TWO_INDICATORS_STORY_IDS = {
    "1013954358",
    "1029846068",
    "1034085667",
    "1038307729",
}


@dataclass(frozen=True)
class Target:
    date: str
    title: str
    story_id: str
    audio_id: str
    npr_url: str


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)


def capture_file_hashes(paths: list[Path] | tuple[Path, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in paths:
        key = str(path)
        if not path.exists():
            hashes[key] = "__missing__"
            continue
        digest = sha256(path.read_bytes()).hexdigest()
        hashes[key] = digest
    return hashes


def production_files_changed(
    before_hashes: dict[str, str],
    after_hashes: dict[str, str],
) -> bool:
    return any(before_hashes.get(path) != after_hashes.get(path) for path in before_hashes)


def assert_production_files_unchanged(
    before_hashes: dict[str, str],
    after_hashes: dict[str, str],
) -> None:
    """Raise RuntimeError if any production file was mutated."""
    changed = [
        path
        for path in before_hashes
        if before_hashes.get(path) != after_hashes.get(path)
    ]
    if changed:
        raise RuntimeError(
            "PRODUCTION FILE MUTATION DETECTED — aborting to protect feed integrity. "
            f"Changed paths: {changed}"
        )


def load_no_audio_targets(map_path: Path = ENCLOSURE_MAP) -> list[Target]:
    payload = load_json(map_path)
    episodes = payload.get("episodes", {})
    targets: list[Target] = []
    for episode in episodes.values():
        if episode.get("status") != "no_audio":
            continue
        targets.append(
            Target(
                date=str(episode.get("date", "")),
                title=str(episode.get("title", "")),
                story_id=str(episode.get("story_id", "")),
                audio_id=str(episode.get("audio_id", "")),
                npr_url=str(episode.get("npr_url", "")),
            )
        )
    targets.sort(key=lambda item: (item.date, item.story_id))
    return targets


def build_history_index(history_path: Path = HISTORY_FILE) -> dict[str, dict]:
    payload = load_json(history_path)
    episodes = payload.get("episodes", [])
    return {str(item.get("story_id")): item for item in episodes}


def baseline_classification(story_id: str) -> str:
    if story_id in TWO_INDICATORS_STORY_IDS:
        return "PROBABLY_NOT_SEPARATE_EPISODE"
    return "CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED"


def select_batch_targets(targets: list[Target], batch: int, batch_size: int) -> list[Target]:
    """Return the fixed slice of targets for the given 1-based batch number.

    The slice is derived purely from position in the sorted list, so it is
    stable even when previously-completed checkpoints are not removed.
    """
    if batch < 1:
        raise ValueError(f"batch must be >= 1, got {batch}")
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    start = (batch - 1) * batch_size
    return targets[start : start + batch_size]


def partition_by_completion(
    targets: list[Target], output_dir: Path
) -> tuple[list[Target], list[Target]]:
    """Split targets into (to_process, already_completed) based on checkpoint files.

    A target is "already completed" when a per-target checkpoint JSON file
    ``checkpoint_<story_id>.json`` already exists in *output_dir*.  Such
    targets are skipped so that re-running the same batch is idempotent.
    """
    to_process: list[Target] = []
    already_completed: list[Target] = []
    for target in targets:
        checkpoint_path = output_dir / f"checkpoint_{target.story_id}.json"
        if checkpoint_path.exists():
            already_completed.append(target)
        else:
            to_process.append(target)
    return to_process, already_completed


def build_endpoint_matrix(target: Target) -> list[dict]:
    """Return the ordered list of episode-specific endpoints to probe.

    Generic endpoints (current NPR feed, NPR show page) and wildcard CDX
    endpoints (``*story_id*``, ``*audio_id*``) have been intentionally removed
    from this audio-ID-first pass to prevent unrelated current-feed Simplecast
    candidates from entering the candidate pool.

    The ``wayback_cdx_player`` entry returns CDX index metadata only; the
    actual archived player pages are fetched separately in ``investigate_target``
    via the Wayback archive fetch step.
    """
    story_url = target.npr_url
    player_url = f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"
    encoded_player = quote(player_url, safe="")
    return [
        {"endpoint": "story_url", "url": story_url},
        # Primary source: exact episode player embed page.  Contains both
        # story_id and audio_id in the URL, giving 0.55 provenance score
        # when a candidate is found here.
        {"endpoint": "player_embed", "url": player_url},
        {
            "endpoint": "story_template",
            "url": f"https://www.npr.org/templates/story/story.php?storyId={target.story_id}",
        },
        {"endpoint": "transcript", "url": f"https://www.npr.org/transcripts/{target.story_id}"},
        # 2020-era fallback: NPR Legacy API can return the ondemand.npr.org
        # enclosure URL directly for pre-Simplecast episodes.
        {
            "endpoint": "legacy_api_story",
            "url": f"https://api.npr.org/query?id={target.story_id}&output=JSON",
        },
        {
            "endpoint": "legacy_api_audio",
            "url": f"https://api.npr.org/query?id={target.audio_id}&output=JSON",
        },
        # CDX index for the exact player embed URL.  Followed by actual archive
        # fetch in investigate_target when captures are found.
        {
            "endpoint": "wayback_cdx_player",
            "url": (
                "https://web.archive.org/cdx/search/cdx"
                f"?url={encoded_player}&output=json&fl=timestamp,original,statuscode,mimetype"
                f"&from={target.date.replace('-', '')}00000000"
                f"&to={target.date.replace('-', '')[:-2]}999999999"
                "&limit=10"
            ),
        },
    ]


def _single_request(
    url: str,
    method: str = "GET",
    timeout: int = TIMEOUT_SECONDS,
    headers: dict | None = None,
    read_bytes: int = MAX_TEXT_BYTES,
) -> dict:
    effective_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NoAudioTargetRecovery/1.0)",
    }
    if headers:
        effective_headers.update(headers)
    request = Request(url, method=method, headers=effective_headers)
    with urlopen(request, timeout=timeout) as response:
        body = response.read(read_bytes)
        content_type = response.headers.get("Content-Type", "")
        text = ""
        if "text" in content_type.lower() or "json" in content_type.lower() or "xml" in content_type.lower():
            try:
                text = body.decode("utf-8", errors="replace")
            except Exception:
                text = ""
        return {
            "ok": True,
            "http_status": getattr(response, "status", None),
            "final_url": response.geturl(),
            "content_type": content_type,
            "content_length": response.headers.get("Content-Length"),
            "text": text,
        }


def request_with_retries(
    url: str,
    method: str = "GET",
    timeout: int = TIMEOUT_SECONDS,
    retries: int = MAX_RETRIES,
    backoff_seconds: float = BACKOFF_SECONDS,
    headers: dict | None = None,
    read_bytes: int = MAX_TEXT_BYTES,
    request_fn: Callable[..., dict] = _single_request,
) -> dict:
    errors = []
    for attempt in range(1, retries + 1):
        try:
            result = request_fn(
                url=url,
                method=method,
                timeout=timeout,
                headers=headers,
                read_bytes=read_bytes,
            )
            result["attempt"] = attempt
            result["error_type"] = None
            return result
        except HTTPError as exc:
            errors.append({"attempt": attempt, "error_type": "http_error", "detail": str(exc), "status": exc.code})
        except URLError as exc:
            errors.append({"attempt": attempt, "error_type": "network_error", "detail": str(exc)})
        except TimeoutError as exc:
            errors.append({"attempt": attempt, "error_type": "timeout", "detail": str(exc)})
        except Exception as exc:  # pragma: no cover - guardrail
            errors.append({"attempt": attempt, "error_type": "unexpected_error", "detail": str(exc)})
        if attempt < retries:
            time.sleep(backoff_seconds * attempt)
    return {
        "ok": False,
        "http_status": None,
        "final_url": None,
        "content_type": None,
        "content_length": None,
        "text": "",
        "error_type": errors[-1]["error_type"] if errors else "unknown_error",
        "errors": errors,
    }


def extract_candidate_audio_urls(text: str) -> list[str]:
    """Extract all plausible audio URLs from page HTML or JSON text.

    Applies multiple extraction strategies adapted from
    ``poc_simplecast_enclosure_recovery.py``:

    1. Direct regex match for .mp3 URLs and simplecastaudio.com paths.
    2. ``"audioUrl"`` / ``"audio_url"`` JSON keys.
    3. ``"enclosureUrl"`` / ``"enclosure"`` JSON keys.
    4. Inline JSON strings containing known audio CDN hostnames.
    5. ``__NEXT_DATA__`` / state-blob JSON for embedded audio references.

    Returns a deduplicated list in discovery order.
    """
    if not text:
        return []

    seen: list[str] = []

    def _add(raw: str) -> None:
        cleaned = raw.replace("\\/", "/").replace("\\u0026", "&").strip()
        if cleaned and cleaned not in seen and _is_audio_like(cleaned):
            seen.append(cleaned)

    def _is_audio_like(url: str) -> bool:
        u = url.lower()
        return (
            "simplecastaudio.com" in u
            or ".mp3" in u
            or ("podtrac.com/npr-510325" in u)
            or "prfx.byspotify.com" in u
        )

    # 1. Direct regex: .mp3 URLs and full simplecastaudio.com paths.
    _re_direct = re.compile(
        r"https?://[^\"'\s<>]+(?:\.mp3|/audio(?:[/?#]|$)|simplecastaudio\.com[^\"'\s<>]*)",
        re.IGNORECASE,
    )
    for m in _re_direct.findall(text):
        _add(m)

    # 2. "audioUrl" / "audio_url" JSON keys.
    for m in re.findall(r'"audio[Uu]rl"\s*:\s*"([^"]+)"', text):
        _add(m)

    # 3. "enclosureUrl" / "enclosure" JSON keys.
    for m in re.findall(r'"enclosure(?:[Uu]rl)?"\s*:\s*"([^"]+)"', text):
        _add(m)

    # 4. Inline JSON strings containing known audio CDN hostnames anywhere in page.
    for m in re.findall(
        r'"(https?://[^"]*(?:simplecastaudio\.com|podtrac\.com/npr-510325|prfx\.byspotify\.com)[^"]*)"',
        text,
        re.IGNORECASE,
    ):
        _add(m)

    # 5. State blobs (__NEXT_DATA__ etc.): extract .mp3 strings from raw JSON text.
    for blob in re.findall(
        r'(?:window\.__(?:STATE|INITIAL_STATE|DATA|PROPS)|__NEXT_DATA__|initialState)\s*=\s*({.{0,500000}?})\s*;?\s*</script>',
        text,
        re.IGNORECASE | re.DOTALL,
    ):
        for m in re.findall(r'"(https?://[^"]+\.mp3[^"]*)"', blob):
            _add(m)

    return seen


def extract_simplecast_uuid(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(
        r"/episodes/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/audio",
        url,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def select_wayback_captures(
    cdx_text: str,
    target_date: str,
    max_captures: int = WAYBACK_MAX_CAPTURES,
) -> list[dict]:
    """Parse a Wayback CDX JSON response and return up to *max_captures* records.

    Only HTTP-200 captures are considered.  Records are sorted by temporal
    proximity to *target_date* (closest-to-publication first) so that the most
    relevant archived snapshot is probed first.

    ``cdx_text`` must be the raw JSON string returned by the CDX API with
    ``output=json&fl=timestamp,...,statuscode,...``.
    """
    if not cdx_text:
        return []
    try:
        rows = json.loads(cdx_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list) or len(rows) < 2:
        return []
    # The first row is the header; subsequent rows are data.
    headers = rows[0]
    if not isinstance(headers, list):
        return []
    try:
        ts_idx = headers.index("timestamp")
        sc_idx = headers.index("statuscode")
    except ValueError:
        return []
    captures: list[dict] = []
    for row in rows[1:]:
        if not isinstance(row, list) or len(row) <= max(ts_idx, sc_idx):
            continue
        status = str(row[sc_idx]).strip()
        if status != "200":
            continue
        ts = str(row[ts_idx]).strip()
        captures.append({"timestamp": ts})
    # Sort by proximity: target_date "YYYY-MM-DD" → "YYYYMMDD" integer.
    target_ymd = int(target_date.replace("-", ""))
    captures.sort(key=lambda c: abs(int(c["timestamp"][:8]) - target_ymd))
    return captures[:max_captures]


def fetch_wayback_player_page(
    player_url: str,
    timestamp: str,
    request_fn: Callable[..., dict] = request_with_retries,
) -> dict:
    """Fetch an archived NPR player embed page from the Wayback Machine.

    The ``id_`` modifier requests the raw archived page without the Wayback
    toolbar injection, which keeps the HTML cleaner for audio URL extraction.

    Returns the same response dict shape as ``request_with_retries``.
    """
    archive_url = f"https://web.archive.org/web/{timestamp}id_/{player_url}"
    return request_fn(url=archive_url, method="GET")


def is_audio_like_content_type(content_type: str | None) -> bool:
    value = (content_type or "").lower()
    return value.startswith("audio/") or "mpeg" in value or "mp3" in value


def validate_audio_candidate(
    candidate_url: str,
    request_fn: Callable[..., dict] = request_with_retries,
) -> dict:
    head = request_fn(url=candidate_url, method="HEAD", read_bytes=0)
    probe = head if head.get("ok") else request_fn(
        url=candidate_url,
        method="GET",
        headers={"Range": "bytes=0-8191"},
        read_bytes=8192,
    )
    playable = bool(
        probe.get("ok")
        and probe.get("http_status") in (200, 206)
        and is_audio_like_content_type(probe.get("content_type"))
    )
    return {
        "candidate_url": candidate_url,
        "playable": playable,
        "final_url": probe.get("final_url"),
        "http_status": probe.get("http_status"),
        "content_type": probe.get("content_type"),
        "content_length": probe.get("content_length"),
        "simplecast_uuid": extract_simplecast_uuid(probe.get("final_url") or candidate_url),
        "validation_error_type": None if probe.get("ok") else probe.get("error_type"),
    }


def compute_identity_provenance(
    target: Target,
    source_endpoints: list[str],
    validated_audio: dict | None,
) -> dict:
    """Score the provenance chain between the target and a validated audio URL.

    Scoring rules:
    - +0.55 when the source endpoint contains **both** target story_id and
      audio_id.  This applies to the exact player embed URL
      ``player/embed/<story_id>/<audio_id>`` and to its Wayback equivalent
      ``web.archive.org/web/<ts>id_/.../player/embed/<story_id>/<audio_id>``.
      It ensures that the chain
        exact player URL → player bootstrap → Simplecast UUID → validated audio
      reaches 0.55 + 0.20 = 0.75 ≥ 0.70 ("high") even when the final
      Simplecast URL does not itself contain the numeric NPR IDs.
    - +0.35 when only one of the IDs appears in the source endpoint URL.
    - +0.45 when the validated final URL includes a target ID (strong signal
      for legacy ondemand.npr.org era where ``?e=<story_id>`` is in the URL).
    - +0.20 when the validated URL exposes a Simplecast episode UUID.

    Generic Simplecast UUIDs sourced from non-episode-specific pages score 0
    on the endpoint contribution and therefore never reach "high" confidence,
    which is the minimum required for RECOVERED_AND_VALIDATED.  This is the
    primary safety guard against false positives from generic page candidates.
    (Those generic sources have also been removed from the endpoint matrix.)
    """
    evidence = []
    confidence = "low"
    score = 0.0
    for endpoint in source_endpoints:
        if target.story_id in endpoint and target.audio_id in endpoint:
            evidence.append(f"candidate discovered via exact target player embed URL (both IDs): {endpoint}")
            score += 0.55
        elif target.story_id in endpoint or target.audio_id in endpoint:
            evidence.append(f"candidate discovered via endpoint containing target ID: {endpoint}")
            score += 0.35
    if validated_audio and validated_audio.get("final_url"):
        final_url = validated_audio["final_url"]
        if target.story_id in final_url or target.audio_id in final_url:
            evidence.append("validated final URL includes target story/audio ID")
            score += 0.45
        if validated_audio.get("simplecast_uuid"):
            evidence.append("validated URL exposes Simplecast episode UUID")
            score += 0.2
    if score >= 0.7:
        confidence = "high"
    elif score >= 0.35:
        confidence = "medium"
    return {"score": round(score, 2), "confidence": confidence, "evidence": evidence}


def normalize_audio_identity(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return f"{parsed.netloc.lower()}{parsed.path}".rstrip("/")


def detect_duplicate_underlying_audio(
    validated_audio: dict | None,
    resolved_corpus: list[dict],
) -> dict:
    """Check validated audio against the full resolved corpus (not just same-day).

    Comparing against the full corpus catches cross-date duplicates, which is
    required for the Four Two-Indicators targets whose counterpart episodes may
    have different air dates.
    """
    if not validated_audio or not validated_audio.get("playable"):
        return {"is_duplicate": False, "matched_story_id": None, "reason": None}
    candidate_identity = normalize_audio_identity(validated_audio.get("final_url") or validated_audio.get("candidate_url"))
    candidate_uuid = validated_audio.get("simplecast_uuid")
    for item in resolved_corpus:
        existing_url = item.get("final_url") or item.get("enclosure_url")
        if candidate_identity and candidate_identity == normalize_audio_identity(existing_url):
            return {
                "is_duplicate": True,
                "matched_story_id": item.get("story_id"),
                "reason": "matching normalized final audio URL",
            }
        existing_uuid = extract_simplecast_uuid(existing_url)
        if candidate_uuid and existing_uuid and candidate_uuid == existing_uuid:
            return {
                "is_duplicate": True,
                "matched_story_id": item.get("story_id"),
                "reason": "matching Simplecast episode UUID",
            }
    return {"is_duplicate": False, "matched_story_id": None, "reason": None}


def classify_probe_outcome(
    endpoint_attempts: list[dict],
    candidate_urls: list[str],
    validated_audio: dict | None,
) -> str:
    success_count = sum(1 for item in endpoint_attempts if item.get("ok"))
    if success_count == 0:
        return "network_failed_all"
    if not candidate_urls:
        return "no_candidate_found"
    if validated_audio and validated_audio.get("playable"):
        return "validated_audio_found"
    return "candidates_not_playable"


def classify_target(
    target: Target,
    baseline: str,
    validated_audio: dict | None,
    duplicate_result: dict,
    provenance: dict | None,
) -> str:
    """Classify the final recovery status for a target.

    Recovery rules:
    1. Duplicate/alternate of existing RSS item — highest priority, always wins.
    2. Two-Indicators targets: never independently recovered unless a duplicate
       match is found.  They remain PROBABLY_NOT_SEPARATE_EPISODE unless a
       corpus-wide duplicate match says otherwise.
    3. Regular targets: RECOVERED_AND_VALIDATED only when both conditions hold:
       a. validated_audio is playable;
       b. provenance confidence is "high" (score >= 0.7, episode-specific chain).
       Weak/ambiguous provenance stays unresolved.
    """
    if duplicate_result.get("is_duplicate"):
        return "DUPLICATE_OR_ALTERNATE_OF_EXISTING_RSS_ITEM"
    # Two-Indicators targets cannot be independently recovered via the normal path.
    if target.story_id in TWO_INDICATORS_STORY_IDS:
        return "PROBABLY_NOT_SEPARATE_EPISODE"
    if (
        validated_audio
        and validated_audio.get("playable")
        and provenance
        and provenance.get("confidence") == MINIMUM_PROVENANCE_CONFIDENCE
    ):
        return "RECOVERED_AND_VALIDATED"
    return baseline


def rank_candidates(
    candidate_urls: list[str],
    target: Target,
    source_endpoints_by_candidate: dict[str, list[str]],
) -> list[str]:
    """Return candidates ordered by descending provenance strength.

    Candidates whose source endpoint URL contains the target story_id or audio_id
    are ranked first (stronger provenance), followed by remaining candidates.
    Deduplication by normalized URL identity is applied before ranking.
    """
    seen_identities: set[str | None] = set()
    unique: list[str] = []
    for url in candidate_urls:
        identity = normalize_audio_identity(url)
        if identity not in seen_identities:
            seen_identities.add(identity)
            unique.append(url)

    def _score(url: str) -> int:
        endpoints = source_endpoints_by_candidate.get(url, [])
        return sum(
            1
            for ep in endpoints
            if target.story_id in ep or target.audio_id in ep
        )

    return sorted(unique, key=_score, reverse=True)


def investigate_target(
    target: Target,
    history_item: dict,
    resolved_corpus: list[dict],
    output_dir: Path | None = None,
) -> dict:
    baseline = baseline_classification(target.story_id)

    # Two Indicators targets require no network probing; exit immediately.
    if target.story_id in TWO_INDICATORS_STORY_IDS:
        result: dict = {
            "date": target.date,
            "title": target.title,
            "story_id": target.story_id,
            "audio_id": target.audio_id,
            "baseline_classification": baseline,
            "history_description": history_item.get("description", ""),
            "endpoint_attempts": [],
            "request_counts": {"success": 0, "failed": 0},
            "candidate_audio_urls_discovered": [],
            "candidate_audio_urls_deduplicated": [],
            "candidate_audio_urls_deduplicated_count": 0,
            "candidate_audio_urls_selected": [],
            "candidate_audio_urls_skipped_due_to_cap": [],
            "validated_candidates": [],
            "validated_audio_url": None,
            "final_redirected_url": None,
            "http_status": None,
            "content_type": None,
            "content_length": None,
            "simplecast_uuid": None,
            "identity_provenance_evidence": [],
            "provenance_score": 0.0,
            "provenance_confidence": "low",
            "final_classification": "PROBABLY_NOT_SEPARATE_EPISODE",
            "duplicate_check": None,
            "probe_outcome": "skipped_two_indicators",
            "recommended_production_action": "do_not_modify_production_files_yet",
        }
        if output_dir is not None:
            checkpoint_path = output_dir / f"checkpoint_{target.story_id}.json"
            write_json(checkpoint_path, result)
        return result

    endpoint_attempts = []
    candidate_urls: list[str] = []
    source_endpoints_by_candidate: dict[str, list[str]] = {}

    for endpoint in build_endpoint_matrix(target):
        response = request_with_retries(url=endpoint["url"], method="GET")
        response["endpoint"] = endpoint["endpoint"]
        response["url"] = endpoint["url"]
        endpoint_attempts.append(response)
        if not response.get("ok"):
            continue
        text = response.get("text", "")
        discovered = extract_candidate_audio_urls(text)
        for candidate in discovered:
            if candidate not in candidate_urls:
                candidate_urls.append(candidate)
            source_endpoints_by_candidate.setdefault(candidate, [])
            source_endpoints_by_candidate[candidate].append(endpoint["url"])

    # --- Wayback archive fetch step ---
    # After the endpoint loop, find the CDX result for the player embed URL,
    # select up to WAYBACK_MAX_CAPTURES closest captures, fetch each archived
    # page, and extract additional candidates.  Using the id_ modifier returns
    # the raw archived page without Wayback toolbar injection.  The archive URL
    # itself contains both story_id and audio_id, so any candidate found via
    # this path receives dual-ID provenance scoring.
    cdx_attempt = next(
        (a for a in endpoint_attempts if a.get("endpoint") == "wayback_cdx_player" and a.get("ok")),
        None,
    )
    if cdx_attempt:
        player_url = f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"
        captures = select_wayback_captures(cdx_attempt.get("text", ""), target.date)
        for capture in captures:
            archive_url = f"https://web.archive.org/web/{capture['timestamp']}id_/{player_url}"
            archive_response = request_with_retries(url=archive_url, method="GET")
            archive_response["endpoint"] = "wayback_player_fetch"
            archive_response["url"] = archive_url
            archive_response["wayback_timestamp"] = capture["timestamp"]
            endpoint_attempts.append(archive_response)
            if not archive_response.get("ok"):
                continue
            discovered = extract_candidate_audio_urls(archive_response.get("text", ""))
            for candidate in discovered:
                if candidate not in candidate_urls:
                    candidate_urls.append(candidate)
                source_endpoints_by_candidate.setdefault(candidate, [])
                source_endpoints_by_candidate[candidate].append(archive_url)

    # Rank by provenance and apply hard cap before validation.
    ranked = rank_candidates(candidate_urls, target, source_endpoints_by_candidate)
    selected = ranked[:MAX_CANDIDATES_PER_TARGET]
    skipped = ranked[MAX_CANDIDATES_PER_TARGET:]

    validated_candidates = [validate_audio_candidate(url) for url in selected]
    playable = [item for item in validated_candidates if item.get("playable")]
    validated_audio = playable[0] if playable else None
    validated_candidate_url = validated_audio.get("candidate_url") if validated_audio else None
    source_evidence = source_endpoints_by_candidate.get(validated_candidate_url, []) if validated_candidate_url else []
    provenance = compute_identity_provenance(target, source_evidence, validated_audio)
    duplicate_result = detect_duplicate_underlying_audio(
        validated_audio=validated_audio,
        resolved_corpus=resolved_corpus,
    )
    final_classification = classify_target(
        target=target,
        baseline=baseline,
        validated_audio=validated_audio,
        duplicate_result=duplicate_result,
        provenance=provenance,
    )
    probe_outcome = classify_probe_outcome(
        endpoint_attempts=endpoint_attempts,
        candidate_urls=candidate_urls,
        validated_audio=validated_audio,
    )
    result = {
        "date": target.date,
        "title": target.title,
        "story_id": target.story_id,
        "audio_id": target.audio_id,
        "baseline_classification": baseline,
        "history_description": history_item.get("description", ""),
        "endpoint_attempts": endpoint_attempts,
        "request_counts": {
            "success": sum(1 for item in endpoint_attempts if item.get("ok")),
            "failed": sum(1 for item in endpoint_attempts if not item.get("ok")),
        },
        "candidate_audio_urls_discovered": candidate_urls,
        "candidate_audio_urls_deduplicated": ranked,
        "candidate_audio_urls_deduplicated_count": len(ranked),
        "candidate_audio_urls_selected": selected,
        "candidate_audio_urls_skipped_due_to_cap": skipped,
        "validated_candidates": validated_candidates,
        "validated_audio_url": validated_audio.get("candidate_url") if validated_audio else None,
        "final_redirected_url": validated_audio.get("final_url") if validated_audio else None,
        "http_status": validated_audio.get("http_status") if validated_audio else None,
        "content_type": validated_audio.get("content_type") if validated_audio else None,
        "content_length": validated_audio.get("content_length") if validated_audio else None,
        "simplecast_uuid": validated_audio.get("simplecast_uuid") if validated_audio else None,
        "identity_provenance_evidence": provenance.get("evidence", []),
        "provenance_score": provenance.get("score"),
        "provenance_confidence": provenance.get("confidence", "low"),
        "final_classification": final_classification,
        "duplicate_check": duplicate_result,
        "probe_outcome": probe_outcome,
        "recommended_production_action": (
            "add_validated_enclosure"
            if final_classification == "RECOVERED_AND_VALIDATED"
            else "do_not_modify_production_files_yet"
        ),
    }
    # Atomic per-target checkpoint so an interrupted run isn't fully lost.
    if output_dir is not None:
        checkpoint_path = output_dir / f"checkpoint_{target.story_id}.json"
        write_json(checkpoint_path, result)
    return result


def summarize(results: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for result in results:
        key = result["final_classification"]
        counts[key] = counts.get(key, 0) + 1
    return counts


def run(output_dir: Path, require_21: bool = True, batch: int | None = None, batch_size: int = 5) -> dict:
    pre_hashes = capture_file_hashes(PRODUCTION_FILES)
    enclosure = load_json(ENCLOSURE_MAP)
    episodes = enclosure.get("episodes", {})
    targets = load_no_audio_targets(ENCLOSURE_MAP)
    if require_21 and len(targets) != 21:
        raise RuntimeError(f"Expected 21 no_audio targets, found {len(targets)}")

    # Build full resolved corpus for cross-date duplicate detection.
    resolved_corpus: list[dict] = [
        item for item in episodes.values() if item.get("status") == "resolved"
    ]

    history_index = build_history_index(HISTORY_FILE)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Apply batch selection; fall back to all targets when batch is omitted.
    active_targets = select_batch_targets(targets, batch, batch_size) if batch is not None else targets
    effective_batch = batch if batch is not None else 1
    effective_batch_size = batch_size if batch is not None else len(targets)

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    # Write placeholder immediately so a workflow timeout doesn't look like a
    # run that never started.
    write_json(
        output_dir / "no_audio_target_recovery_placeholder.json",
        {
            "note": "Placeholder artifact file for branch commits from workflow runs.",
            "generated_by": "scripts/recovery/recover_no_audio_targets.py",
            "generated_at": generated_at,
            "batch": effective_batch,
            "run_complete": False,
        },
    )

    # Skip targets whose checkpoint already exists (idempotent re-runs).
    to_process, completed_previously = partition_by_completion(active_targets, output_dir)

    results = []
    for target in to_process:
        history_item = history_index.get(target.story_id, {})
        results.append(
            investigate_target(target, history_item, resolved_corpus, output_dir=output_dir)
        )

    post_hashes = capture_file_hashes(PRODUCTION_FILES)
    # Hard assertion: abort loudly if any production file was mutated.
    assert_production_files_unchanged(pre_hashes, post_hashes)

    # Derive endpoint count from the matrix itself so the budget stays correct
    # if endpoints are ever added or removed.  Use the first active_target so the
    # count is consistent with the batch context; fall back to the full target
    # list only if the batch is empty (all already completed).
    _representative_target = (active_targets or targets)[0]
    _endpoint_count = len(build_endpoint_matrix(_representative_target))

    # Build classification and probe-outcome tallies over newly processed results.
    classification_counts: dict[str, int] = {}
    probe_outcome_counts: dict[str, int] = {}
    for result in results:
        k = result.get("final_classification", "unknown")
        classification_counts[k] = classification_counts.get(k, 0) + 1
        pk = result.get("probe_outcome", "unknown")
        probe_outcome_counts[pk] = probe_outcome_counts.get(pk, 0) + 1

    payload = {
        "generated_at": generated_at,
        "run_complete": True,
        "batch": effective_batch,
        "batch_size": effective_batch_size,
        "total_targets_in_corpus": len(targets),
        "batch_target_count": len(active_targets),
        "completed_previously_count": len(completed_previously),
        "completed_previously_story_ids": [t.story_id for t in completed_previously],
        "processed_this_run_count": len(results),
        # Kept for backward compatibility.
        "target_count": len(results),
        "completed_target_count": len(results),
        "classifications": classification_counts,
        "probe_outcomes": probe_outcome_counts,
        # Human-readable summary that distinguishes all required categories.
        # Note: unfinished_in_batch is always 0 in a completed run; a timed-out
        # run produces no summary JSON, so this field never misrepresents
        # cancelled targets as negative recovery results.
        "summary": {
            "completed_previously": len(completed_previously),
            "processed_this_run": len(results),
            "recovered": classification_counts.get("RECOVERED_AND_VALIDATED", 0),
            "duplicate_or_alternate": classification_counts.get("DUPLICATE_OR_ALTERNATE_OF_EXISTING_RSS_ITEM", 0),
            "probably_not_separate": classification_counts.get("PROBABLY_NOT_SEPARATE_EPISODE", 0),
            "unresolved": classification_counts.get("CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED", 0),
            "network_or_timeout": probe_outcome_counts.get("network_failed_all", 0),
        },
        "results": results,
        "production_files_changed": False,
        "request_budget": {
            "endpoints_per_target": _endpoint_count,
            "max_retries_per_endpoint": MAX_RETRIES,
            "max_endpoint_attempts_per_target": _endpoint_count * MAX_RETRIES,
            "wayback_archive_fetches_per_target": WAYBACK_MAX_CAPTURES,
            "max_retries_per_wayback_fetch": MAX_RETRIES,
            "max_wayback_archive_attempts_per_target": WAYBACK_MAX_CAPTURES * MAX_RETRIES,
            "candidate_cap_per_target": MAX_CANDIDATES_PER_TARGET,
            "max_requests_per_candidate": 2 * MAX_RETRIES,
            "max_candidate_attempts_per_target": MAX_CANDIDATES_PER_TARGET * 2 * MAX_RETRIES,
            "max_requests_per_target": (
                _endpoint_count * MAX_RETRIES
                + WAYBACK_MAX_CAPTURES * MAX_RETRIES
                + MAX_CANDIDATES_PER_TARGET * 2 * MAX_RETRIES
            ),
            "max_requests_17_targets": 17 * (
                _endpoint_count * MAX_RETRIES
                + WAYBACK_MAX_CAPTURES * MAX_RETRIES
                + MAX_CANDIDATES_PER_TARGET * 2 * MAX_RETRIES
            ),
        },
    }
    # Overwrite placeholder with completed run marker.
    write_json(
        output_dir / "no_audio_target_recovery_placeholder.json",
        {
            "note": "Placeholder artifact file for branch commits from workflow runs.",
            "generated_by": "scripts/recovery/recover_no_audio_targets.py",
            "generated_at": generated_at,
            "batch": effective_batch,
            "run_complete": True,
        },
    )
    # Write a batch-specific summary as well as the generic one for back-compat.
    if batch is not None:
        write_json(output_dir / f"no_audio_target_recovery_batch{batch}_summary.json", payload)
    write_json(output_dir / "no_audio_target_recovery_summary.json", payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover current no_audio NPR targets with network-enabled probing.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR_DEFAULT),
        help="Directory for JSON artifacts.",
    )
    parser.add_argument(
        "--allow-non-21",
        action="store_true",
        help="Skip strict check that exactly 21 no_audio targets exist.",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        metavar="N",
        help=(
            "1-based batch number.  Selects targets[(N-1)*batch_size : N*batch_size] "
            "from the deterministic sorted order.  Omit to process all targets."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        metavar="K",
        help="Number of targets per batch (default: 5).  ceil(21/5) = 5 runs for full coverage.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = run(
        output_dir=Path(args.output_dir),
        require_21=not args.allow_non_21,
        batch=args.batch,
        batch_size=args.batch_size,
    )
    print(f"batch={payload['batch']} targets_processed={payload['target_count']} skipped={payload['completed_previously_count']}")
    print(json.dumps(payload["classifications"], sort_keys=True))


if __name__ == "__main__":
    main()
