#!/usr/bin/env python3
"""Build isolated early-history promotion artifacts without touching production."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis.audit_early_promotion_candidates import (
    FEED,
    HISTORY,
    ENCLOSURE_MAP,
    OUTPUT as MANIFEST,
    REPO_ROOT,
    build_manifest,
    feed_story_ids,
    story_id_from_feed_item,
)


DEFAULT_OUTPUT_DIR = REPO_ROOT / "work" / "early-promotion-dry-run"
REPORT = REPO_ROOT / "data" / "audits" / "indicator_early_promotion_dry_run_report.json"
ENRICHMENT_AUDIT = (
    REPO_ROOT / "data" / "audits" / "indicator_early_metadata_enrichment_audit.json"
)
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("media", "http://search.yahoo.com/mrss/")
ET.register_namespace("podcast", "https://podcastindex.org/namespace/1.0")
ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def production_hashes(repo_root: Path) -> dict[str, str]:
    return {
        name: sha256(repo_root / name)
        for name in ("indicator_history.json", "indicator_enclosure_map.json", "theindicator_feed.xml")
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def make_history_record(candidate: dict) -> dict:
    player = candidate.get("verified_player_identity") or {}
    affiliate = candidate.get("supporting_non_npr_metadata") or {}
    return {
        "title": candidate["reference_title"],
        "date": candidate["reference_date"],
        "npr_url": candidate["npr_url"],
        "story_id": candidate["npr_story_id"],
        "audio_id": player.get("audio_id"),
        "player_url": player.get("player_url"),
        "description": affiliate.get("affiliate_description"),
        "description_provenance": (
            "affiliate_mirror" if affiliate.get("affiliate_description") else None
        ),
        "date_precision": "date_only",
        "recovery_status": "dry_run_strong_candidate",
        "release_classification": candidate.get("release_classification"),
        "same_title_relationship": candidate.get("same_title_relationship"),
        "recovery_provenance": candidate["provenance"],
    }


def make_enclosure_record(candidate: dict) -> dict:
    player = candidate.get("verified_player_identity") or {}
    return {
        "story_id": candidate["npr_story_id"],
        "audio_id": player.get("audio_id"),
        "date": candidate["reference_date"],
        "title": candidate["reference_title"],
        "npr_url": candidate["npr_url"],
        "status": "resolved",
        "enclosure_url": candidate["validated_final_audio_url"],
        "final_url": candidate["validated_final_audio_url"],
        "episode_uuid": None,
        "http_status": 206,
        "content_type": "audio/mpeg",
        "content_length": candidate.get("declared_file_size_bytes"),
        "duration_seconds": candidate.get("duration_seconds"),
        "extraction_method": "early_promotion_dry_run",
        "provenance": candidate["provenance"],
        "retry_count": 0,
        "resolved_at": None,
        "observed_player_ids": candidate["observed_player_ids"],
        "audio_url_e_parameter": candidate["audio_url_e_parameter"],
        "release_classification": candidate.get("release_classification"),
    }


def build_feed_item(candidate: dict) -> ET.Element:
    item = ET.Element("item")
    title = candidate["reference_title"]
    ET.SubElement(item, "title").text = title
    description = (
        candidate.get("supporting_non_npr_metadata", {}).get("affiliate_description")
        or title
    )
    ET.SubElement(item, "description").text = description
    date = datetime.fromisoformat(candidate["reference_date"] + "T00:00:00+00:00")
    ET.SubElement(item, "pubDate").text = format_datetime(date)
    ET.SubElement(item, "link").text = candidate["npr_url"]
    guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
    guid.text = candidate["npr_story_id"]
    ET.SubElement(
        item,
        "enclosure",
        {
            "url": candidate["validated_final_audio_url"],
            "length": str(candidate.get("declared_file_size_bytes") or 0),
            "type": "audio/mpeg",
        },
    )
    ET.SubElement(item, f"{{{ITUNES_NS}}}title").text = title
    ET.SubElement(item, f"{{{ITUNES_NS}}}episodeType").text = "full"
    ET.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "no"
    return item


def feed_timestamp(item: ET.Element) -> float:
    try:
        return parsedate_to_datetime(item.findtext("pubDate") or "").timestamp()
    except (TypeError, ValueError):
        return 0


def build_dry_run(repo_root: Path = REPO_ROOT, output_dir: Path | None = None) -> dict:
    output_dir = output_dir or (repo_root / DEFAULT_OUTPUT_DIR.relative_to(REPO_ROOT))
    output_dir.mkdir(parents=True, exist_ok=True)
    before = production_hashes(repo_root)

    manifest = build_manifest(repo_root)
    enrichment = json.loads(
        (repo_root / ENRICHMENT_AUDIT.relative_to(REPO_ROOT)).read_text(encoding="utf-8")
    )
    enrichment_by_id = {
        entry["npr_story_id"]: entry for entry in enrichment["episodes"]
    }
    approved_dry_run_ids = {
        entry["npr_story_id"]
        for entry in enrichment["episodes"]
        if entry["promotion_tier"] == "ready_with_limited_metadata"
    }
    candidates = []
    for entry in manifest["episodes"]:
        if entry["npr_story_id"] not in approved_dry_run_ids:
            continue
        enriched = enrichment_by_id[entry["npr_story_id"]]
        candidates.append({
            **entry,
            "verified_player_identity": enriched["verified_metadata"]["player_identity"],
            "release_classification": enriched["release_classification"],
            "same_title_relationship": enriched["same_title_relationship"],
            "supporting_non_npr_metadata": enriched["supporting_non_npr_metadata"],
            "duration_seconds": enriched["verified_metadata"]["duration_seconds"]["value"],
            "declared_file_size_bytes": enriched["verified_metadata"]["declared_file_size_bytes"]["value"],
        })
    candidate_ids = {entry["npr_story_id"] for entry in candidates}
    if len(candidates) != 226 or len(candidate_ids) != len(candidates):
        raise RuntimeError("Safe-cohort count or story-ID uniqueness changed; aborting.")

    history = json.loads((repo_root / HISTORY.relative_to(REPO_ROOT)).read_text(encoding="utf-8"))
    enclosure_map = json.loads(
        (repo_root / ENCLOSURE_MAP.relative_to(REPO_ROOT)).read_text(encoding="utf-8")
    )
    production_history_count = len(history["episodes"])
    production_map_count = len(enclosure_map["episodes"])
    production_ids = {
        str(entry["story_id"]) for entry in history["episodes"] if entry.get("story_id")
    }
    overlap = candidate_ids & production_ids
    if overlap:
        raise RuntimeError(
            f"Promotion cohort is already present in production ({len(overlap)} IDs); "
            "refusing to build a duplicate dry run."
        )

    history["episodes"].extend(make_history_record(entry) for entry in candidates)
    history["episodes"].sort(key=lambda entry: entry.get("date") or "", reverse=True)
    history["episode_count"] = len(history["episodes"])
    for entry in candidates:
        enclosure_map["episodes"][entry["npr_story_id"]] = make_enclosure_record(entry)

    history_path = output_dir / "indicator_history.json"
    map_path = output_dir / "indicator_enclosure_map.json"
    feed_path = output_dir / "theindicator_feed.xml"
    write_json(history_path, history)
    write_json(map_path, enclosure_map)

    tree = ET.parse(repo_root / FEED.relative_to(REPO_ROOT))
    channel = tree.getroot().find("channel")
    if channel is None:
        raise RuntimeError("Production feed has no channel element.")
    production_feed_count = len(channel.findall("item"))
    for entry in candidates:
        channel.append(build_feed_item(entry))
    items = channel.findall("item")
    for item in items:
        channel.remove(item)
    for item in sorted(items, key=feed_timestamp, reverse=True):
        channel.append(item)
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)

    dry_feed_ids = feed_story_ids(feed_path)
    production_feed_ids = feed_story_ids(repo_root / FEED.relative_to(REPO_ROOT))
    parsed_items = ET.parse(feed_path).getroot().findall(".//item")
    parsed_story_ids = [story_id_from_feed_item(item) for item in parsed_items]
    duplicate_feed_story_ids = sorted(
        story_id for story_id, count in Counter(parsed_story_ids).items() if story_id and count > 1
    )
    audio_hosts = Counter(
        urlparse(entry["validated_final_audio_url"]).netloc for entry in candidates
    )

    after = production_hashes(repo_root)
    unchanged = before == after
    checks = {
        "production_files_unchanged": unchanged,
        "candidate_count_is_226": len(candidates) == 226,
        "candidate_story_ids_unique": len(candidate_ids) == len(candidates),
        "history_delta_is_226": len(history["episodes"]) - production_history_count == 226,
        "enclosure_map_delta_is_226": len(enclosure_map["episodes"]) - production_map_count == 226,
        "feed_delta_is_226": len(parsed_items) - production_feed_count == 226,
        "feed_story_id_delta_exact": dry_feed_ids - production_feed_ids == candidate_ids,
        "no_duplicate_feed_story_ids": not duplicate_feed_story_ids,
        "all_candidate_audio_is_npr_hosted": set(audio_hosts) == {"ondemand.npr.org"},
        "dry_run_xml_parses": True,
    }
    if not unchanged:
        raise RuntimeError("A production file changed during the dry run; aborting.")

    report = {
        "report_version": 1,
        "mode": "dry_run_only",
        "production_files_modified": False,
        "source_manifest": str(MANIFEST.relative_to(REPO_ROOT)),
        "source_enrichment_audit": str(ENRICHMENT_AUDIT.relative_to(REPO_ROOT)),
        "output_directory": str(output_dir.relative_to(repo_root)),
        "counts": {
            "promoted_candidates": len(candidates),
            "history_before": production_history_count,
            "history_after": len(history["episodes"]),
            "enclosure_map_before": production_map_count,
            "enclosure_map_after": len(enclosure_map["episodes"]),
            "feed_before": production_feed_count,
            "feed_after": len(parsed_items),
        },
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "candidate_audio_hosts": dict(audio_hosts),
        "duplicate_feed_story_ids": duplicate_feed_story_ids,
        "known_metadata_omissions": {
            "canonical_audio_id": sum(
                not candidate.get("verified_player_identity") for candidate in candidates
            ),
            "canonical_player_url": sum(
                not candidate.get("verified_player_identity") for candidate in candidates
            ),
            "npr_description": len(candidates),
            "any_description": sum(
                not candidate.get("supporting_non_npr_metadata", {}).get("affiliate_description")
                for candidate in candidates
            ),
            "exact_publication_time": len(candidates),
            "audio_content_length": sum(
                candidate.get("declared_file_size_bytes") is None
                for candidate in candidates
            ),
        },
        "production_sha256_before": before,
        "production_sha256_after": after,
        "artifacts": {
            "history": str(history_path.relative_to(repo_root)),
            "enclosure_map": str(map_path.relative_to(repo_root)),
            "feed": str(feed_path.relative_to(repo_root)),
        },
    }
    write_json(output_dir / "dry_run_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    report = build_dry_run(output_dir=args.output_dir)
    write_json(REPORT, report)
    print(json.dumps(report["counts"], indent=2))
    print(json.dumps(report["checks"], indent=2))
    print(f"Dry-run artifacts: {args.output_dir}")
    print("Production files were not modified.")


if __name__ == "__main__":
    main()
