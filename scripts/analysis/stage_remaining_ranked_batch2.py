#!/usr/bin/env python3
"""Stage the three confirmed episodes from ranked batch 2 without production writes."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.audit_early_promotion_candidates import feed_story_ids, story_id_from_feed_item  # noqa: E402
from scripts.analysis.build_early_promotion_dry_run import (  # noqa: E402
    build_feed_item, feed_timestamp, production_hashes, sha256, write_json,
)

DISCOVERY = REPO_ROOT / "data/audits/indicator_remaining_34_ranked_batch2.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/remaining-ranked-batch2-staging"
REPORT = REPO_ROOT / "data/audits/indicator_remaining_ranked_batch2_staging_report.json"
EXPECTED_COUNT = 3
BATCH_LABEL = "batch2"
DISCOVERY_LABEL = "indicator_remaining_34_ranked_batch2.json"


def preserve_namespace_style(production_feed: Path, staged_feed: Path) -> None:
    """Keep the updater's ns0..ns3 serialization to avoid whole-feed churn."""
    production_head = production_feed.read_text(encoding="utf-8")[:1000]
    if 'xmlns:ns0="http://www.itunes.com/dtds/podcast-1.0.dtd"' not in production_head:
        return
    text = staged_feed.read_text(encoding="utf-8")
    replacements = {
        "xmlns:itunes=": "xmlns:ns0=", "xmlns:media=": "xmlns:ns1=",
        "xmlns:podcast=": "xmlns:ns2=", "xmlns:content=": "xmlns:ns3=",
        "<itunes:": "<ns0:", "</itunes:": "</ns0:",
        "<media:": "<ns1:", "</media:": "</ns1:",
        "<podcast:": "<ns2:", "</podcast:": "</ns2:",
        "<content:": "<ns3:", "</content:": "</ns3:",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    staged_feed.write_text(text, encoding="utf-8")


def candidate(item: dict) -> dict:
    return {
        "reference_date": item["publication_date"],
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
            "affiliate_description": None,
            "affiliate_url": item["affiliate_url"],
        },
        "declared_file_size_bytes": item["content_length_bytes"],
        "duration_seconds": item["duration_seconds"],
        "observed_player_ids": [item["player_story_id"], item["audio_id"], item["npr_story_id"]],
        "audio_url_e_parameter": item["player_story_id"],
        "release_classification": "confirmed_indicator_episode",
        "provenance": [
            DISCOVERY_LABEL,
            "live NPR story page",
            "multiple public-radio affiliate mirrors",
            "validated NPR-hosted byte-range response",
        ],
    }


def history_record(item: dict) -> dict:
    player = item["verified_player_identity"]
    return {
        "title": item["reference_title"], "date": item["reference_date"],
        "npr_url": item["npr_url"], "story_id": item["npr_story_id"],
        "audio_id": player["audio_id"], "player_url": player["player_url"],
        "description": item["reference_title"],
        "description_provenance": "reference_title_fallback", "date_precision": "date_only",
        "recovery_status": f"validated_remaining_ranked_{BATCH_LABEL}",
        "release_classification": item["release_classification"],
        "recovery_provenance": item["provenance"],
    }


def enclosure_record(item: dict) -> dict:
    player = item["verified_player_identity"]
    return {
        "story_id": item["npr_story_id"], "audio_id": player["audio_id"],
        "date": item["reference_date"], "title": item["reference_title"],
        "npr_url": item["npr_url"], "status": "resolved",
        "enclosure_url": item["validated_final_audio_url"],
        "final_url": item["validated_final_audio_url"], "episode_uuid": None,
        "http_status": 206, "content_type": "audio/mpeg",
        "content_length": item["declared_file_size_bytes"],
        "duration_seconds": item["duration_seconds"],
        "extraction_method": f"validated_remaining_ranked_{BATCH_LABEL}",
        "provenance": item["provenance"], "retry_count": 0, "resolved_at": None,
        "observed_player_ids": item["observed_player_ids"],
        "audio_url_e_parameter": item["audio_url_e_parameter"],
        "release_classification": item["release_classification"],
    }


