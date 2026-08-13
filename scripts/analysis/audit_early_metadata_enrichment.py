#!/usr/bin/env python3
"""Enrich strong early-history candidates and assign conservative review tiers."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.audit_early_promotion_candidates import (  # noqa: E402
    OUTPUT as MANIFEST,
    episode_key,
    load_json,
)


AUDIO_RECOVERY = (
    REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_recovery.json"
)
AFFILIATE_RECOVERY = (
    REPO_ROOT / "data" / "recovery" / "indicator_recovered_episodes.json"
)
OUTPUT = (
    REPO_ROOT / "data" / "audits" / "indicator_early_metadata_enrichment_audit.json"
)

_PLAYER_RE = re.compile(r"/player/embed/(\d+)/(\d+)")
_REBROADCAST_RE = re.compile(r"(?i)(?:rebroadcast|rerun|re[-_ ]?air|_rpt(?:_|\.))")


def normalize_title(title: str) -> str:
    title = re.sub(r"(?i)\b(?:rebroadcast|rerun)\b", "", title)
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def index_by_reference(records: list[dict]) -> dict[tuple[str, str], dict]:
    return {
        episode_key(record.get("reference_date"), record.get("reference_title")):
        record
        for record in records
    }


def player_pairs(record: dict | None) -> list[dict]:
    pairs = set()
    for candidate in (record or {}).get("audio_candidates", []):
        match = _PLAYER_RE.search(candidate.get("url", ""))
        if match:
            pairs.add(match.groups())
    return [
        {
            "player_story_id": story_id,
            "audio_id": audio_id,
            "player_url": f"https://www.npr.org/player/embed/{story_id}/{audio_id}",
            "evidence_status": "directly_observed_on_recovered_npr_page",
        }
        for story_id, audio_id in sorted(pairs)
    ]


def query_int(url: str | None, name: str) -> int | None:
    values = parse_qs(urlparse(url or "").query).get(name, [])
    if not values or not values[0].isdigit():
        return None
    return int(values[0])


def select_player_identity(
    pairs: list[dict], npr_story_id: str, audio_url: str
) -> tuple[dict | None, str]:
    if len(pairs) == 1:
        return pairs[0], "single_observed_player_pair"
    matching_story = [
        pair for pair in pairs if pair["player_story_id"] == npr_story_id
    ]
    audio_e = str(query_int(audio_url, "e") or "")
    if len(matching_story) == 1 and audio_e == npr_story_id:
        return matching_story[0], "page_story_id_and_audio_e_parameter_match"
    return None, "ambiguous"


def build_enrichment_audit(repo_root: Path = REPO_ROOT) -> dict:
    manifest = load_json(repo_root / MANIFEST.relative_to(REPO_ROOT))
    audio_recovery = load_json(repo_root / AUDIO_RECOVERY.relative_to(REPO_ROOT))
    affiliate_recovery = load_json(
        repo_root / AFFILIATE_RECOVERY.relative_to(REPO_ROOT)
    )
    audio_by_key = index_by_reference(audio_recovery.get("results", []))
    affiliate_by_key = index_by_reference(affiliate_recovery.get("recovered", []))

    candidates = [
        record
        for record in manifest["episodes"]
        if record.get("identity_status") == "strong_npr_identity"
        and record.get("validated_final_audio_url")
        and "indicator_npr_audio_validation.json" in record.get("provenance", [])
    ]
    title_groups = defaultdict(list)
    for record in candidates:
        title_groups[normalize_title(record["reference_title"])].append(record)

    enriched = []
    for candidate in candidates:
        key = episode_key(candidate["reference_date"], candidate["reference_title"])
        audio_record = audio_by_key.get(key)
        affiliate_record = affiliate_by_key.get(key)
        pairs = player_pairs(audio_record)
        final_audio = candidate["validated_final_audio_url"]
        path = urlparse(final_audio).path
        same_title = title_groups[normalize_title(candidate["reference_title"])]
        other_same_title = [
            {
                "date": other["reference_date"],
                "title": other["reference_title"],
                "story_id": other["npr_story_id"],
            }
            for other in same_title
            if other["npr_story_id"] != candidate["npr_story_id"]
        ]

        canonical_player, player_selection_method = select_player_identity(
            pairs, candidate["npr_story_id"], final_audio
        )
        flags = []
        if canonical_player is None:
            flags.append("ambiguous_npr_player_pair")
        is_rebroadcast = bool(
            _REBROADCAST_RE.search(candidate["reference_title"])
            or _REBROADCAST_RE.search(path)
        )
        if is_rebroadcast:
            flags.append("confirmed_rebroadcast_release")

        relationship = None
        if other_same_title:
            if is_rebroadcast:
                relationship = "later_rebroadcast_of_same_title_release"
            elif any(
                _REBROADCAST_RE.search(
                    urlparse(other["validated_final_audio_url"]).path
                )
                for other in same_title
                if other["npr_story_id"] != candidate["npr_story_id"]
            ):
                relationship = "original_release_with_later_rebroadcast"
            else:
                relationship = "same_title_distinct_release_evidence"

        # Rebroadcasts are legitimate dated Indicator releases and remain in the
        # historical catalog with an explicit relationship. Only unresolved
        # player identity is a blocking review flag.
        if flags:
            blocking_flags = [flag for flag in flags if flag == "ambiguous_npr_player_pair"]
            tier = "manual_review" if blocking_flags else "ready_with_limited_metadata"
        else:
            tier = "ready_with_limited_metadata"
        enriched.append({
            "reference_date": candidate["reference_date"],
            "reference_title": candidate["reference_title"],
            "npr_story_id": candidate["npr_story_id"],
            "npr_url": candidate["npr_url"],
            "promotion_tier": tier,
            "review_flags": flags,
            "release_classification": (
                "confirmed_indicator_rebroadcast"
                if is_rebroadcast
                else "confirmed_indicator_episode"
            ),
            "same_title_relationship": relationship,
            "verified_metadata": {
                "publication_date": {
                    "value": candidate["reference_date"],
                    "precision": "date_only",
                    "provenance": "NPR URL date plus reference catalog",
                },
                "npr_story_id": {
                    "value": candidate["npr_story_id"],
                    "provenance": "numeric ID in original NPR story URL",
                },
                "audio_url": {
                    "value": final_audio,
                    "provenance": "validated final NPR-hosted audio URL",
                },
                "duration_seconds": {
                    "value": query_int(final_audio, "d"),
                    "provenance": "NPR audio URL d parameter",
                },
                "declared_file_size_bytes": {
                    "value": query_int(final_audio, "size"),
                    "provenance": "NPR audio URL size parameter when present",
                },
                "player_identity": canonical_player,
                "player_identity_selection_method": player_selection_method,
            },
            "observed_player_pairs": pairs,
            "supporting_non_npr_metadata": {
                "affiliate_description": (affiliate_record or {}).get("description"),
                "affiliate_url": (affiliate_record or {}).get("source_url"),
                "use_restriction": (
                    "Supporting discovery evidence only; not represented as an NPR description."
                ),
            },
            "related_same_title_candidates": other_same_title,
            "unavailable_metadata": [
                name
                for name, missing in (
                    ("exact_publication_timestamp", True),
                    ("npr_description", True),
                    ("verified_full_content_length", query_int(final_audio, "size") is None),
                    ("canonical_player_identity", canonical_player is None),
                )
                if missing
            ],
            "provenance": candidate["provenance"] + [
                "indicator_npr_audio_recovery.json",
                "indicator_recovered_episodes.json",
            ],
        })

    tiers = defaultdict(list)
    for record in enriched:
        tiers[record["promotion_tier"]].append(record)

    flag_counts = defaultdict(int)
    for record in enriched:
        for flag in record["review_flags"]:
            flag_counts[flag] += 1

    return {
        "audit_version": 1,
        "mode": "read_only",
        "production_files_modified": False,
        "source_manifest": str(MANIFEST.relative_to(REPO_ROOT)),
        "summary": {
            "strong_candidates_reviewed": len(enriched),
            "ready": 0,
            "ready_with_limited_metadata": len(tiers["ready_with_limited_metadata"]),
            "manual_review": len(tiers["manual_review"]),
            "exclude": 0,
            "unambiguous_player_identity": sum(
                len(record["observed_player_pairs"]) == 1 for record in enriched
            ),
            "selected_player_identity": sum(
                record["verified_metadata"]["player_identity"] is not None
                for record in enriched
            ),
            "duration_available": sum(
                record["verified_metadata"]["duration_seconds"]["value"] is not None
                for record in enriched
            ),
            "declared_file_size_available": sum(
                record["verified_metadata"]["declared_file_size_bytes"]["value"] is not None
                for record in enriched
            ),
            "review_flag_counts": dict(sorted(flag_counts.items())),
        },
        "tier_definitions": {
            "ready": "Complete verified production metadata and no conflict flags.",
            "ready_with_limited_metadata": (
                "Confirmed NPR identity and audio with no conflict flags, but exact time "
                "and/or NPR description remain unavailable."
            ),
            "manual_review": "Unresolved competing-player or identity evidence requires review.",
            "exclude": "Confirmed duplicate, combined item, or unsupported candidate.",
        },
        "planet_money_crosswalk": {
            "status": "limited_external_discovery",
            "relationships": [
                {
                    "indicator_title": "The Measure Of A Tragedy",
                    "indicator_original_date": "2018-06-19",
                    "indicator_rebroadcast_date": "2018-08-27",
                    "planet_money_date": "2018-12-17",
                    "relationship": "Planet Money later republished an Indicator episode",
                    "combined_episode": False,
                    "source_url": "https://www.wbez.org/planet-money/2018/12/17/the-measure-of-a-tragedy",
                    "source_note": "Affiliate page explicitly says the episode is from The Indicator.",
                }
            ],
            "conclusion": (
                "This discovered Planet Money release is a later republication, not evidence "
                "that either dated Indicator release should be replaced by a combined item."
            ),
        },
        "episodes": enriched,
    }


def main() -> None:
    audit = build_enrichment_audit()
    OUTPUT.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")
    print(json.dumps(audit["summary"], indent=2))
    print("Production files were not modified.")


if __name__ == "__main__":
    main()
