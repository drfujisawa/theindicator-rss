#!/usr/bin/env python3
"""Atomically apply the reviewed final ten-episode recovery staging set."""

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.maintenance import apply_remaining_ranked_batch2 as engine

REPO_ROOT = engine.REPO_ROOT
DEFAULT_STAGE_DIR = REPO_ROOT / "work/final-10-recovered-staging"
STAGING_REPORT = REPO_ROOT / "data/audits/indicator_final_10_staging_report.json"
APPLICATION_REPORT = REPO_ROOT / "data/audits/indicator_final_10_application_report.json"
REQUIRED_IDS = {
    "627818329", "628548830", "628896537", "629858218", "630639437",
    "661827210", "666385019", "675828921", "688905589", "707396339",
}
write_json = engine.write_json


def apply(repo_root=REPO_ROOT, stage_dir=None, staging_report_path=None):
    values = {
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "STAGING_REPORT": STAGING_REPORT,
        "APPLICATION_REPORT": APPLICATION_REPORT,
        "EXPECTED_COUNT": 10,
        "REQUIRED_IDS": REQUIRED_IDS,
        "BATCH_LABEL": "final10",
    }
    prior = {name: getattr(engine, name) for name in values}
    try:
        for name, value in values.items():
            setattr(engine, name, value)
        return engine.apply(
            repo_root=repo_root,
            stage_dir=stage_dir,
            staging_report_path=staging_report_path,
        )
    finally:
        for name, value in prior.items():
            setattr(engine, name, value)


def main():
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
