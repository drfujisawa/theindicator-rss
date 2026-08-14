#!/usr/bin/env python3
"""Discover and stage 11 confirmed January 2025 omissions."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2025_january_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2025-january-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2025_january_cluster_staging_report.json"
ARCHIVE = "https://web.archive.org/web/20250119170926id_/https://feeds.npr.org/510325/podcast.xml"
EPISODES = [
    ("2025-01-02", "Why to look twice when your portfolio is doing well", "1222474621", "why-to-look-twice-when-your-portfolio-is-doing-well", ARCHIVE),
    ("2025-01-03", "President Jimmy Carter's economic legacy", "1222640171", "president-jimmy-carters-economic-legacy", ARCHIVE),
    ("2025-01-06", "The water mystery unfolding in the western U.S.", "1223291772", "the-water-mystery-unfolding-in-the-western-us", ARCHIVE),
    ("2025-01-07", "Why Netflix spent billions for WWE", "1223358002", "the-indicator-from-planet-money-why-netflix-spent-billions-for-wwe-01-07-2025", ARCHIVE),
    ("2025-01-08", "What's a moneyline bet anyway?", "1223466594", "investopedia-top-financial-terms-2024", ARCHIVE),
    ("2025-01-10", "What's going on with men's labor force participation?", "1223918032", "economy-men-labor-force-unemployment-inflation", ARCHIVE),
    ("2025-01-13", "How batteries are already changing the grid", "1224599777", "the-indicator-from-planet-money-how-batteries-are-changing-the-us-01-13-2025", ARCHIVE),
    ("2025-01-14", "How batteries are riding the free market rodeo in Texas", "1224682730", "how-batteries-are-riding-the-free-market-rodeo-in-texas", ARCHIVE),
    ("2025-01-15", "The race to produce lithium", "1224776146", "the-race-to-produce-lithium", ARCHIVE),
    ("2025-01-16", "Who's on the hook for California's uninsurable homes?", "1224897145", "the-indicator-from-planet-money-california-wildfire-fair-plan-01-16-2025", ARCHIVE),
    ("2025-01-17", "Student loans, savings accounts, and goodbye to artificial red dye", "1225172104", "biden-trump-fda-student-loans-capital-one-red-dye", ARCHIVE),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": len(EPISODES),
        "EPISODES": EPISODES,
        "BATCH_LABEL": "2025_january_cluster",
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
