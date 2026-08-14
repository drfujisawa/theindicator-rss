#!/usr/bin/env python3
"""Stage the confirmed calculator original and rebroadcast."""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.audit_early_promotion_candidates import (  # noqa: E402
    feed_story_ids,
    story_id_from_feed_item,
)
from scripts.analysis.build_early_promotion_dry_run import (  # noqa: E402
    ITUNES_NS,
    feed_timestamp,
    production_hashes,
    write_json,
)

REVIEW = REPO_ROOT / "data/audits/indicator_calculator_releases_review.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/calculator-releases-staging"
REPORT = REPO_ROOT / "data/audits/indicator_calculator_releases_staging_report.json"
PRODUCTION_FILES = (
    "indicator_history.json",
    "indicator_enclosure_map.json",
    "theindicator_feed.xml",
)


def publication_datetime(item: dict) -> datetime:
    archived = item.get("archived_feed_publication_time")
    if archived:
        return parsedate_to_datetime(archived)
    return datetime.fromisoformat(item["reference_date"] + "T00:00:00+00:00")


def history_record(item: dict) -> dict:
    original_id = (item.get("relationship_to") or {}).get("npr_story_id")
    return {
        "title": item["reference_title"],
        "date": publication_datetime(item).isoformat(),
        "npr_url": item["npr_url"],
        "story_id": item["npr_story_id"],
        "audio_id": item["audio_id"],
        "player_url": item["player_url"],
        "description": "Creative destruction is a fact of economic life that few products can resist. Graphing calculators are a notable exception.",
        "description_provenance": (
            "archived_npr_feed" if item.get("archived_feed_publication_time")
            else "archived_npr_feed_shared_episode_description"
        ),
        "date_precision": "exact" if item.get("archived_feed_publication_time") else "date_only",
        "recovery_status": "reviewed_calculator_release",
        "release_classification": item["release_classification"],
        "same_title_relationship": (
            "dated_rebroadcast_of_original_release" if original_id else "original_release_with_later_rebroadcast"
        ),
        "related_story_id": original_id or "642675050",
        "recovery_provenance": [
            "indicator_calculator_releases_review.json",
            "live NPR story page",
            "archived NPR feed" if item.get("archived_feed_publication_time") else "KAWC affiliate mirror",
        ],
    }


def enclosure_record(item: dict) -> dict:
    original_id = (item.get("relationship_to") or {}).get("npr_story_id")
    return {
        "story_id": item["npr_story_id"],
        "audio_id": item["audio_id"],
        "date": item["reference_date"],
        "title": item["reference_title"],
        "npr_url": item["npr_url"],
        "status": "resolved",
        "enclosure_url": item["audio_url"],
        "final_url": item["audio_url"],
        "episode_uuid": item.get("archived_feed_guid"),
        "http_status": 206,
        "content_type": "audio/mpeg",
        "content_length": item["content_length_bytes"],
        "duration_seconds": item["duration_seconds"],
        "extraction_method": "reviewed_calculator_release",
        "provenance": ["indicator_calculator_releases_review.json"],
        "retry_count": 0,
        "resolved_at": None,
        "observed_player_ids": [item["player_story_id"], item["audio_id"], item["npr_story_id"]],
        "audio_url_e_parameter": item["player_story_id"],
        "release_classification": item["release_classification"],
        "related_story_id": original_id or "642675050",
    }


def feed_item(item: dict) -> ET.Element:
    element = ET.Element("item")
    ET.SubElement(element, "title").text = item["reference_title"]
    ET.SubElement(element, "description").text = "Creative destruction is a fact of economic life that few products can resist. Graphing calculators are a notable exception."
    ET.SubElement(element, "pubDate").text = format_datetime(publication_datetime(item))
    ET.SubElement(element, "link").text = item["npr_url"]
    ET.SubElement(element, "guid", {"isPermaLink": "false"}).text = item["npr_story_id"]
    ET.SubElement(element, "enclosure", {
        "url": item["audio_url"],
        "length": str(item["content_length_bytes"]),
        "type": "audio/mpeg",
    })
    ET.SubElement(element, f"{{{ITUNES_NS}}}title").text = item["reference_title"]
    ET.SubElement(element, f"{{{ITUNES_NS}}}episodeType").text = "full"
    ET.SubElement(element, f"{{{ITUNES_NS}}}explicit").text = "no"
    return element


