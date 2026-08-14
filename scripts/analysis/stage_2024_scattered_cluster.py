#!/usr/bin/env python3
"""Discover and stage five confirmed scattered 2024 omissions."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2024_scattered_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2024-scattered-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2024_scattered_cluster_staging_report.json"
EPISODES = [
    ("2024-04-03", "Can an old law bring down grocery prices?", "1197963467", "can-an-old-law-bring-down-grocery-prices", "https://podcasts.apple.com/us/podcast/can-an-old-law-bring-down-grocery-prices/id1320118593?i=1000651358256", "https://www.npr.org/2024/03/29/1197963467/can-an-old-law-bring-down-grocery-prices"),
    ("2024-05-10", "A new gold rush and other indicators", "1197964551", "a-new-gold-rush-and-other-indicators", "https://podcasts.apple.com/xk/podcast/a-new-gold-rush-and-other-indicators/id1320118593?i=1000655219113"),
    ("2024-05-17", "Trade wars and talent shortages", "1197964709", "trade-wars-and-talent-shortages", "https://www.northcountrypublicradio.org/news/npr/1197964709/trade-wars-and-talent-shortages"),
    ("2024-05-31", "The cutest indicator in the world", "1197965006", "the-cutest-indicator-in-the-world", "https://podcasts.apple.com/us/podcast/the-cutest-indicator-in-the-world/id1320118593?i=1000657457333"),
    ("2024-08-06", "Markets have a bad case of the Mondays", "1197967993", "markets-have-a-bad-case-of-the-mondays", "https://www.podchaser.com/podcasts/the-indicator-from-planet-mone-595555/episodes/markets-have-a-bad-case-of-the-219321939"),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": 5,
        "EPISODES": EPISODES,
        "BATCH_LABEL": "2024_scattered_cluster",
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
