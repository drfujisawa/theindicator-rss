#!/usr/bin/env python3
"""Discover and stage four confirmed scattered 2025 omissions."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2025_scattered_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2025-scattered-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2025_scattered_cluster_staging_report.json"
ARCHIVE = "https://web.archive.org/web/20250531000314id_/https://feeds.npr.org/510325/podcast.xml"
EPISODES = [
    ("2025-02-26", "A polite message from Canada to the U.S.", "1233894751", "a-polite-message-from-canada-to-the-us", ARCHIVE),
    ("2025-04-29", "Is the US pushing countries towards China?", "1247777247", "pakistan-us-china-trade-tariffs-aid", ARCHIVE),
    ("2025-05-30", "Let's 'TACO' 'bout General Motors gassing up V-8s and golden shares", "1253382246", "trump-taco-gm-v8-golden-change-nippon-steel-tariffs", ARCHIVE),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": len(EPISODES),
        "EPISODES": EPISODES,
        "BATCH_LABEL": "2025_scattered_cluster",
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

    stage_dir = stage_dir or DEFAULT_STAGE_DIR
    history_path = stage_dir / "indicator_history.json"
    feed_path = stage_dir / "theindicator_feed.xml"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    history_matches = [item for item in history["episodes"] if str(item.get("story_id")) == "1250192002"]
    if len(history_matches) != 1:
        raise RuntimeError("Expected one existing 1250192002 history record.")
    history_matches[0]["title"] = "What's in YOUR wallet?"
    engine.engine.write_json(history_path, history)

    tree = ET.parse(feed_path)
    feed_matches = [item for item in tree.getroot().findall(".//item") if (item.findtext("guid") or "").strip() == "1250192002"]
    if len(feed_matches) != 1:
        raise RuntimeError("Expected one existing 1250192002 feed item.")
    feed_item = feed_matches[0]
    feed_item.find("title").text = "What's in YOUR wallet?"
    itunes_title = feed_item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}title")
    if itunes_title is not None:
        itunes_title.text = "What's in YOUR wallet?"
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    engine.engine.preserve_namespace_style(repo_root / "theindicator_feed.xml", feed_path)

    report["metadata_corrections"] = [{
        "story_id": "1250192002",
        "old_title": "Prepping for a rainy day and higher used car prices",
        "new_title": "What's in YOUR wallet?",
        "fields": ["title", "itunes:title"],
    }]
    report["checks"]["existing_1250192002_metadata_corrected"] = True
    report["all_checks_passed"] = all(report["checks"].values())
    report["staged_sha256"]["indicator_history.json"] = engine.engine.sha256(history_path)
    report["staged_sha256"]["theindicator_feed.xml"] = engine.engine.sha256(feed_path)
    engine.engine.write_json(stage_dir / "staging_report.json", report)
    return report


if __name__ == "__main__":
    result = stage()
    engine.engine.write_json(REPORT, result)
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["checks"], indent=2))
    print("Staging complete. Production files were not modified.")