def stage(repo_root: Path = REPO_ROOT, stage_dir: Path | None = None) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    stage_dir.mkdir(parents=True, exist_ok=True)
    before = production_hashes(repo_root)
    review = json.loads((repo_root / REVIEW.relative_to(REPO_ROOT)).read_text(encoding="utf-8"))
    releases = review["releases"]
    ids = {item["npr_story_id"] for item in releases}
    if len(releases) != 2 or len(ids) != 2:
        raise RuntimeError("Review must contain exactly two unique releases.")

    history = json.loads((repo_root / "indicator_history.json").read_text(encoding="utf-8"))
    enclosure = json.loads((repo_root / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
    production_ids = {str(item.get("story_id")) for item in history["episodes"]}
    if ids & production_ids or ids & set(enclosure["episodes"]):
        raise RuntimeError("A calculator release is already in production.")
    history_before = len(history["episodes"])
    map_before = len(enclosure["episodes"])
    history["episodes"].extend(history_record(item) for item in releases)
    history["episodes"].sort(key=lambda item: item.get("date") or "", reverse=True)
    history["episode_count"] = len(history["episodes"])
    for item in releases:
        enclosure["episodes"][item["npr_story_id"]] = enclosure_record(item)

    tree = ET.parse(repo_root / "theindicator_feed.xml")
    channel = tree.getroot().find("channel")
    if channel is None:
        raise RuntimeError("Production feed has no channel.")
    feed_before = len(channel.findall("item"))
    for item in releases:
        channel.append(feed_item(item))
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
    story_ids = [item for item in story_ids if item]
    dates = [parsedate_to_datetime(item.findtext("pubDate") or "") for item in parsed_items]
    promoted_history = [item for item in history["episodes"] if str(item.get("story_id")) in ids]
    after = production_hashes(repo_root)
    checks = {
        "production_files_unchanged": before == after,
        "release_count_is_2": len(releases) == 2,
        "one_original_and_one_rebroadcast": {item["release_classification"] for item in releases} == {"confirmed_indicator_episode", "confirmed_indicator_rebroadcast"},
        "history_delta_is_2": len(history["episodes"]) - history_before == 2,
        "history_count_consistent": history["episode_count"] == len(history["episodes"]),
        "enclosure_map_delta_is_2": len(enclosure["episodes"]) - map_before == 2,
        "feed_delta_is_2": len(parsed_items) - feed_before == 2,
        "feed_story_id_delta_exact": feed_story_ids(feed_path) - feed_story_ids(repo_root / "theindicator_feed.xml") == ids,
        "rss_guids_unique": not any(value > 1 for value in Counter(guids).values()),
        "rss_story_ids_unique": len(story_ids) == len(set(story_ids)),
        "rss_sorted_newest_first": dates == sorted(dates, reverse=True),
        "rss_xml_parses": True,
        "all_enclosures_resolved": all(enclosure["episodes"][story_id]["status"] == "resolved" for story_id in ids),
        "all_lengths_known": all(enclosure["episodes"][story_id]["content_length"] > 0 for story_id in ids),
        "relationship_is_bidirectional": {item.get("related_story_id") for item in promoted_history} == ids,
        "original_exact_timestamp_preserved": any(item.get("date_precision") == "exact" for item in promoted_history),
    }
    report = {
        "report_version": 1,
        "mode": "staging_only",
        "production_files_modified": False,
        "source_review": str(REVIEW.relative_to(REPO_ROOT)),
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
        "next_gate": "review before hash-guarded atomic production application",
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
