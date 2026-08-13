#!/usr/bin/env python3
"""Atomically apply the reviewed eight-episode staging artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.audit_early_promotion_candidates import story_id_from_feed_item  # noqa: E402
from scripts.analysis.build_early_promotion_dry_run import production_hashes  # noqa: E402

PRODUCTION_FILES = (
    "indicator_history.json",
    "indicator_enclosure_map.json",
    "theindicator_feed.xml",
)
DEFAULT_STAGE_DIR = REPO_ROOT / "work/early-near-ready-staging"
STAGING_REPORT = REPO_ROOT / "data/audits/indicator_early_near_ready_staging_report.json"
APPLICATION_REPORT = REPO_ROOT / "data/audits/indicator_early_near_ready_application_report.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_artifacts(directory: Path, expected: dict[str, int]) -> dict[str, int]:
    history = json.loads((directory / "indicator_history.json").read_text(encoding="utf-8"))
    enclosure = json.loads((directory / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
    items = ET.parse(directory / "theindicator_feed.xml").getroot().findall(".//item")
    counts = {
        "history": len(history.get("episodes", [])),
        "enclosure_map": len(enclosure.get("episodes", {})),
        "feed": len(items),
    }
    if counts != expected:
        raise RuntimeError(f"Unexpected artifact counts: {counts}; expected {expected}")
    if history.get("episode_count") != counts["history"]:
        raise RuntimeError("History episode_count does not match its episode list.")
    history_ids = [str(item.get("story_id")) for item in history["episodes"]]
    if len(history_ids) != len(set(history_ids)):
        raise RuntimeError("History contains duplicate story IDs.")
    story_ids = [story_id_from_feed_item(item) for item in items]
    story_ids = [item for item in story_ids if item]
    guids = [(item.findtext("guid") or "").strip() for item in items]
    if len(story_ids) != len(set(story_ids)) or any(v > 1 for v in Counter(guids).values()):
        raise RuntimeError("Feed contains duplicate story IDs or GUIDs.")
    dates = [parsedate_to_datetime(item.findtext("pubDate") or "") for item in items]
    if dates != sorted(dates, reverse=True):
        raise RuntimeError("Feed is not sorted newest first.")
    return counts


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def apply_promotion(
    repo_root: Path = REPO_ROOT,
    stage_dir: Path | None = None,
    staging_report_path: Path | None = None,
) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    staging_report_path = staging_report_path or (repo_root / STAGING_REPORT.relative_to(REPO_ROOT))
    report = json.loads(staging_report_path.read_text(encoding="utf-8"))
    if report.get("mode") != "staging_only" or not report.get("all_checks_passed"):
        raise RuntimeError("The staging report is not approved for application.")
    if report.get("counts", {}).get("promoted_candidates") != 8:
        raise RuntimeError("The staging report does not describe the reviewed eight-episode cohort.")

    actual_before = production_hashes(repo_root)
    if actual_before != report["production_sha256_before"]:
        raise RuntimeError("Production hashes changed after staging; re-stage before applying.")
    actual_staged = {name: sha256(stage_dir / name) for name in PRODUCTION_FILES}
    if actual_staged != report["staged_sha256"]:
        raise RuntimeError("Staged artifacts changed after review.")
    expected_counts = {
        "history": report["counts"]["history_after"],
        "enclosure_map": report["counts"]["enclosure_map_after"],
        "feed": report["counts"]["feed_after"],
    }
    staged_counts = validate_artifacts(stage_dir, expected_counts)

    rollback_dir = repo_root / "work/early-near-ready-rollback" / actual_before["indicator_history.json"][:12]
    rollback_dir.mkdir(parents=True, exist_ok=True)
    for name in PRODUCTION_FILES:
        shutil.copy2(repo_root / name, rollback_dir / name)

    prepared = {}
    for name in PRODUCTION_FILES:
        temp_path = repo_root / f".{name}.near-ready.tmp"
        shutil.copy2(stage_dir / name, temp_path)
        prepared[name] = temp_path
    replaced = []
    try:
        for name in PRODUCTION_FILES:
            os.replace(prepared[name], repo_root / name)
            replaced.append(name)
        applied_counts = validate_artifacts(repo_root, expected_counts)
        actual_after = production_hashes(repo_root)
        if actual_after != actual_staged:
            raise RuntimeError("Post-write hashes do not match reviewed staging hashes.")
    except Exception:
        for name in replaced:
            restore = repo_root / f".{name}.near-ready.rollback.tmp"
            shutil.copy2(rollback_dir / name, restore)
            os.replace(restore, repo_root / name)
        for temp_path in prepared.values():
            if temp_path.exists():
                temp_path.unlink()
        raise

    return {
        "report_version": 1,
        "mode": "production_application",
        "production_files_modified": True,
        "counts": applied_counts,
        "reviewed_candidate_count": 8,
        "staged_counts": staged_counts,
        "production_sha256_before": actual_before,
        "production_sha256_after": actual_after,
        "reviewed_staged_sha256": actual_staged,
        "post_write_hashes_match_staging": actual_after == actual_staged,
        "rollback_directory": str(rollback_dir.relative_to(repo_root)),
        "rollback_files_retained": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stage-dir", type=Path, default=DEFAULT_STAGE_DIR)
    args = parser.parse_args()
    if not args.apply:
        parser.error("Refusing to write production without --apply")
    report = apply_promotion(stage_dir=args.stage_dir)
    write_json(APPLICATION_REPORT, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
