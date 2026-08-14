#!/usr/bin/env python3
"""Atomically apply the reviewed calculator original and rebroadcast."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.build_early_promotion_dry_run import production_hashes  # noqa: E402
from scripts.maintenance.apply_two_recovered_episodes import (  # noqa: E402
    PRODUCTION_FILES,
    sha256,
    validate,
    write_json,
)

DEFAULT_STAGE_DIR = REPO_ROOT / "work/calculator-releases-staging"
STAGING_REPORT = REPO_ROOT / "data/audits/indicator_calculator_releases_staging_report.json"
APPLICATION_REPORT = REPO_ROOT / "data/audits/indicator_calculator_releases_application_report.json"


def apply(
    repo_root: Path = REPO_ROOT,
    stage_dir: Path | None = None,
    staging_report_path: Path | None = None,
) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    staging_report_path = staging_report_path or (repo_root / STAGING_REPORT.relative_to(REPO_ROOT))
    report = json.loads(staging_report_path.read_text(encoding="utf-8"))
    if report.get("mode") != "staging_only" or not report.get("all_checks_passed"):
        raise RuntimeError("Calculator staging report is not approved for application.")
    required_ids = set(report.get("candidate_story_ids", []))
    if required_ids != {"592961699", "642675050"}:
        raise RuntimeError("Staging report does not contain the reviewed calculator release pair.")

    before = production_hashes(repo_root)
    if before != report["production_sha256_before"]:
        raise RuntimeError("Production hashes changed after staging; re-stage before applying.")
    expected_counts = {
        "history": report["counts"]["history_after"],
        "enclosure_map": report["counts"]["enclosure_map_after"],
        "feed": report["counts"]["feed_after"],
    }
    staged_counts = validate(stage_dir, expected_counts, required_ids)
    staged_hashes = {name: sha256(stage_dir / name) for name in PRODUCTION_FILES}

    rollback_dir = repo_root / "work/calculator-releases-rollback" / before["indicator_history.json"][:12]
    rollback_dir.mkdir(parents=True, exist_ok=True)
    for name in PRODUCTION_FILES:
        shutil.copy2(repo_root / name, rollback_dir / name)

    prepared = {}
    for name in PRODUCTION_FILES:
        temp = repo_root / f".{name}.calculator-releases.tmp"
        shutil.copy2(stage_dir / name, temp)
        prepared[name] = temp
    replaced = []
    try:
        for name in PRODUCTION_FILES:
            os.replace(prepared[name], repo_root / name)
            replaced.append(name)
        applied_counts = validate(repo_root, expected_counts, required_ids)
        after = production_hashes(repo_root)
        if after != staged_hashes:
            raise RuntimeError("Post-write hashes do not match reviewed staging artifacts.")
    except Exception:
        for name in replaced:
            restore = repo_root / f".{name}.calculator-releases.rollback.tmp"
            shutil.copy2(rollback_dir / name, restore)
            os.replace(restore, repo_root / name)
        for temp in prepared.values():
            if temp.exists():
                temp.unlink()
        raise

    return {
        "report_version": 1,
        "mode": "production_application",
        "production_files_modified": True,
        "candidate_story_ids": sorted(required_ids),
        "counts": applied_counts,
        "staged_counts": staged_counts,
        "production_sha256_before": before,
        "production_sha256_after": after,
        "reviewed_staged_sha256": staged_hashes,
        "post_write_hashes_match_staging": after == staged_hashes,
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
    report = apply(stage_dir=args.stage_dir)
    write_json(APPLICATION_REPORT, report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
