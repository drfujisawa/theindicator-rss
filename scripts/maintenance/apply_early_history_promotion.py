#!/usr/bin/env python3
"""Atomically apply the reviewed early-history staging artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.build_early_promotion_dry_run import production_hashes  # noqa: E402


PRODUCTION_FILES = (
    "indicator_history.json",
    "indicator_enclosure_map.json",
    "theindicator_feed.xml",
)
DEFAULT_STAGE_DIR = REPO_ROOT / "work" / "early-promotion-staging"
STAGING_REPORT = (
    REPO_ROOT / "data" / "audits" / "indicator_early_promotion_staging_report.json"
)
APPLICATION_REPORT = (
    REPO_ROOT / "data" / "audits" / "indicator_early_promotion_application_report.json"
)


def validate_artifacts(directory: Path) -> dict[str, int]:
    history = json.loads((directory / "indicator_history.json").read_text(encoding="utf-8"))
    enclosure = json.loads(
        (directory / "indicator_enclosure_map.json").read_text(encoding="utf-8")
    )
    items = ET.parse(directory / "theindicator_feed.xml").getroot().findall(".//item")
    counts = {
        "history": len(history.get("episodes", [])),
        "enclosure_map": len(enclosure.get("episodes", {})),
        "feed": len(items),
    }
    if history.get("episode_count") != counts["history"]:
        raise RuntimeError("History episode_count does not match its episode list.")
    if counts != {"history": 1706, "enclosure_map": 1706, "feed": 2007}:
        raise RuntimeError(f"Unexpected staged counts: {counts}")
    return counts


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def apply_promotion(
    repo_root: Path = REPO_ROOT,
    stage_dir: Path | None = None,
    staging_report_path: Path | None = None,
) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    staging_report_path = staging_report_path or (
        repo_root / STAGING_REPORT.relative_to(REPO_ROOT)
    )
    staging_report = json.loads(staging_report_path.read_text(encoding="utf-8"))
    if staging_report.get("mode") != "simulation_only" or not staging_report.get("all_checks_passed"):
        raise RuntimeError("The reviewed staging report is not approved for application.")

    expected_before = staging_report["production_sha256_before"]
    actual_before = production_hashes(repo_root)
    if actual_before != expected_before:
        raise RuntimeError(
            "Production hashes changed after review. Re-stage and review before applying."
        )
    staged_counts = validate_artifacts(stage_dir)
    expected_staged = staging_report["staged_sha256"]
    actual_staged = {
        name: __import__("hashlib").sha256((stage_dir / name).read_bytes()).hexdigest()
        for name in PRODUCTION_FILES
    }
    if actual_staged != expected_staged:
        raise RuntimeError("Staged artifacts changed after review.")

    rollback_dir = repo_root / "work" / "early-promotion-rollback" / actual_before[
        "indicator_history.json"
    ][:12]
    rollback_dir.mkdir(parents=True, exist_ok=True)
    for name in PRODUCTION_FILES:
        shutil.copy2(repo_root / name, rollback_dir / name)

    prepared = {}
    for name in PRODUCTION_FILES:
        temp_path = repo_root / f".{name}.early-promotion.tmp"
        shutil.copy2(stage_dir / name, temp_path)
        prepared[name] = temp_path

    replaced = []
    try:
        for name in PRODUCTION_FILES:
            os.replace(prepared[name], repo_root / name)
            replaced.append(name)
        applied_counts = validate_artifacts(repo_root)
        actual_after = production_hashes(repo_root)
        if actual_after != expected_staged:
            raise RuntimeError("Post-write hashes do not match reviewed staging hashes.")
    except Exception:
        for name in replaced:
            restore_temp = repo_root / f".{name}.early-promotion.rollback.tmp"
            shutil.copy2(rollback_dir / name, restore_temp)
            os.replace(restore_temp, repo_root / name)
        for temp_path in prepared.values():
            if temp_path.exists():
                temp_path.unlink()
        raise

    return {
        "report_version": 1,
        "mode": "production_application",
        "production_files_modified": True,
        "counts": applied_counts,
        "production_sha256_before": actual_before,
        "production_sha256_after": actual_after,
        "reviewed_staged_sha256": expected_staged,
        "post_write_hashes_match_staging": actual_after == expected_staged,
        "rollback_directory": str(rollback_dir.relative_to(repo_root)),
        "rollback_files_retained": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Required acknowledgement that production files will be replaced.",
    )
    parser.add_argument("--stage-dir", type=Path, default=DEFAULT_STAGE_DIR)
    args = parser.parse_args()
    if not args.apply:
        parser.error("Refusing to write production without --apply")
    report = apply_promotion(stage_dir=args.stage_dir)
    write_json(APPLICATION_REPORT, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
