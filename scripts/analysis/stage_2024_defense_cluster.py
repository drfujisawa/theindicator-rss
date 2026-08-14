#!/usr/bin/env python3
"""Stage three independently confirmed 2024 omissions without production writes."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_remaining_ranked_batch2 as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2024_defense_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2024-defense-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2024_defense_cluster_staging_report.json"
write_json = engine.write_json


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": 2,
        "BATCH_LABEL": "2024_defense_cluster",
        "DISCOVERY_LABEL": "indicator_2024_defense_cluster_staging_candidates.json",
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
    history_matches = [item for item in history["episodes"] if str(item.get("story_id")) == "1197961492"]
    if len(history_matches) != 1:
        raise RuntimeError("Expected one existing 1197961492 history record.")
    history_matches[0]["title"] = "Are we overpaying for military equipment?"
    history_matches[0]["npr_url"] = "https://www.npr.org/2024/01/29/1197961492/are-we-overpaying-for-military-equipment"
    engine.write_json(history_path, history)

    tree = ET.parse(feed_path)
    feed_matches = [item for item in tree.getroot().findall(".//item") if (item.findtext("guid") or "").strip() == "1197961492"]
    if len(feed_matches) != 1:
        raise RuntimeError("Expected one existing 1197961492 feed item.")
    feed_item = feed_matches[0]
    feed_item.find("title").text = "Are we overpaying for military equipment?"
    feed_item.find("link").text = "https://www.npr.org/2024/01/29/1197961492/are-we-overpaying-for-military-equipment"
    itunes_title = feed_item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}title")
    if itunes_title is not None:
        itunes_title.text = "Are we overpaying for military equipment?"
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    engine.preserve_namespace_style(repo_root / "theindicator_feed.xml", feed_path)

    report["metadata_corrections"] = [{
        "story_id": "1197961492",
        "old_title": "The Military Industry ... It's Complex",
        "new_title": "Are we overpaying for military equipment?",
        "fields": ["title", "itunes:title", "link"],
    }]
    report["checks"]["existing_1197961492_metadata_corrected"] = True
    report["all_checks_passed"] = all(report["checks"].values())
    report["staged_sha256"]["indicator_history.json"] = engine.sha256(history_path)
    report["staged_sha256"]["theindicator_feed.xml"] = engine.sha256(feed_path)
    engine.write_json(stage_dir / "staging_report.json", report)
    return report


if __name__ == "__main__":
    result = stage()
    write_json(REPORT, result)
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["checks"], indent=2))
    print("Staging complete. Production files were not modified.")
