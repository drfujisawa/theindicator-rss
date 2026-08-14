#!/usr/bin/env python3
"""Discover and stage four independently confirmed 2022 omissions."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2022_confirmed_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2022-confirmed-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2022_confirmed_cluster_staging_report.json"
EPISODES = [
    ("2022-04-08", "Inflation indicators: Fed chatter, global inflation and used cars", "1091790773", "inflation-indicators-fed-chatter-global-inflation-and-used-cars", "https://www.podchaser.com/podcasts/the-indicator-from-planet-mone-595555/episodes/inflation-indicators-fed-chatt-133237561"),
    ("2022-05-19", "Economists weigh in on the abortion debate", "1100277520", "economists-weigh-in-on-the-abortion-debate", "https://cynthiamulcahy.com/abortion-bouquet-project.html"),
    ("2022-05-26", "The pros and cons of a strong dollar", "1101600846", "the-pros-and-cons-of-a-strong-dollar", "https://podcasts.apple.com/xk/podcast/the-pros-and-cons-of-a-strong-dollar/id1320118593?i=1000564098217"),
    ("2022-08-24", "SCOTUS: de facto pro-business?", "1119307426", "scotus-de-facto-pro-business", "https://www.fixdemocracyfirst.org/events/democracy-happy-hour-august-31-2022"),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": len(EPISODES),
        "EPISODES": EPISODES,
        "BATCH_LABEL": "2022_confirmed_cluster",
        "DISCOVERY_LABEL": DISCOVERY.name,
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
    engine.engine.write_json(REPORT, result)
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["checks"], indent=2))
    print("Staging complete. Production files were not modified.")
