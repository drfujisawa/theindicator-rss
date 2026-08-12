#!/usr/bin/env python3
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

TIMEOUT_SECONDS = 25
MAX_RETRIES = 3
BACKOFF_SECONDS = 0.8
MAX_TEXT_BYTES = 400_000

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


def build_endpoint_matrix(target: Target) -> list[dict]:
    year, month, day = target.date.split("-")
    story_url = target.npr_url
    player_url = f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"
    section_url = f"https://www.npr.org/sections/money/{year}/{month}/{day}/"
    encoded_story = quote(story_url, safe="")
    encoded_player = quote(player_url, safe="")
    encoded_section = quote(section_url, safe="")
    return [
        {"endpoint": "story_url", "url": story_url},
        {"endpoint": "player_embed", "url": player_url},
        {
            "endpoint": "story_template",
            "url": f"https://www.npr.org/templates/story/story.php?storyId={target.story_id}",
        },
        {"endpoint": "transcript", "url": f"https://www.npr.org/transcripts/{target.story_id}"},
        {
            "endpoint": "legacy_api_story",
            "url": f"https://api.npr.org/query?id={target.story_id}&output=JSON",
        },
        {
            "endpoint": "legacy_api_audio",
            "url": f"https://api.npr.org/query?id={target.audio_id}&output=JSON",
        },
        {"endpoint": "money_section_date", "url": section_url},
        {
            "endpoint": "wayback_cdx_story",
            "url": (
                "https://web.archive.org/cdx/search/cdx"
                f"?url={encoded_story}&output=json&fl=timestamp,original,statuscode,mimetype"
            ),
        },
        {
            "endpoint": "wayback_cdx_player",
            "url": (
                "https://web.archive.org/cdx/search/cdx"
                f"?url={encoded_player}&output=json&fl=timestamp,original,statuscode,mimetype"
            ),
        },
        {
            "endpoint": "wayback_cdx_section",
            "url": (
                "https://web.archive.org/cdx/search/cdx"
                f"?url={encoded_section}&output=json&fl=timestamp,original,statuscode,mimetype"
            ),
        },
        {
            "endpoint": "wayback_cdx_story_id",
            "url": (
                "https://web.archive.org/cdx/search/cdx"
                f"?url=https://www.npr.org/*{target.story_id}*&output=json"
                "&fl=timestamp,original,statuscode,mimetype"
            ),
        },
        {
            "endpoint": "wayback_cdx_audio_id",
            "url": (
                "https://web.archive.org/cdx/search/cdx"
                f"?url=https://www.npr.org/*{target.audio_id}*&output=json"
                "&fl=timestamp,original,statuscode,mimetype"
            ),
        },
        {"endpoint": "npr_feed", "url": "https://feeds.npr.org/510325/podcast.xml"},
        {
            "endpoint": "npr_show_page",
            "url": "https://www.npr.org/podcasts/510325/the-indicator-from-planet-money",
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
    if not text:
        return []
    pattern = re.compile(
        r"https?://[^\"'\s<>]+(?:\.mp3|/audio(?:[/?#]|$)|simplecastaudio\.com[^\"'\s<>]*)",
        re.IGNORECASE,
    )
    seen = []
    for match in pattern.findall(text):
        cleaned = match.replace("\\/", "/")
        if cleaned not in seen:
            seen.append(cleaned)
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
    evidence = []
    confidence = "low"
    score = 0.0
    for endpoint in source_endpoints:
        if target.story_id in endpoint or target.audio_id in endpoint:
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
    same_day_resolved: list[dict],
) -> dict:
    if not validated_audio or not validated_audio.get("playable"):
        return {"is_duplicate": False, "matched_story_id": None, "reason": None}
    candidate_identity = normalize_audio_identity(validated_audio.get("final_url") or validated_audio.get("candidate_url"))
    candidate_uuid = validated_audio.get("simplecast_uuid")
    for item in same_day_resolved:
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
) -> str:
    if duplicate_result.get("is_duplicate"):
        return "DUPLICATE_OR_ALTERNATE_OF_EXISTING_RSS_ITEM"
    if validated_audio and validated_audio.get("playable"):
        return "RECOVERED_AND_VALIDATED"
    return baseline


