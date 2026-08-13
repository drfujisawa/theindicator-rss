#!/usr/bin/env python3
"""Stage the eight reviewed early episodes and validate feed invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.audit_early_near_ready_candidates import (  # noqa: E402
    OUTPUT as AUDIT,
)
from scripts.analysis.audit_early_promotion_candidates import (  # noqa: E402
    feed_story_ids,
    story_id_from_feed_item,
)
from scripts.analysis.build_early_promotion_dry_run import (  # noqa: E402
    build_feed_item,
    feed_timestamp,
    production_hashes,
    write_json,
)

AFFILIATE = REPO_ROOT / "data/recovery/indicator_recovered_episodes.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/early-near-ready-staging"
REPORT = REPO_ROOT / "data/audits/indicator_early_near_ready_staging_report.json"
PRODUCTION_FILES = (
    "indicator_history.json",
    "indicator_enclosure_map.json",
    "theindicator_feed.xml",
)


def affiliate_index(repo_root: Path) -> dict[tuple[str, str], dict]:
    payload = json.loads(
        (repo_root / AFFILIATE.relative_to(REPO_ROOT)).read_text(encoding="utf-8")
    )
    return {
        (item["reference_date"], item["reference_title"]): item
        for item in payload["recovered"]
    }


def candidate_for_build(item: dict, affiliate: dict | None) -> dict:
    evidence = item["evidence"]
    player = evidence["observed_player_pairs"][0]
    return {
        "reference_date": item["reference_date"],
        "reference_title": item["reference_title"],
        "npr_story_id": item["npr_story_id"],
        "npr_url": item["npr_url"],
        "validated_final_audio_url": evidence["audio_url"],
        "verified_player_identity": {
            **player,
            "player_url": (
                "https://www.npr.org/player/embed/"
                f"{player['player_story_id']}/{player['audio_id']}"
            ),
        },
        "supporting_non_npr_metadata": {
            "affiliate_description": (affiliate or {}).get("description"),
            "affiliate_url": (affiliate or {}).get("source_url"),
        },
        "duration_seconds": evidence["duration_seconds"],
        "declared_file_size_bytes": evidence["declared_file_size_bytes"],
        "observed_player_ids": [player["player_story_id"], player["audio_id"]],
        "audio_url_e_parameter": evidence["audio_e_parameter"],
        "release_classification": item["release_classification"],
        "same_title_relationship": None,
        "provenance": item["provenance"] + [
            "indicator_early_near_ready_audit.json"
        ],
    }


def history_record(candidate: dict) -> dict:
    player = candidate["verified_player_identity"]
    description = candidate["supporting_non_npr_metadata"]["affiliate_description"]
    return {
        "title": candidate["reference_title"],
        "date": candidate["reference_date"],
        "npr_url": candidate["npr_url"],
        "story_id": candidate["npr_story_id"],
        "audio_id": player["audio_id"],
        "player_url": player["player_url"],
        "description": description,
        "description_provenance": "affiliate_mirror" if description else None,
        "date_precision": "date_only",
        "recovery_status": "reviewed_near_ready_candidate",
        "release_classification": candidate["release_classification"],
        "recovery_provenance": candidate["provenance"],
    }


def enclosure_record(candidate: dict) -> dict:
    player = candidate["verified_player_identity"]
    audio = candidate["validated_final_audio_url"]
    return {
        "story_id": candidate["npr_story_id"],
        "audio_id": player["audio_id"],
        "date": candidate["reference_date"],
        "title": candidate["reference_title"],
        "npr_url": candidate["npr_url"],
        "status": "resolved",
        "enclosure_url": audio,
        "final_url": audio,
        "episode_uuid": None,
        "http_status": 206,
        "content_type": "audio/mpeg",
        "content_length": candidate["declared_file_size_bytes"],
        "duration_seconds": candidate["duration_seconds"],
        "extraction_method": "reviewed_early_near_ready_staging",
        "provenance": candidate["provenance"],
        "retry_count": 0,
        "resolved_at": None,
        "observed_player_ids": candidate["observed_player_ids"],
        "audio_url_e_parameter": candidate["audio_url_e_parameter"],
        "release_classification": candidate["release_classification"],
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(repo_root: Path = REPO_ROOT, stage_dir: Path | None = None) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    stage_dir.mkdir(parents=True, exist_ok=True)
    before = production_hashes(repo_root)
    audit = json.loads((repo_root / AUDIT.relative_to(REPO_ROOT)).read_text(encoding="utf-8"))
    approved = [
        item for item in audit["episodes"]
        if item["review_outcome"] in {"ready_with_limited_metadata", "already_in_production"}
    ]
    affiliates = affiliate_index(repo_root)
    candidates = [
        candidate_for_build(
            item, affiliates.get((item["reference_date"], item["reference_title"]))
        )
        for item in approved
    ]
    candidate_ids = {item["npr_story_id"] for item in candidates}
    if len(candidates) != 8 or len(candidate_ids) != 8:
        raise RuntimeError("Reviewed cohort must contain exactly eight unique story IDs.")

    history_path = repo_root / "indicator_history.json"
    map_path = repo_root / "indicator_enclosure_map.json"
    feed_path = repo_root / "theindicator_feed.xml"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    enclosure_map = json.loads(map_path.read_text(encoding="utf-8"))
    history_before = len(history["episodes"])
    map_before = len(enclosure_map["episodes"])
    production_ids = {str(item.get("story_id")) for item in history["episodes"]}
    if candidate_ids & production_ids or candidate_ids & set(enclosure_map["episodes"]):
        raise RuntimeError("At least one reviewed candidate is already in production.")

    history["episodes"].extend(history_record(item) for item in candidates)
    history["episodes"].sort(key=lambda item: item.get("date") or "", reverse=True)
    history["episode_count"] = len(history["episodes"])
    for item in candidates:
        enclosure_map["episodes"][item["npr_story_id"]] = enclosure_record(item)

    tree = ET.parse(feed_path)
    channel = tree.getroot().find("channel")
    if channel is None:
        raise RuntimeError("Production feed has no channel element.")
    feed_before = len(channel.findall("item"))
    for item in candidates:
        channel.append(build_feed_item(item))
    items = channel.findall("item")
    for item in items:
        channel.remove(item)
    for item in sorted(items, key=feed_timestamp, reverse=True):
        channel.append(item)

    staged_history = stage_dir / "indicator_history.json"
    staged_map = stage_dir / "indicator_enclosure_map.json"
    staged_feed = stage_dir / "theindicator_feed.xml"
    write_json(staged_history, history)
    write_json(staged_map, enclosure_map)
    ET.indent(tree, space="  ")
    tree.write(staged_feed, encoding="utf-8", xml_declaration=True)

    parsed_items = ET.parse(staged_feed).getroot().findall(".//item")
    story_ids = [story_id_from_feed_item(item) for item in parsed_items]
    guids = [(item.findtext("guid") or "").strip() for item in parsed_items]
    dates = [parsedate_to_datetime(item.findtext("pubDate") or "") for item in parsed_items]
    staged_feed_ids = feed_story_ids(staged_feed)
    production_feed_ids = feed_story_ids(feed_path)
    promoted_history = [
        item for item in history["episodes"] if str(item.get("story_id")) in candidate_ids
    ]
    promoted_items = [item for item in parsed_items if story_id_from_feed_item(item) in candidate_ids]
    promoted_enclosures = [enclosure_map["episodes"][story_id] for story_id in candidate_ids]
    audio_paths = [urlparse(item["final_url"]).path.lower() for item in promoted_enclosures]
    after = production_hashes(repo_root)
    checks = {
        "production_files_unchanged": before == after,
        "candidate_count_is_8": len(candidates) == 8,
        "candidate_story_ids_unique": len(candidate_ids) == 8,
        "history_delta_is_8": len(history["episodes"]) - history_before == 8,
        "history_episode_count_consistent": history["episode_count"] == len(history["episodes"]),
        "history_story_ids_unique": len({str(item.get("story_id")) for item in history["episodes"]}) == len(history["episodes"]),
        "enclosure_map_delta_is_8": len(enclosure_map["episodes"]) - map_before == 8,
        "feed_delta_is_8": len(parsed_items) - feed_before == 8,
        "feed_story_id_delta_exact": staged_feed_ids - production_feed_ids == candidate_ids,
        "rss_guids_unique": len(guids) == len(set(guids)),
        "rss_story_ids_unique": len([x for x in story_ids if x]) == len(set(x for x in story_ids if x)),
        "rss_sorted_newest_first": dates == sorted(dates, reverse=True),
        "rss_xml_parses": True,
        "all_promoted_history_records_present": len(promoted_history) == 8,
        "all_promoted_feed_items_present": len(promoted_items) == 8,
        "all_promoted_enclosures_resolved": all(item["status"] == "resolved" for item in promoted_enclosures),
        "all_promoted_audio_npr_hosted": all(urlparse(item["final_url"]).netloc == "ondemand.npr.org" for item in promoted_enclosures),
        "promoted_audio_paths_unique": len(audio_paths) == len(set(audio_paths)),
        "all_promoted_have_player_identity": all(item.get("audio_id") and item.get("player_url") for item in promoted_history),
        "all_promoted_dates_marked_date_only": all(item.get("date_precision") == "date_only" for item in promoted_history),
    }
    report = {
        "report_version": 1,
        "mode": "staging_only",
        "production_files_modified": False,
        "source_audit": str(AUDIT.relative_to(REPO_ROOT)),
        "stage_directory": str(stage_dir.relative_to(repo_root)),
        "counts": {
            "promoted_candidates": 8,
            "history_before": history_before,
            "history_after": len(history["episodes"]),
            "enclosure_map_before": map_before,
            "enclosure_map_after": len(enclosure_map["episodes"]),
            "feed_before": feed_before,
            "feed_after": len(parsed_items),
            "known_content_lengths": sum(bool(item["content_length"]) for item in promoted_enclosures),
            "descriptions_present": sum(bool(item.get("description")) for item in promoted_history),
        },
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "candidate_story_ids": sorted(candidate_ids),
        "production_sha256_before": before,
        "production_sha256_after": after,
        "staged_sha256": {name: sha256(stage_dir / name) for name in PRODUCTION_FILES},
        "next_gate": "review the staged diff, then use the existing hash-guarded atomic production workflow",
    }
    write_json(stage_dir / "staging_report.json", report)
    if not report["all_checks_passed"]:
        failed = [name for name, passed in checks.items() if not passed]
        raise RuntimeError(f"Staging validation failed: {failed}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", type=Path, default=DEFAULT_STAGE_DIR)
    args = parser.parse_args()
    report = stage(stage_dir=args.stage_dir)
    write_json(REPORT, report)
    print(json.dumps(report["counts"], indent=2))
    print(json.dumps(report["checks"], indent=2))
    print("Staging complete. Production files were not modified.")


if __name__ == "__main__":
    main()
