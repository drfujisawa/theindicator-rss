#!/usr/bin/env python3
"""Resolve the eight near-ready early episodes without mutating production."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "data/audits/indicator_early_promotion_manifest.json"
IDENTITIES = REPO_ROOT / "data/recovery/indicator_npr_identities.json"
AUDIO_RECOVERY = REPO_ROOT / "data/recovery/indicator_npr_audio_recovery.json"
AUDIO_VALIDATION = REPO_ROOT / "data/recovery/indicator_npr_audio_validation.json"
HISTORY = REPO_ROOT / "indicator_history.json"
ENCLOSURE_MAP = REPO_ROOT / "indicator_enclosure_map.json"
OUTPUT = REPO_ROOT / "data/audits/indicator_early_near_ready_audit.json"

SCOPED_CATEGORIES = {"identity_review_candidate", "special_recovery_review"}
PLAYER_RE = re.compile(r"/player/embed/(\d+)/(\d+)")
REBROADCAST_RE = re.compile(r"(?i)(rebroadcast|rerun)")

# Human review notes record why the automated identity threshold was too strict.
# Every conclusion below is also checked against machine-readable evidence.
REVIEW_RESOLUTIONS = {
    "614193817": "The exact-date NPR page exposes a unique Indicator player and a date-matched FinCEN audio file; the differing NPR slug is an editorial alternate title.",
    "643056045": "The exact-date NPR slug matches after removing the catalog's rebroadcast label, and both the catalog and audio filename explicitly identify a rerun.",
    "643423980": "The exact-date NPR slug matches after removing the catalog's rebroadcast label, and both the catalog and audio filename explicitly identify a rerun.",
    "644961856": "The NPR slug is malformed, but the exact-date page exposes a unique Indicator player and the audio filename contains both the date and naftasplainer.",
    "673840278": "The exact-date NPR page exposes a unique Indicator player; Macron/yellow-vests is consistent with the catalog title Paris Is Burning.",
    "685292915": "The NPR title is an exact match and its one-day URL-date offset is corroborated by the 011419 production date embedded in the audio filename.",
    "662708285": "The archived NPR story page embeds the same player IDs and a direct NPR-hosted audio URL recorded by the special recovery.",
    "716132270": "The exact-title NPR identity and player IDs are corroborated by a WBUR page that resolves to the recorded NPR-hosted audio URL.",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def records(payload: dict) -> list[dict]:
    for key in ("episodes", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    partitioned = [
        record
        for key in ("validated_audio", "needs_review", "failed")
        for record in payload.get(key, [])
    ]
    if partitioned:
        return partitioned
    raise ValueError("unsupported record payload")


def by_story(payload: dict) -> dict[str, dict]:
    return {
        str(record["npr_story_id"]): record
        for record in records(payload)
        if record.get("npr_story_id")
    }


def canonical_audio_path(url: str | None) -> str | None:
    return urlparse(url or "").path.lower() or None


def query_int(url: str | None, name: str) -> int | None:
    value = parse_qs(urlparse(url or "").query).get(name, [None])[0]
    return int(value) if value and value.isdigit() else None


def observed_player_pairs(record: dict) -> list[dict]:
    pairs = set()
    for candidate in record.get("audio_candidates", []):
        match = PLAYER_RE.search(candidate.get("url", ""))
        if match:
            pairs.add(match.groups())
    return [
        {"player_story_id": pair[0], "audio_id": pair[1]}
        for pair in sorted(pairs)
    ]


def build_audit(repo_root: Path = REPO_ROOT) -> dict:
    manifest = load(repo_root / MANIFEST.relative_to(REPO_ROOT))
    identities = by_story(load(repo_root / IDENTITIES.relative_to(REPO_ROOT)))
    recovery = by_story(load(repo_root / AUDIO_RECOVERY.relative_to(REPO_ROOT)))
    validation = by_story(load(repo_root / AUDIO_VALIDATION.relative_to(REPO_ROOT)))
    history = load(repo_root / HISTORY.relative_to(REPO_ROOT))["episodes"]
    enclosure = load(repo_root / ENCLOSURE_MAP.relative_to(REPO_ROOT))["episodes"]

    history_story_ids = {str(item.get("story_id")) for item in history}
    production_audio_paths = {
        canonical_audio_path(item.get("final_url") or item.get("enclosure_url"))
        for item in enclosure.values()
    }
    production_audio_paths.discard(None)

    scoped = [
        item for item in manifest["episodes"]
        if item.get("category") in SCOPED_CATEGORIES
    ]
    reviewed = []
    for item in scoped:
        story_id = str(item["npr_story_id"])
        identity = identities[story_id]
        audio_record = recovery[story_id]
        audio_url = item["validated_final_audio_url"]
        path = canonical_audio_path(audio_url)
        pairs = observed_player_pairs(audio_record)
        e_parameter = query_int(audio_url, "e")
        unique_player = len(pairs) == 1
        e_matches_player = bool(
            e_parameter and unique_player
            and str(e_parameter) == pairs[0]["player_story_id"]
        )
        direct_validation = validation.get(story_id, {}).get("validation_status")
        special = item["category"] == "special_recovery_review"
        audio_supported = (
            direct_validation == "validated_audio"
            or (special and audio_url.startswith("https://ondemand.npr.org/"))
        )
        absent = story_id not in history_story_ids and story_id not in enclosure
        audio_unique = path not in production_audio_paths
        identity_supported = bool(
            identity.get("selected_date_match")
            or identity.get("selected_title_score") == 1.0
        )
        ready = all((unique_player, audio_supported, absent, audio_unique, identity_supported))
        applied = not absent and not audio_unique
        rebroadcast = bool(
            REBROADCAST_RE.search(item["reference_title"])
            or REBROADCAST_RE.search(path or "")
        )
        reviewed.append({
            "reference_date": item["reference_date"],
            "reference_title": item["reference_title"],
            "npr_story_id": story_id,
            "npr_url": item["npr_url"],
            "prior_category": item["category"],
            "review_outcome": (
                "already_in_production" if applied
                else "ready_with_limited_metadata" if ready
                else "manual_review"
            ),
            "release_classification": (
                "confirmed_indicator_rebroadcast" if rebroadcast
                else "confirmed_indicator_episode"
            ),
            "review_resolution": REVIEW_RESOLUTIONS[story_id],
            "evidence": {
                "reference_date": item["reference_date"],
                "npr_url_date": identity.get("selected_url_date"),
                "url_date_matches_reference": identity.get("selected_date_match"),
                "npr_slug": identity.get("selected_slug"),
                "title_score": identity.get("selected_title_score"),
                "identity_score": identity.get("identity_score"),
                "observed_player_pairs": pairs,
                "single_observed_player_pair": unique_player,
                "audio_e_parameter": e_parameter,
                "audio_e_matches_player_story_id": e_matches_player,
                "audio_url": audio_url,
                "duration_seconds": query_int(audio_url, "d"),
                "declared_file_size_bytes": query_int(audio_url, "size"),
                "direct_audio_validation_status": direct_validation,
                "special_recovery_source": item.get("special_recovery_source"),
                "audio_identity_supported": audio_supported,
                "story_absent_from_production": absent,
                "audio_path_unique_in_production": audio_unique,
            },
            "metadata_limits": [
                "exact_publication_timestamp_unavailable",
                "npr_description_not_preserved",
                *([] if query_int(audio_url, "size") else ["verified_full_content_length_unavailable"]),
            ],
            "provenance": sorted(set(item["provenance"] + [
                "indicator_npr_identities.json",
                "indicator_npr_audio_recovery.json",
                "indicator_npr_audio_validation.json" if not special
                else "indicator_completeness_audit.json:special_recoveries",
            ])),
        })

    outcomes = Counter(item["review_outcome"] for item in reviewed)
    releases = Counter(item["release_classification"] for item in reviewed)
    return {
        "audit_version": 1,
        "mode": "read_only",
        "production_files_modified": False,
        "scope": sorted(SCOPED_CATEGORIES),
        "summary": {
            "candidates_reviewed": len(reviewed),
            "outcomes": dict(sorted(outcomes.items())),
            "release_classifications": dict(sorted(releases.items())),
            "recommended_next_action": (
                "no further production action; verify deployment"
                if outcomes.get("already_in_production") == len(reviewed)
                else "stage these eight records and run feed invariants before any production write"
            ),
        },
        "episodes": reviewed,
    }


def main() -> None:
    audit = build_audit()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit["summary"], indent=2))


if __name__ == "__main__":
    main()