def investigate_target(
    target: Target,
    history_item: dict,
    resolved_by_date: dict[str, list[dict]],
) -> dict:
    baseline = baseline_classification(target.story_id)
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

    validated_candidates = [validate_audio_candidate(url) for url in candidate_urls]
    playable = [item for item in validated_candidates if item.get("playable")]
    validated_audio = playable[0] if playable else None
    validated_candidate_url = validated_audio.get("candidate_url") if validated_audio else None
    source_evidence = source_endpoints_by_candidate.get(validated_candidate_url, []) if validated_candidate_url else []
    provenance = compute_identity_provenance(target, source_evidence, validated_audio)
    duplicate_result = detect_duplicate_underlying_audio(
        validated_audio=validated_audio,
        same_day_resolved=resolved_by_date.get(target.date, []),
    )
    final_classification = classify_target(
        target=target,
        baseline=baseline,
        validated_audio=validated_audio,
        duplicate_result=duplicate_result,
    )
    probe_outcome = classify_probe_outcome(
        endpoint_attempts=endpoint_attempts,
        candidate_urls=candidate_urls,
        validated_audio=validated_audio,
    )
    return {
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
        "candidate_audio_urls": candidate_urls,
        "validated_candidates": validated_candidates,
        "validated_audio_url": validated_audio.get("candidate_url") if validated_audio else None,
        "final_redirected_url": validated_audio.get("final_url") if validated_audio else None,
        "http_status": validated_audio.get("http_status") if validated_audio else None,
        "content_type": validated_audio.get("content_type") if validated_audio else None,
        "content_length": validated_audio.get("content_length") if validated_audio else None,
        "simplecast_uuid": validated_audio.get("simplecast_uuid") if validated_audio else None,
        "identity_provenance_evidence": provenance.get("evidence", []),
        "provenance_score": provenance.get("score"),
        "final_classification": final_classification,
        "confidence": provenance.get("confidence", "low"),
        "duplicate_check": duplicate_result,
        "probe_outcome": probe_outcome,
        "recommended_production_action": (
            "add_validated_enclosure"
            if final_classification == "RECOVERED_AND_VALIDATED"
            else "do_not_modify_production_files_yet"
        ),
    }


def summarize(results: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for result in results:
        key = result["final_classification"]
        counts[key] = counts.get(key, 0) + 1
    return counts


def run(output_dir: Path, require_21: bool = True) -> dict:
    pre_hashes = capture_file_hashes(PRODUCTION_FILES)
    enclosure = load_json(ENCLOSURE_MAP)
    episodes = enclosure.get("episodes", {})
    targets = load_no_audio_targets(ENCLOSURE_MAP)
    if require_21 and len(targets) != 21:
        raise RuntimeError(f"Expected 21 no_audio targets, found {len(targets)}")

    resolved_by_date: dict[str, list[dict]] = {}
    for item in episodes.values():
        if item.get("status") != "resolved":
            continue
        resolved_by_date.setdefault(str(item.get("date", "")), []).append(item)

    history_index = build_history_index(HISTORY_FILE)
    results = []
    for target in targets:
        history_item = history_index.get(target.story_id, {})
        results.append(investigate_target(target, history_item, resolved_by_date))

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = {
        "generated_at": generated_at,
        "target_count": len(results),
        "classifications": summarize(results),
        "results": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        output_dir / "no_audio_target_recovery_placeholder.json",
        {
            "note": "Placeholder artifact file for branch commits from workflow runs.",
            "generated_by": "scripts/recovery/recover_no_audio_targets.py",
            "generated_at": generated_at,
        },
    )
    post_hashes = capture_file_hashes(PRODUCTION_FILES)
    payload["production_files_changed"] = production_files_changed(pre_hashes, post_hashes)
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = run(
        output_dir=Path(args.output_dir),
        require_21=not args.allow_non_21,
    )
    print(f"targets={payload['target_count']}")
    print(json.dumps(payload["classifications"], sort_keys=True))


if __name__ == "__main__":
    main()
