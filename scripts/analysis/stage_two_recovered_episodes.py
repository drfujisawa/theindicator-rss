#!/usr/bin/env python3
"""Stage the two newly recovered early episodes without production writes."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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

DISCOVERY = REPO_ROOT / "data/audits/indicator_two_episode_audio_discovery.json"
AFFILIATE = REPO_ROOT / "data/recovery/indicator_recovered_episodes.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/two-episode-recovery-staging"
REPORT = REPO_ROOT / "data/audits/indicator_two_episode_staging_report.json"
PRODUCTION_FILES = (
    "indicator_history.json",
    "indicator_enclosure_map.json",
    "theindicator_feed.xml",
)


def affiliate_descriptions(repo_root: Path) -> dict[tuple[str, str], dict]:
    data = json.loads((repo_root / AFFILIATE.relative_to(REPO_ROOT)).read_text(encoding="utf-8"))
    return {
        (item["reference_date"], item["reference_title"]): item
        for item in data["recovered"]
    }


def build_candidate(item: dict, affiliate: dict | None) -> dict:
    return {
        "reference_date": item["reference_date"],
        "reference_title": item["reference_title"],
        "npr_story_id": item["npr_story_id"],
        "npr_url": item["npr_url"],
        "validated_final_audio_url": item["audio_url"],
        "verified_player_identity": {
            "player_story_id": item["player_story_id"],
            "audio_id": item["audio_id"],
            "player_url": item["player_url"],
        },
        "supporting_non_npr_metadata": {
            "affiliate_description": (affiliate or {}).get("description"),
            "affiliate_url": (affiliate or {}).get("source_url"),
        },
        "declared_file_size_bytes": item["content_length_bytes"],
        "duration_seconds": item["duration_seconds"],
        "observed_player_ids": [item["player_story_id"], item["audio_id"], item["npr_story_id"]],
        "audio_url_e_parameter": item["player_story_id"],
        "release_classification": "confirmed_indicator_episode",
        "same_title_relationship": None,
        "provenance": [
            "indicator_two_episode_audio_discovery.json",
            "live NPR story page",
            "validated NPR-hosted byte-range response",
        ],
    }


def history_record(item: dict) -> dict:
    player = item["verified_player_identity"]
    description = item["supporting_non_npr_metadata"]["affiliate_description"]
    return {
        "title": item["reference_title"],
        "date": item["reference_date"],
        "npr_url": item["npr_url"],
        "story_id": item["npr_story_id"],
        "audio_id": player["audio_id"],
        "player_url": player["player_url"],
        "description": description or item["reference_title"],
        "description_provenance": "affiliate_mirror" if description else "reference_title_fallback",
        "date_precision": "date_only",
        "recovery_status": "validated_two_episode_recovery",
        "release_classification": item["release_classification"],
        "recovery_provenance": item["provenance"],
    }


def enclosure_record(item: dict) -> dict:
    player = item["verified_player_identity"]
    return {
        "story_id": item["npr_story_id"],
        "audio_id": player["audio_id"],
        "date": item["reference_date"],
        "title": item["reference_title"],
        "npr_url": item["npr_url"],
        "status": "resolved",
        "enclosure_url": item["validated_final_audio_url"],
        "final_url": item["validated_final_audio_url"],
        "episode_uuid": None,
        "http_status": 206,
        "content_type": "audio/mpeg",
        "content_length": item["declared_file_size_bytes"],
        "duration_seconds": item["duration_seconds"],
        "extraction_method": "validated_two_episode_recovery",
        "provenance": item["provenance"],
        "retry_count": 0,
        "resolved_at": None,
        "observed_player_ids": item["observed_player_ids"],
        "audio_url_e_parameter": item["audio_url_e_parameter"],
        "release_classification": item["release_classification"],
    }


def stage(repo_root: Path = REPO_ROOT, stage_dir: Path | None = None) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    stage_dir.mkdir(parents=True, exist_ok=True)
    before = production_hashes(repo_root)
    discovery = json.loads((repo_root / DISCOVERY.relative_to(REPO_ROOT)).read_text(encoding="utf-8"))
    affiliates = affiliate_descriptions(repo_root)
    candidates = [
        build_candidate(item, affiliates.get((item["reference_date"], item["reference_title"])))
        for item in discovery["episodes"]
    ]
    ids = {item["npr_story_id"] for item in candidates}
    if len(candidates) != 2 or len(ids) != 2:
        raise RuntimeError("Discovery must contain exactly two unique approved episodes.")

    history = json.loads((repo_root / "indicator_history.json").read_text(encoding="utf-8"))
    enclosure = json.loads((repo_root / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
    production_ids = {str(item.get("story_id")) for item in history["episodes"]}
    if ids & production_ids or ids & set(enclosure["episodes"]):
        raise RuntimeError("A recovered episode is already in production.")
    history_before = len(history["episodes"])
    map_before = len(enclosure["episodes"])
    history["episodes"].extend(history_record(item) for item in candidates)
    history["episodes"].sort(key=lambda item: item.get("date") or "", reverse=True)
    history["episode_count"] = len(history["episodes"])
    for item in candidates:
        enclosure["episodes"][item["npr_story_id"]] = enclosure_record(item)

    tree = ET.parse(repo_root / "theindicator_feed.xml")
    channel = tree.getroot().find("channel")
    if channel is None:
        raise RuntimeError("Production feed has no channel.")
    feed_before = len(channel.findall("item"))
    for item in candidates:
        channel.append(build_feed_item(item))
    items = channel.findall("item")
    for item in items:
        channel.remove(item)
    for item in sorted(items, key=feed_timestamp, reverse=True):
        channel.append(item)

    history_path = stage_dir / "indicator_history.json"
    map_path = stage_dir / "indicator_enclosure_map.json"
    feed_path = stage_dir / "theindicator_feed.xml"
    write_json(history_path, history)
    write_json(map_path, enclosure)
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)

    parsed_items = ET.parse(feed_path).getroot().findall(".//item")
    guids = [(item.findtext("guid") or "").strip() for item in parsed_items]
    story_ids = [story_id_from_feed_item(item) for item in parsed_items]
    dates = [parsedate_to_datetime(item.findtext("pubDate") or "") for item in parsed_items]
    after = production_hashes(repo_root)
    checks = {
        "production_files_unchanged": before == after,
        "candidate_count_is_2": len(candidates) == 2,
        "candidate_story_ids_unique": len(ids) == 2,
        "history_delta_is_2": len(history["episodes"]) - history_before == 2,
        "history_count_consistent": history["episode_count"] == len(history["episodes"]),
        "enclosure_map_delta_is_2": len(enclosure["episodes"]) - map_before == 2,
        "feed_delta_is_2": len(parsed_items) - feed_before == 2,
        "feed_story_id_delta_exact": feed_story_ids(feed_path) - feed_story_ids(repo_root / "theindicator_feed.xml") == ids,
        "rss_guids_unique": not any(value > 1 for value in Counter(guids).values()),
        "rss_story_ids_unique": len([x for x in story_ids if x]) == len(set(x for x in story_ids if x)),
        "rss_sorted_newest_first": dates == sorted(dates, reverse=True),
        "rss_xml_parses": True,
        "all_enclosures_resolved": all(enclosure["episodes"][story_id]["status"] == "resolved" for story_id in ids),
        "all_content_lengths_known": all(enclosure["episodes"][story_id]["content_length"] > 0 for story_id in ids),
        "all_player_identities_present": all(item["verified_player_identity"]["audio_id"] for item in candidates),
    }
    report = {
        "report_version": 1,
        "mode": "staging_only",
        "production_files_modified": False,
        "source_discovery": str(DISCOVERY.relative_to(REPO_ROOT)),
        "stage_directory": str(stage_dir.relative_to(repo_root)),
        "counts": {
            "promoted_candidates": 2,
            "history_before": history_before,
            "history_after": len(history["episodes"]),
            "enclosure_map_before": map_before,
            "enclosure_map_after": len(enclosure["episodes"]),
            "feed_before": feed_before,
            "feed_after": len(parsed_items),
        },
        "candidate_story_ids": sorted(ids),
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "production_sha256_before": before,
        "production_sha256_after": after,
        "next_gate": "review this staging report before any hash-guarded atomic production application",
    }
    write_json(stage_dir / "staging_report.json", report)
    if not report["all_checks_passed"]:
        raise RuntimeError(f"Staging checks failed: {[key for key, value in checks.items() if not value]}")
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
