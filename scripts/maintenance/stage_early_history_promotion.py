#!/usr/bin/env python3
"""Stage and validate an early-history promotion without production writes.

This command is deliberately simulation-only. It has no apply mode and never
replaces indicator_history.json, indicator_enclosure_map.json, or the RSS feed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.build_early_promotion_dry_run import (  # noqa: E402
    build_dry_run,
    production_hashes,
)
from scripts.analysis.review_early_production_promotion import review  # noqa: E402


DEFAULT_STAGE_DIR = REPO_ROOT / "work" / "early-promotion-staging"
REPORT = REPO_ROOT / "data" / "audits" / "indicator_early_promotion_staging_report.json"
PRODUCTION_FILES = (
    "indicator_history.json",
    "indicator_enclosure_map.json",
    "theindicator_feed.xml",
)


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def stage_promotion(
    repo_root: Path = REPO_ROOT,
    stage_dir: Path | None = None,
) -> dict:
    stage_dir = stage_dir or (repo_root / DEFAULT_STAGE_DIR.relative_to(REPO_ROOT))
    stage_dir.mkdir(parents=True, exist_ok=True)
    before = production_hashes(repo_root)

    dry_report = build_dry_run(repo_root=repo_root, output_dir=stage_dir)
    design_review = review(repo_root=repo_root, dry_dir=stage_dir)

    after = production_hashes(repo_root)
    staged_hashes = {
        name: __import__("hashlib").sha256((stage_dir / name).read_bytes()).hexdigest()
        for name in PRODUCTION_FILES
    }
    checks = {
        "production_hashes_unchanged": before == after,
        "dry_run_checks_passed": dry_report["all_checks_passed"],
        "design_checks_passed": all(design_review["checks"].values()),
        "staged_files_all_exist": all((stage_dir / name).is_file() for name in PRODUCTION_FILES),
        "staged_files_differ_from_production": all(
            staged_hashes[name] != before[name] for name in PRODUCTION_FILES
        ),
        "candidate_count_is_226": dry_report["counts"]["promoted_candidates"] == 226,
        "command_has_no_apply_mode": True,
    }
    if not checks["production_hashes_unchanged"]:
        raise RuntimeError("Production changed during staging; aborting.")

    report = {
        "report_version": 1,
        "mode": "simulation_only",
        "production_files_modified": False,
        "stage_directory": str(stage_dir.relative_to(repo_root)),
        "publication_time_policy": {
            "status": "adopted_for_simulation",
            "verified_precision": "date_only",
            "rss_serialization": "00:00:00 UTC",
            "meaning": (
                "Serialization convention required by RSS; not claimed as the NPR publication time."
            ),
            "history_marker": "date_precision=date_only",
        },
        "counts": dry_report["counts"],
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "production_sha256_before": before,
        "production_sha256_after": after,
        "staged_sha256": staged_hashes,
        "proposed_transaction": [
            {
                "phase": 1,
                "action": "verify production hashes equal the reviewed baseline",
                "implemented_in_simulation": True,
            },
            {
                "phase": 2,
                "action": "build and validate all candidate files outside production",
                "implemented_in_simulation": True,
            },
            {
                "phase": 3,
                "action": "replace all three production files as one guarded operation",
                "implemented_in_simulation": False,
            },
            {
                "phase": 4,
                "action": "run post-write validation and retain rollback hashes",
                "implemented_in_simulation": False,
            },
        ],
        "next_gate": (
            "A separate production-capable command may be implemented only after reviewing "
            "this staging report; this simulation cannot write production."
        ),
    }
    write_json(stage_dir / "promotion_plan.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage the early-history promotion without production writes."
    )
    parser.add_argument("--stage-dir", type=Path, default=DEFAULT_STAGE_DIR)
    args = parser.parse_args()
    report = stage_promotion(stage_dir=args.stage_dir)
    write_json(REPORT, report)
    print(json.dumps(report["counts"], indent=2))
    print(json.dumps(report["checks"], indent=2))
    print(f"Staged files: {args.stage_dir}")
    print("Simulation complete. Production files were not modified.")


if __name__ == "__main__":
    main()
