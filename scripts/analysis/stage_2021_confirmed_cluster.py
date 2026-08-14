#!/usr/bin/env python3
"""Discover and stage two independently confirmed 2021 omissions."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2021_confirmed_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2021-confirmed-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2021_confirmed_cluster_staging_report.json"
EPISODES = [
    (
        "2021-04-14",
        "What McDonald's Tells Us About The Minimum Wage",
        "987341168",
        "what-mcdonalds-tells-us-about-the-minimum-wage",
        "https://www.podchaser.com/podcasts/the-indicator-from-planet-mone-595555/episodes/what-mcdonalds-tells-us-about-89262186/reviews/60762",
        "https://www.npr.org/2021/04/14/987341168/what-mcdonalds-tells-us-about-the-minimum-wage",
        "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2021/04/20210414_indicator_2104xx_mcdonalds_min_wage_upload.mp3?awCollectionId=510325&awEpisodeId=987341168&orgId=1&topicId=1017&aggIds=94427042&d=567&p=510325&story=987341168&t=podcast&e=987341168&size=9076071&ft=pod&f=510325",
    ),
    (
        "2021-08-03",
        "The Time the US Paid Off All Its Debt",
        "1024401554",
        "the-time-the-us-paid-off-all-its-debt",
        "https://www.podchaser.com/podcasts/the-indicator-from-planet-mone-595555/episodes/the-time-the-us-paid-off-all-i-96146916",
        "https://www.npr.org/2021/08/03/1024401554/the-time-the-us-paid-off-all-its-debt",
        "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2021/08/20210803_indicator_andrew_jackson_ready_to_publish.mp3?awCollectionId=510325&awEpisodeId=1024401554&orgId=1&topicId=1006&d=597&p=510325&story=1024401554&t=podcast&e=1024401554&size=9568427&ft=pod&f=510325",
    ),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": len(EPISODES),
        "EPISODES": EPISODES,
        "BATCH_LABEL": "2021_confirmed_cluster",
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
