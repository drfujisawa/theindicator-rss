#!/usr/bin/env python3
"""Discover and stage six 2023/early-2024 omissions and one title correction."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2023_early2024_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2023-early2024-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2023_early2024_cluster_staging_report.json"
ARCHIVE_2023 = "https://web.archive.org/web/20231231012458id_/https://feeds.npr.org/510325/podcast.xml"
ARCHIVE_2024 = "https://web.archive.org/web/20240115000000id_/https://feeds.npr.org/510325/podcast.xml"
EPISODES = [
    ("2023-01-30", "Artists vs. AI", "1152653269", "artists-vs-ai", ARCHIVE_2023),
    ("2023-03-20", "The demise of Credit Suisse", "1164823375", "the-demise-of-credit-suisse", ARCHIVE_2023),
    ("2023-07-12", "What's behind the China deflation scare", "1187372320", "whats-behind-the-china-deflation-scare", ARCHIVE_2023),
    ("2023-09-20", "The rat under the Fed's hat", "1197954200", "the-indicator-how-the-fed-controls-interest-rates", ARCHIVE_2023),
    ("2023-09-25", "Is the Canada, Meta news standoff coming to the US?", "1197954327", "the-indicator-from-planet-money-canada-vs-meta-fight", ARCHIVE_2023),
    ("2024-01-12", "Offloading EVs, vacating offices and reaping windfalls", "1197961087", "hertz-evs-office-vacancies-iphone-settlement", ARCHIVE_2024),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": len(EPISODES),
        "EPISODES": EPISODES,
        "BATCH_LABEL": "2023_early2024_cluster",
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
    history_matches = [item for item in history["episodes"] if str(item.get("story_id")) == "1197958421"]
    if len(history_matches) != 1:
        raise RuntimeError("Expected one existing 1197958421 history record.")
    history_matches[0]["title"] = 'How "dark defaults" could cost you'
    engine.engine.write_json(history_path, history)

    tree = ET.parse(feed_path)
    feed_matches = [item for item in tree.getroot().findall(".//item") if (item.findtext("guid") or "").strip() == "1197958421"]
    if len(feed_matches) != 1:
        raise RuntimeError("Expected one existing 1197958421 feed item.")
    feed_item = feed_matches[0]
    feed_item.find("title").text = 'How "dark defaults" could cost you'
    itunes_title = feed_item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}title")
    if itunes_title is not None:
        itunes_title.text = 'How "dark defaults" could cost you'
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    engine.engine.preserve_namespace_style(repo_root / "theindicator_feed.xml", feed_path)

    report["metadata_corrections"] = [{
        "story_id": "1197958421",
        "old_title": "How political campaigns raise millions through unwitting donors",
        "new_title": 'How "dark defaults" could cost you',
        "fields": ["title", "itunes:title"],
    }]
    report["checks"]["existing_1197958421_metadata_corrected"] = True
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
