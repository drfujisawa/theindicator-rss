#!/usr/bin/env python3
"""Stage the final ten recovered episodes without modifying production artifacts."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_remaining_ranked_batch2 as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_final_10_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/final-10-recovered-staging"
REPORT = REPO_ROOT / "data/audits/indicator_final_10_staging_report.json"
write_json = engine.write_json


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": 10,
        "BATCH_LABEL": "final10",
        "DISCOVERY_LABEL": "indicator_final_10_staging_candidates.json",
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
