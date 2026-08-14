#!/usr/bin/env python3
"""Discover and stage eight confirmed February 2024 omissions."""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_remaining_ranked_batch2 as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_2024_february_mid_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/2024-february-mid-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_2024_february_mid_cluster_staging_report.json"
EXPECTED_COUNT = 8
BATCH_LABEL = "2024_february_mid_cluster"
DISCOVERY_LABEL = DISCOVERY.name

EPISODES = [
    ("2024-02-07", "Is Wall Street's hottest trend finally over?", "1197961691", "is-wall-streets-hottest-trend-finally-over", "https://www.gpb.org/news/planet-money/2024/02/07/wall-streets-hottest-trend-finally-over"),
    ("2024-02-08", "Why Saudi Arabia is building a new city in the desert", "1197961699", "the-indicator-from-planet-money-saudi-arabia-vision-2030-02-08-2024", "https://www.gpb.org/news/planet-money/2024/02/08/why-saudi-arabia-building-new-city-in-the-desert"),
    ("2024-02-09", "A Swiftie Super Bowl, a stumbling bank, and other indicators", "1197961749", "taylor-swift-super-bowl-mexico-imports-china-nycb", "https://www.gpb.org/news/planet-money/2024/02/09/swiftie-super-bowl-stumbling-bank-and-other-indicators"),
    ("2024-02-12", "What's really happening with the Evergrande liquidation", "1197961810", "whats-really-happening-with-the-evergrande-liquidation", "https://www.gpb.org/news/planet-money/2024/02/12/whats-really-happening-the-evergrande-liquidation"),
    ("2024-02-13", "How's your defense industry knowledge?", "1197961826", "indicator-quiz-defense-industry", "https://www.gpb.org/news/planet-money/2024/02/13/hows-your-defense-industry-knowledge"),
    ("2024-02-14", "How Egypt's military is dragging down its economy", "1197961858", "how-egypts-military-is-dragging-down-its-economy", "https://www.gpb.org/news/planet-money/2024/02/14/how-egypts-military-dragging-down-its-economy"),
    ("2024-02-15", "Why banks are fighting changes to an anti-redlining program", "1197961870", "why-banks-are-fighting-changes-to-an-anti-redlining-program", "https://www.npr.org/transcripts/1197961870"),
    ("2024-02-16", "Chocolate, Lyft's typo and India's election bonds", "1197961990", "indicators-cocoa-prices-lyft-india-elections", "https://www.wbur.org/npr/1197961990/indicators-cocoa-prices-lyft-india-elections"),
]


def request(url: str, *, byte_range: bool = False):
    headers = {"User-Agent": "Mozilla/5.0"}
    if byte_range:
        headers["Range"] = "bytes=0-0"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)


def discover() -> dict:
    records = []
    for values in EPISODES:
        date, title, story_id, slug, evidence_url = values[:5]
        npr_url = values[5] if len(values) > 5 else f"https://www.npr.org/{date.replace('-', '/')}/{story_id}/{slug}"
        with request(npr_url) as response:
            page = response.read().decode("utf-8", "replace")
        audio_urls = sorted(set(
            html.unescape(value).replace("\\u0026", "&")
            for value in re.findall(r'https?[^"<>\\ ]+\.mp3[^"<>\\ ]*', page)
            if "siteplayer" in value
        ))
        if len(audio_urls) != 1:
            raise RuntimeError(f"Expected one NPR enclosure for {story_id}; found {len(audio_urls)}")
        audio_url = audio_urls[0]
        with request(audio_url, byte_range=True) as response:
            if response.status != 206 or response.headers.get("Content-Type") != "audio/mpeg":
                raise RuntimeError(f"Invalid audio response for {story_id}")
            content_range = response.headers.get("Content-Range", "")
        match = re.search(r"/(?:episodes/)?([0-9a-f]{8}-[0-9a-f-]{27,})/|indicator_([0-9a-f-]{36})\.mp3", audio_url)
        audio_id = next((value for value in (match.groups() if match else ()) if value), story_id)
        duration = int(re.search(r"[?&]d=(\d+)", audio_url).group(1))
        content_length = int(content_range.rsplit("/", 1)[1])
        records.append({
            "publication_date": date,
            "reference_title": title,
            "npr_story_id": story_id,
            "npr_url": npr_url,
            "player_story_id": story_id,
            "audio_id": audio_id,
            "player_url": npr_url,
            "affiliate_url": evidence_url,
            "audio_url": audio_url,
            "content_length_bytes": content_length,
            "duration_seconds": duration,
            "classification": "ready_for_isolated_staging",
        })
    payload = {"source_audit": "indicator_final_catalog_completeness_audit.md", "episodes": records}
    engine.write_json(DISCOVERY, payload)
    return payload


def stage(repo_root=REPO_ROOT, stage_dir=None):
    discovery = discover()
    if len(discovery["episodes"]) != EXPECTED_COUNT:
        raise RuntimeError(f"Expected exactly {EXPECTED_COUNT} discovered episodes.")
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": EXPECTED_COUNT,
        "BATCH_LABEL": BATCH_LABEL,
        "DISCOVERY_LABEL": DISCOVERY_LABEL,
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
    engine.write_json(REPORT, result)
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["checks"], indent=2))
    print("Staging complete. Production files were not modified.")
