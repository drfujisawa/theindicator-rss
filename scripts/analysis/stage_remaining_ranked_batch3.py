#!/usr/bin/env python3
"""Stage the four confirmed episodes from ranked batch 3 without production writes."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_remaining_ranked_batch2 as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_remaining_31_ranked_batch3.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/remaining-ranked-batch3-staging"
REPORT = REPO_ROOT / "data/audits/indicator_remaining_ranked_batch3_staging_report.json"
write_json = engine.write_json


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": 4,
        "BATCH_LABEL": "batch3",
        "DISCOVERY_LABEL": "indicator_remaining_31_ranked_batch3.json",
    }
    prior = {name: getattr(engine, name) for name in values}
    try:
        for name, value in values.items():
            setattr(engine, name, value)
        return engine.stage(repo_root=repo_root, stage_dir=stage_dir)
    finally:
        for name, value in prior.items():
            setattr(engine, name, value)


if __name__ == "__main__":
    result = stage()
    write_json(REPORT, result)
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["checks"], indent=2))
    print("Staging complete. Production files were not modified.")
