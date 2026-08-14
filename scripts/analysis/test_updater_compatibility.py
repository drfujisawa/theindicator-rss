#!/usr/bin/env python3
"""Run the scheduled updater in isolation and verify archive preservation."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FEED = REPO_ROOT / "theindicator_feed.xml"
UPDATER = REPO_ROOT / "theindicator_rss.py"
STAGE_DIR = REPO_ROOT / "work/updater-compatibility-test"
REPORT = REPO_ROOT / "data/audits/indicator_updater_compatibility_report.json"
AUDIT = REPO_ROOT / "data/audits/indicator_pre_march_2018_catalog_audit.json"
OFFICIAL_FEED = "https://feeds.npr.org/510325/podcast.xml"
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature(element: ET.Element) -> tuple:
    return (
        element.tag,
        (element.text or "").strip(),
        tuple(sorted(element.attrib.items())),
        tuple(signature(child) for child in element),
    )


def by_guid(path: Path) -> dict[str, ET.Element]:
    items = ET.parse(path).getroot().findall("./channel/item")
    return {(item.findtext("guid") or "").strip(): item for item in items}


def official_guids() -> set[str]:
    request = urllib.request.Request(
        OFFICIAL_FEED,
        headers={"User-Agent": "Mozilla/5.0 theindicator-rss compatibility test"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        root = ET.fromstring(response.read())
    return {
        (item.findtext("guid") or "").strip()
        for item in root.findall("./channel/item")
        if (item.findtext("guid") or "").strip()
    }


def main() -> int:
    production_hash_before = sha256(PRODUCTION_FEED)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    isolated_feed = STAGE_DIR / "theindicator_feed.xml"
    isolated_updater = STAGE_DIR / "theindicator_rss.py"
    shutil.copy2(PRODUCTION_FEED, isolated_feed)
    shutil.copy2(UPDATER, isolated_updater)

    before = by_guid(isolated_feed)
    launch_ids = {
        item["story_id"]
        for item in json.loads(AUDIT.read_text(encoding="utf-8"))["items"]
    }
    official_ids = official_guids()
    result = subprocess.run(
        [sys.executable, isolated_updater.name],
        cwd=STAGE_DIR,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise RuntimeError(f"Isolated updater failed:\n{result.stdout}\n{result.stderr}")

    after = by_guid(isolated_feed)
    added_ids = set(after) - set(before)
    removed_ids = set(before) - set(after)
    changed_ids = sorted(
        guid for guid in set(before) & set(after)
        if signature(before[guid]) != signature(after[guid])
    )
    trailer = after["567752488"]
    lengths = [int(item.find("enclosure").get("length", "0")) for item in after.values()]
    checks = {
        "production_feed_unchanged": production_hash_before == sha256(PRODUCTION_FEED),
        "updater_exit_code_zero": result.returncode == 0,
        "no_existing_guids_removed": not removed_ids,
        "no_existing_items_semantically_changed": not changed_ids,
        "all_59_launch_items_preserved": launch_ids <= set(after),
        "trailer_preserved": (trailer.findtext("title") or "").strip() == "Coming Soon",
        "trailer_type_preserved": trailer.findtext(f"{{{ITUNES_NS}}}episodeType") == "trailer",
        "trailer_timestamp_preserved": (trailer.findtext("pubDate") or "").strip() == "Fri, 01 Dec 2017 12:13:00 -0500",
        "trailer_length_preserved": trailer.find("enclosure").get("length") == "967085",
        "all_enclosure_lengths_positive": all(length > 0 for length in lengths),
        "all_added_guids_from_current_npr_feed": added_ids <= official_ids,
        "all_output_guids_unique": len(after) == len(ET.parse(isolated_feed).getroot().findall("./channel/item")),
    }
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "isolated_updater_compatibility_test",
        "production_files_modified": False,
        "production_feed_sha256_before": production_hash_before,
        "production_feed_sha256_after": sha256(PRODUCTION_FEED),
        "isolated_feed_sha256_after": sha256(isolated_feed),
        "counts": {
            "before": len(before),
            "after": len(after),
            "added": len(added_ids),
            "removed": len(removed_ids),
            "semantically_changed_existing_items": len(changed_ids),
            "launch_items_preserved": len(launch_ids & set(after)),
            "unknown_or_zero_lengths_after": sum(length <= 0 for length in lengths),
        },
        "added_guids": sorted(added_ids),
        "removed_guids": sorted(removed_ids),
        "changed_existing_guids": changed_ids,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "updater_stdout": result.stdout.strip(),
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))
    print(json.dumps(checks, indent=2))
    print(f"Report: {REPORT}")
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
