#!/usr/bin/env python3
"""Discover and stage 26 confirmed November-December 2024 omissions."""

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2024_november_december_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2024-november-december-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2024_november_december_cluster_staging_report.json"
ARCHIVE = "https://web.archive.org/web/20241230211051id_/https://feeds.npr.org/510325/podcast.xml"
EPISODES = [
    ("2024-11-04", "Why the US government is buying more apples than ever before", "1211597740", "america-apple-abundance-problem", ARCHIVE),
    ("2024-11-05", "Why Midwest crop farmers are having a logistics problem", "1211597784", "chicago-midwest-farmers-soybeans-mexico-mississippi", ARCHIVE),
    ("2024-11-06", "America's economy is the envy of the world. Will it stay that way?", "1211597925", "americas-economy-is-the-envy-of-the-world", ARCHIVE),
    ("2024-11-07", "The story behind Cuba's economic dysfunction", "1211597969", "the-story-behind-cubas-economic-dysfunction", ARCHIVE),
    ("2024-11-08", "Stocks jump, the temperamental peso, and other election aftermath indicators", "1211598002", "trump-election-stocks-peso-mexico-minimum-wage", ARCHIVE),
    ("2024-11-11", "What's a weather forecast worth?", "1212475049", "indicator-what-is-weather-forecasts-worth", ARCHIVE),
    ("2024-11-12", "Why this former banking regulator is writing kids books", "1212541658", "sheila-bair-fdic-compound-interest-predatory-lenders-kids-books", ARCHIVE),
    ("2024-11-13", "Why the government's flood insurance program is underwater", "1212604199", "indicator-problems-with-national-flood-insurance-program", ARCHIVE),
    ("2024-11-14", "Who's powering nuclear energy's comeback?", "1212866790", "whos-powering-nuclear-energys-comeback", ARCHIVE),
    ("2024-11-15", "23andMe's financial troubles, Paul vs. Tyson and Bitcoin to the moon", "1213159038", "the-indicator-from-planet-money-23andme-paul-tyson-boxing-bitcoin-11-15-2024", ARCHIVE),
    ("2024-11-17", "The Economics of Everyday Things: Pizza (Box) Time!", "1198001375", "freakonomics-the-economics-of-everything-pizza-boxes", ARCHIVE, "https://www.npr.org/2026/01/01/1198001375/freakonomics-the-economics-of-everyday-things-pizza-boxes"),
    ("2024-11-18", "A fraught climate change conference, how are US home builders doing, and more", "1213978431", "cop29-trump-manufacturing-home-building", ARCHIVE),
    ("2024-11-19", "How to shop during a crisis", "1214051393", "the-indicator-from-planet-money-how-to-shop-during-a-crisis-11-19-2024", ARCHIVE),
    ("2024-11-20", "How Magic Johnson's Starbucks created new neighborhood businesses", "1214145113", "magic-johnson-lakers-starbucks-entrepreneurship-los-angeles-harlem", ARCHIVE),
    ("2024-11-21", "Bond vigilantes. Who they are, what they want, and how you'll know they're coming", "1214380327", "who-are-the-bond-vigilantes", ARCHIVE),
    ("2024-11-22", "The most expensive banana in the world and other indicators", "1214662562", "the-most-expensive-banana-in-the-world-and-other-indicators", ARCHIVE),
    ("2024-11-25", "How big is the US housing shortage?", "1215189230", "how-trump-tariffs-imports", ARCHIVE),
    ("2024-11-26", "Trump's plans for the housing market", "1215240061", "indicator-trump-plan-housing-market", ARCHIVE),
    ("2024-11-27", "What's in your wallet? Ask the new Treasury Secretary", "1215355317", "whats-in-your-wallet-ask-the-new-treasury-secretary", ARCHIVE),
    ("2024-12-14", "Why the US economy is still the envy of the world", "1198001428", "simon-rabinovitch-us-economy-envy-of-the-world", ARCHIVE, "https://www.npr.org/2026/01/01/1198001428/simon-rabinovitch-us-economy-envy-of-the-world"),
    ("2024-12-23", "What indicators will 2025 bring?", "1221439465", "indicators-of-the-year-economy-tariffs-inflation-soft-landing", ARCHIVE),
    ("2024-12-24", "How TV holiday rom-coms got so successful (Encore)", "1221471002", "tv-holiday-rom-com-successful-encore", ARCHIVE),
    ("2024-12-26", "How video games become more accessible (Encore)", "1221596349", "designing-disability-video-gaming-accessibility-encore", ARCHIVE),
    ("2024-12-27", "Half a billion people need reading glasses. Why can't they get them? (Encore)", "1221795522", "half-a-billion-people-need-reading-glasses-why-cant-they-get-them-encore", ARCHIVE),
    ("2024-12-30", "Invest like a Congress member (Encore)", "1222276534", "stock-trading-congress-etfs-unusual-whales", ARCHIVE),
    ("2024-12-31", "The curious rise of novelty popcorn buckets (Encore)", "1222340422", "the-curious-rise-of-novelty-popcorn-buckets", "https://web.archive.org/web/20250102182923id_/https://feeds.npr.org/510325/podcast.xml"),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": len(EPISODES),
        "EPISODES": EPISODES,
        "BATCH_LABEL": "2024_november_december_cluster",
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
