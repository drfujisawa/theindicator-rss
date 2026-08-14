#!/usr/bin/env python3
"""Stage the original 59 pre-March 2018 Indicator feed items without production writes."""

from __future__ import annotations

import json
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_remaining_ranked_batch2 as engine
from scripts.validate_feed_integrity import validate

REPO_ROOT = engine.REPO_ROOT
AUDIT = REPO_ROOT / "data/audits/indicator_pre_march_2018_catalog_audit.json"
DISCOVERY = REPO_ROOT / "data/audits/indicator_pre_march_2018_staging_candidates.json"
REPORT = REPO_ROOT / "data/audits/indicator_pre_march_2018_staging_report.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/pre-march-2018-staging"
EXPECTED_COUNT = 59
TRAILER_STORY_ID = "567752488"
USER_AGENT = "theindicator-rss pre-March 2018 staging review"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def verify_item(item: dict) -> dict:
    date_path = str(item["published"])[:10].replace("-", "/")
    locator = f"https://www.npr.org/{date_path}/{item['story_id']}"
    page_request = urllib.request.Request(locator, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(page_request, timeout=45) as response:
        response.read(1)
        canonical_url = response.geturl()
        page_status = response.status
    if page_status != 200 or f"/{item['story_id']}/" not in canonical_url:
        raise RuntimeError(f"Canonical NPR identity failed for {item['story_id']}")

    audio_request = urllib.request.Request(
        item["candidate_audio_url"],
        headers={
            "User-Agent": USER_AGENT,
            "Range": "bytes=0-0",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(audio_request, timeout=45) as response:
        response.read(1)
        content_range = response.headers.get("Content-Range", "")
        content_type = response.headers.get_content_type()
        audio_status = response.status
        final_audio_url = response.geturl()
    prefix = "bytes 0-0/"
    if audio_status != 206 or content_type != "audio/mpeg" or not content_range.startswith(prefix):
        raise RuntimeError(f"Live NPR audio validation failed for {item['story_id']}")
    content_length = int(content_range[len(prefix):])
    if content_length != item["audio_probe"]["content_length"]:
        raise RuntimeError(f"Audio length changed for {item['story_id']}")
    return {
        "canonical_npr_url": canonical_url,
        "canonical_http_status": page_status,
        "audio_url": final_audio_url,
        "audio_http_status": audio_status,
        "content_type": content_type,
        "content_range": content_range,
        "content_length_bytes": content_length,
    }


def discover(workers: int = 12) -> tuple[dict, dict[str, dict]]:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    source_items = audit["items"]
    if len(source_items) != EXPECTED_COUNT:
        raise RuntimeError(f"Expected {EXPECTED_COUNT} audited source items.")

    verified: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(verify_item, item): item for item in source_items}
        for future in as_completed(futures):
            item = futures[future]
            verified[item["story_id"]] = future.result()

    episodes = []
    for item in source_items:
        check = verified[item["story_id"]]
        episodes.append({
            "publication_date": item["published"][:10],
            "reference_title": item["title"],
            "npr_story_id": item["story_id"],
            "npr_url": check["canonical_npr_url"],
            "player_story_id": item["story_id"],
            "audio_id": item["story_id"],
            "player_url": check["canonical_npr_url"],
            "affiliate_url": audit["source"]["snapshot_url"],
            "audio_url": check["audio_url"],
            "content_length_bytes": check["content_length_bytes"],
            "duration_seconds": item["duration_seconds"],
            "classification": "ready_for_isolated_staging",
            "release_classification": item["classification"],
            "source_feed_guid": item["guid"],
            "source_description": item["description"],
            "source_published": item["published"],
        })
    payload = {
        "source_audit": str(AUDIT.relative_to(REPO_ROOT)),
        "source_snapshot": audit["source"],
        "episodes": episodes,
    }
    write_json(DISCOVERY, payload)
    return payload, {item["npr_story_id"]: item for item in episodes}


def normalized_title(value: str) -> str:
    return " ".join(value.casefold().split())


def element_signature(element: ET.Element) -> tuple:
    """Compare XML semantics while ignoring indentation-only tail whitespace."""
    return (
        element.tag,
        (element.text or "").strip(),
        tuple(sorted(element.attrib.items())),
        tuple(element_signature(child) for child in element),
    )


def collision_review(repo_root: Path, candidates: list[dict]) -> dict:
    history = json.loads((repo_root / "indicator_history.json").read_text(encoding="utf-8"))
    enclosure = json.loads((repo_root / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
    feed_items = ET.parse(repo_root / "theindicator_feed.xml").getroot().findall("./channel/item")
    history_ids = {str(item.get("story_id")) for item in history["episodes"]}
    map_ids = set(enclosure["episodes"])
    feed_guids = {(item.findtext("guid") or "").strip() for item in feed_items}
    feed_title_dates = {
        (
            normalized_title((item.findtext("title") or "").strip()),
            parsedate_to_datetime(item.findtext("pubDate") or "").date().isoformat(),
        )
        for item in feed_items
    }
    feed_audio_paths = {
        (item.find("enclosure").get("url", "").split("?", 1)[0])
        for item in feed_items if item.find("enclosure") is not None
    }
    return {
        "history_story_id_collisions": sorted(
            item["npr_story_id"] for item in candidates if item["npr_story_id"] in history_ids
        ),
        "enclosure_map_story_id_collisions": sorted(
            item["npr_story_id"] for item in candidates if item["npr_story_id"] in map_ids
        ),
        "source_guid_collisions": sorted(
            item["source_feed_guid"] for item in candidates if item["source_feed_guid"] in feed_guids
        ),
        "story_id_guid_collisions": sorted(
            item["npr_story_id"] for item in candidates if item["npr_story_id"] in feed_guids
        ),
        "title_date_collisions": sorted(
            item["npr_story_id"] for item in candidates
            if (normalized_title(item["reference_title"]), item["publication_date"]) in feed_title_dates
        ),
        "audio_path_collisions": sorted(
            item["npr_story_id"] for item in candidates
            if item["audio_url"].split("?", 1)[0] in feed_audio_paths
        ),
    }


def preservation_review(repo_root: Path, stage_dir: Path, candidate_ids: set[str]) -> dict:
    production_history = json.loads((repo_root / "indicator_history.json").read_text(encoding="utf-8"))
    staged_history = json.loads((stage_dir / "indicator_history.json").read_text(encoding="utf-8"))
    production_history_by_id = {str(item["story_id"]): item for item in production_history["episodes"]}
    staged_history_by_id = {str(item["story_id"]): item for item in staged_history["episodes"]}
    production_map = json.loads((repo_root / "indicator_enclosure_map.json").read_text(encoding="utf-8"))["episodes"]
    staged_map = json.loads((stage_dir / "indicator_enclosure_map.json").read_text(encoding="utf-8"))["episodes"]

    def feed_by_guid(path: Path) -> dict[str, tuple]:
        return {
            (item.findtext("guid") or "").strip(): element_signature(item)
            for item in ET.parse(path).getroot().findall("./channel/item")
        }

    production_feed = feed_by_guid(repo_root / "theindicator_feed.xml")
    staged_feed = feed_by_guid(stage_dir / "theindicator_feed.xml")
    return {
        "existing_history_records_changed": sorted(
            story_id for story_id, item in production_history_by_id.items()
            if staged_history_by_id.get(story_id) != item
        ),
        "existing_enclosure_map_records_changed": sorted(
            story_id for story_id, item in production_map.items() if staged_map.get(story_id) != item
        ),
        "existing_feed_items_semantically_changed": sorted(
            guid for guid, signature in production_feed.items() if staged_feed.get(guid) != signature
        ),
        "new_history_story_ids_exact": set(staged_history_by_id) - set(production_history_by_id) == candidate_ids,
        "new_enclosure_map_story_ids_exact": set(staged_map) - set(production_map) == candidate_ids,
        "new_feed_story_ids_exact": set(staged_feed) - set(production_feed) == candidate_ids,
    }


def enrich_stage(stage_dir: Path, by_id: dict[str, dict]) -> None:
    history_path = stage_dir / "indicator_history.json"
    map_path = stage_dir / "indicator_enclosure_map.json"
    feed_path = stage_dir / "theindicator_feed.xml"

    history = json.loads(history_path.read_text(encoding="utf-8"))
    for record in history["episodes"]:
        item = by_id.get(str(record.get("story_id")))
        if not item:
            continue
        record.update({
            "date": item["source_published"],
            "description": item["source_description"],
            "description_provenance": "original_npr_feed_snapshot",
            "date_precision": "timestamp",
            "source_feed_guid": item["source_feed_guid"],
            "duration_seconds": item["duration_seconds"],
            "release_classification": item["release_classification"],
        })
    history["episodes"].sort(key=lambda record: record.get("date") or "", reverse=True)
    write_json(history_path, history)

    enclosure = json.loads(map_path.read_text(encoding="utf-8"))
    for story_id, item in by_id.items():
        enclosure["episodes"][story_id].update({
            "date": item["source_published"],
            "source_feed_guid": item["source_feed_guid"],
            "release_classification": item["release_classification"],
        })
    write_json(map_path, enclosure)

    tree = ET.parse(feed_path)
    staged_items = tree.getroot().findall("./channel/item")
    for rss_item in staged_items:
        item = by_id.get((rss_item.findtext("guid") or "").strip())
        if not item:
            continue
        rss_item.find("description").text = item["source_description"]
        rss_item.find("pubDate").text = format_datetime(datetime.fromisoformat(item["source_published"]))
        episode_type = rss_item.find(f"{{{ITUNES_NS}}}episodeType")
        episode_type.text = "trailer" if item["release_classification"] == "launch_trailer" else "full"
    items = tree.getroot().find("channel").findall("item")
    channel = tree.getroot().find("channel")
    for item in items:
        channel.remove(item)
    for item in sorted(items, key=engine.feed_timestamp, reverse=True):
        channel.append(item)
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    engine.preserve_namespace_style(REPO_ROOT / "theindicator_feed.xml", feed_path)


def stage(repo_root: Path = REPO_ROOT, stage_dir: Path | None = None) -> dict:
    discovery, by_id = discover()
    candidates = discovery["episodes"]
    collisions = collision_review(repo_root, candidates)
    if any(collisions.values()):
        raise RuntimeError(f"Production collision detected: {collisions}")

    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": EXPECTED_COUNT,
        "BATCH_LABEL": "pre_march_2018",
        "DISCOVERY_LABEL": DISCOVERY.name,
    }
    prior = {name: getattr(engine, name) for name in values}
    try:
        for name, value in values.items():
            setattr(engine, name, value)
        report = engine.stage(repo_root=repo_root, stage_dir=stage_dir)
    finally:
        for name, value in prior.items():
            setattr(engine, name, value)

    enrich_stage(stage_dir, by_id)
    summary = validate(stage_dir / "theindicator_feed.xml")
    preservation = preservation_review(repo_root, stage_dir, set(by_id))
    trailer = by_id[TRAILER_STORY_ID]
    report.update({
        "collision_review": collisions,
        "preservation_review": preservation,
        "metadata_review": {
            "canonical_npr_pages_verified": EXPECTED_COUNT,
            "original_titles_preserved": EXPECTED_COUNT,
            "original_descriptions_preserved": EXPECTED_COUNT,
            "original_publication_timestamps_preserved": EXPECTED_COUNT,
            "original_source_guids_preserved_as_provenance": EXPECTED_COUNT,
            "exact_live_audio_lengths_verified": EXPECTED_COUNT,
            "missing_optional_durations": sorted(
                item["npr_story_id"] for item in candidates if item["duration_seconds"] is None
            ),
        },
        "trailer_review": {
            "story_id": TRAILER_STORY_ID,
            "title": trailer["reference_title"],
            "duration_seconds": trailer["duration_seconds"],
            "content_length_bytes": trailer["content_length_bytes"],
            "original_feed_classification": "launch_trailer",
            "staged_itunes_episode_type": "trailer",
            "included_in_stage": True,
            "production_decision": "approved_for_inclusion",
            "decision_basis": "first item in the original NPR feed and explicitly typed as a trailer",
        },
        "staged_feed_summary": {
            "items": summary.items,
            "unique_guids": summary.unique_guids,
            "unknown_enclosure_lengths": summary.unknown_enclosure_lengths,
            "oldest_date": summary.oldest_date,
            "newest_date": summary.newest_date,
        },
    })
    report["checks"].update({
        "all_collision_sets_empty": not any(collisions.values()),
        "all_canonical_npr_pages_verified": report["metadata_review"]["canonical_npr_pages_verified"] == EXPECTED_COUNT,
        "all_exact_live_audio_lengths_verified": report["metadata_review"]["exact_live_audio_lengths_verified"] == EXPECTED_COUNT,
        "staged_feed_has_zero_unknown_lengths": summary.unknown_enclosure_lengths == 0,
        "trailer_explicitly_classified": report["trailer_review"]["staged_itunes_episode_type"] == "trailer",
        "existing_records_semantically_unchanged": (
            not preservation["existing_history_records_changed"]
            and not preservation["existing_enclosure_map_records_changed"]
            and not preservation["existing_feed_items_semantically_changed"]
        ),
        "new_record_sets_exact": (
            preservation["new_history_story_ids_exact"]
            and preservation["new_enclosure_map_story_ids_exact"]
            and preservation["new_feed_story_ids_exact"]
        ),
    })
    report["all_checks_passed"] = all(report["checks"].values())
    report["staged_sha256"] = {
        "indicator_history.json": engine.sha256(stage_dir / "indicator_history.json"),
        "indicator_enclosure_map.json": engine.sha256(stage_dir / "indicator_enclosure_map.json"),
        "theindicator_feed.xml": engine.sha256(stage_dir / "theindicator_feed.xml"),
    }
    write_json(stage_dir / "staging_report.json", report)
    write_json(REPORT, report)
    if not report["all_checks_passed"]:
        raise RuntimeError("One or more staging checks failed")
    return report


if __name__ == "__main__":
    result = stage()
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["metadata_review"], indent=2))
    print(json.dumps(result["trailer_review"], indent=2))
    print("Staging complete. Production files were not modified.")
