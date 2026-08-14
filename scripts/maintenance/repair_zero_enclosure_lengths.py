#!/usr/bin/env python3
"""Measure and atomically repair zero-length RSS enclosures."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_FILES = ("indicator_enclosure_map.json", "theindicator_feed.xml")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(target: tuple[str, str, str]) -> dict:
    story_id, title, url = target
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Range": "bytes=0-0",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_range = response.headers.get("Content-Range", "")
        match = re.search(r"/([0-9]+)$", content_range)
        if response.status != 206 or response.headers.get("Content-Type") != "audio/mpeg" or not match:
            raise RuntimeError(f"Invalid range response for {story_id}")
        response.read(1)
    return {
        "story_id": story_id,
        "title": title,
        "content_length": int(match.group(1)),
        "http_status": 206,
        "content_type": "audio/mpeg",
        "content_range": content_range,
    }


def repair(repo_root: Path, output_dir: Path, workers: int = 12) -> dict:
    tree = ET.parse(repo_root / "theindicator_feed.xml")
    items = tree.getroot().findall("./channel/item")
    targets = []
    target_elements = {}
    for item in items:
        enclosure = item.find("enclosure")
        if enclosure is not None and int(enclosure.get("length", "0")) == 0:
            story_id = (item.findtext("guid") or "").strip()
            targets.append((story_id, item.findtext("title") or "", enclosure.get("url", "")))
            target_elements[story_id] = enclosure
    if not targets:
        raise RuntimeError("No zero-length enclosures found.")
    if len(targets) != len(target_elements):
        raise RuntimeError("Zero-length target GUIDs are not unique.")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        measurements = list(pool.map(probe, targets))
    measured = {record["story_id"]: record for record in measurements}
    if set(measured) != set(target_elements) or any(record["content_length"] <= 0 for record in measurements):
        raise RuntimeError("Not every target received a positive measured length.")

    enclosure_map = json.loads((repo_root / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
    missing_map = sorted(set(measured) - set(enclosure_map["episodes"]))
    if missing_map:
        raise RuntimeError(f"Targets absent from enclosure map: {missing_map}")
    for story_id, record in measured.items():
        target_elements[story_id].set("length", str(record["content_length"]))
        enclosure_map["episodes"][story_id]["content_length"] = record["content_length"]

    output_dir.mkdir(parents=True, exist_ok=True)
    map_path = output_dir / "indicator_enclosure_map.json"
    feed_path = output_dir / "theindicator_feed.xml"
    map_path.write_text(json.dumps(enclosure_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)

    reparsed = ET.parse(feed_path).getroot().findall("./channel/item")
    remaining = [item.findtext("guid") for item in reparsed if int(item.find("enclosure").get("length", "0")) == 0]
    if remaining:
        raise RuntimeError(f"Zero-length enclosures remain: {remaining}")
    return {
        "report_version": 1,
        "targets": len(targets),
        "measured": len(measurements),
        "remaining_zero_lengths": 0,
        "measurements": sorted(measurements, key=lambda value: value["story_id"]),
        "staged_sha256": {name: sha256(output_dir / name) for name in PRODUCTION_FILES},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--stage-dir", type=Path, default=REPO_ROOT / "work/zero-length-repair-staging")
    args = parser.parse_args()
    report = repair(REPO_ROOT, args.stage_dir, args.workers)
    if args.apply:
        rollback = args.stage_dir / "rollback"
        rollback.mkdir(parents=True, exist_ok=True)
        try:
            for name in PRODUCTION_FILES:
                shutil.copy2(REPO_ROOT / name, rollback / name)
                shutil.copy2(args.stage_dir / name, REPO_ROOT / name)
            if {name: sha256(REPO_ROOT / name) for name in PRODUCTION_FILES} != report["staged_sha256"]:
                raise RuntimeError("Post-write hashes do not match staged files.")
        except Exception:
            for name in PRODUCTION_FILES:
                backup = rollback / name
                if backup.exists():
                    shutil.copy2(backup, REPO_ROOT / name)
            raise
        report["production_applied"] = True
        report["rollback_directory"] = str(rollback.relative_to(REPO_ROOT))
    else:
        report["production_applied"] = False
    report_path = REPO_ROOT / "data/audits/indicator_zero_length_repair_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("targets", "measured", "remaining_zero_lengths", "production_applied")}, indent=2))


if __name__ == "__main__":
    main()