def stage(repo_root: Path = REPO_ROOT, stage_dir: Path | None = None) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    stage_dir.mkdir(parents=True, exist_ok=True)
    before = production_hashes(repo_root)
    discovery = json.loads((repo_root / DISCOVERY.relative_to(REPO_ROOT)).read_text(encoding="utf-8"))
    candidates = [candidate(item) for item in discovery["episodes"] if item["classification"] == "ready_for_isolated_staging"]
    ids = {item["npr_story_id"] for item in candidates}
    if len(candidates) != EXPECTED_COUNT or len(ids) != EXPECTED_COUNT:
        raise RuntimeError(f"Discovery must contain exactly {EXPECTED_COUNT} unique approved episodes.")

    history = json.loads((repo_root / "indicator_history.json").read_text(encoding="utf-8"))
    enclosure = json.loads((repo_root / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
    production_ids = {str(item.get("story_id")) for item in history["episodes"]}
    if ids & production_ids or ids & set(enclosure["episodes"]):
        raise RuntimeError("A recovered episode is already in production.")
    history_before, map_before = len(history["episodes"]), len(enclosure["episodes"])
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

    history_path, map_path, feed_path = (
        stage_dir / "indicator_history.json", stage_dir / "indicator_enclosure_map.json",
        stage_dir / "theindicator_feed.xml",
    )
    write_json(history_path, history)
    write_json(map_path, enclosure)
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    preserve_namespace_style(repo_root / "theindicator_feed.xml", feed_path)

    parsed_items = ET.parse(feed_path).getroot().findall(".//item")
    guids = [(item.findtext("guid") or "").strip() for item in parsed_items]
    story_ids = [story_id_from_feed_item(item) for item in parsed_items]
    dates = [parsedate_to_datetime(item.findtext("pubDate") or "") for item in parsed_items]
    after = production_hashes(repo_root)
    checks = {
        "production_files_unchanged": before == after,
        f"candidate_count_is_{EXPECTED_COUNT}": len(candidates) == EXPECTED_COUNT,
        "candidate_story_ids_unique": len(ids) == EXPECTED_COUNT,
        f"history_delta_is_{EXPECTED_COUNT}": len(history["episodes"]) - history_before == EXPECTED_COUNT,
        "history_count_consistent": history["episode_count"] == len(history["episodes"]),
        f"enclosure_map_delta_is_{EXPECTED_COUNT}": len(enclosure["episodes"]) - map_before == EXPECTED_COUNT,
        f"feed_delta_is_{EXPECTED_COUNT}": len(parsed_items) - feed_before == EXPECTED_COUNT,
        "feed_story_id_delta_exact": feed_story_ids(feed_path) - feed_story_ids(repo_root / "theindicator_feed.xml") == ids,
        "rss_guids_unique": not any(value > 1 for value in Counter(guids).values()),
        "rss_story_ids_unique": len([x for x in story_ids if x]) == len(set(x for x in story_ids if x)),
        "rss_sorted_newest_first": dates == sorted(dates, reverse=True),
        "rss_xml_parses": True,
        "all_enclosures_resolved": all(enclosure["episodes"][story_id]["status"] == "resolved" for story_id in ids),
        "all_content_lengths_known": all(enclosure["episodes"][story_id]["content_length"] > 0 for story_id in ids),
        "all_audio_npr_hosted": all("ondemand.npr.org/" in item["validated_final_audio_url"] for item in candidates),
    }
    report = {
        "report_version": 1, "mode": "staging_only", "production_files_modified": False,
        "source_discovery": str(DISCOVERY.relative_to(REPO_ROOT)),
        "stage_directory": str(stage_dir.relative_to(repo_root)),
        "counts": {
            "promoted_candidates": EXPECTED_COUNT, "history_before": history_before,
            "history_after": len(history["episodes"]), "enclosure_map_before": map_before,
            "enclosure_map_after": len(enclosure["episodes"]), "feed_before": feed_before,
            "feed_after": len(parsed_items),
        },
        "candidate_story_ids": sorted(ids), "checks": checks,
        "all_checks_passed": all(checks.values()),
        "production_sha256_before": before, "production_sha256_after": after,
        "staged_sha256": {
            "indicator_history.json": sha256(history_path),
            "indicator_enclosure_map.json": sha256(map_path),
            "theindicator_feed.xml": sha256(feed_path),
        },
        "next_gate": "review this staging report before any hash-guarded atomic production application",
    }
    write_json(stage_dir / "staging_report.json", report)
    if not report["all_checks_passed"]:
        raise RuntimeError(f"Staging checks failed: {[key for key, value in checks.items() if not value]}")
    return report


if __name__ == "__main__":
    result = stage()
    write_json(REPORT, result)
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["checks"], indent=2))
    print("Staging complete. Production files were not modified.")
