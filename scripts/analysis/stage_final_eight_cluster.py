#!/usr/bin/env python3
"""Discover and stage the final eight catalog omissions from archived NPR feeds."""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.analysis import stage_2024_february_mid_cluster as engine

REPO_ROOT = engine.REPO_ROOT
DISCOVERY = REPO_ROOT / "data/audits/indicator_final_eight_cluster_staging_candidates.json"
DEFAULT_STAGE_DIR = REPO_ROOT / "work/final-eight-cluster-staging"
REPORT = REPO_ROOT / "data/audits/indicator_final_eight_cluster_staging_report.json"
FEED_2020 = "https://web.archive.org/web/20200327033855id_/https://feeds.npr.org/510325/podcast.xml"
FEED_2020_11 = "https://web.archive.org/web/20201122000000id_/https://feeds.npr.org/510325/podcast.xml"
FEED_2020_12 = "https://web.archive.org/web/20201226000000id_/https://feeds.npr.org/510325/podcast.xml"
FEED_2021 = "https://web.archive.org/web/20210527014623id_/https://feeds.npr.org/510325/podcast.xml"
FEED_2022 = "https://web.archive.org/web/20221210000000id_/https://feeds.npr.org/510325/podcast.xml"

EPISODES = [
    (
        "2019-10-04", "Jobs Friday: Crunching The Numbers", "766998707",
        "jobs-friday-crunching-the-numbers", FEED_2020,
        "https://www.npr.org/2019/10/03/766998707/jobs-friday-crunching-the-numbers",
        "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/indicator/2019/10/20191004_indicator_191004_jobs_friday_final-e80d0cec-1fb2-4221-9124-0123ae0da535.mp3?awCollectionId=510325&awEpisodeId=766998707&orgId=1&topicId=1006&d=600&p=510325&story=766998707&t=podcast&e=766998707&size=9579947&ft=pod&f=510325",
    ),
    (
        "2019-11-05", "Openness Versus National Security: A Dilemma For U.S. Schools", "776504323",
        "openness-versus-national-security-a-dilemma-for-u-s-schools", FEED_2020,
        "https://www.npr.org/2019/11/05/776504323/openness-versus-national-security-a-dilemma-for-u-s-schools",
        "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/indicator/2019/11/20191105_indicator_191104_chinese_influence_us_universities_final-4f2300b8-4bd5-4470-b665-fd365e8a6ac7-f346c45f-62a8-42d4-8703-32272c0f764f.mp3?awCollectionId=510325&awEpisodeId=776504323&orgId=1&topicId=1006&d=598&p=510325&story=776504323&t=podcast&e=776504323&size=9560765&ft=pod&f=510325",
    ),
    (
        "2020-02-14", "How Economists Do Valentines", "806153805",
        "how-economists-do-valentines", FEED_2020,
        "https://www.npr.org/2020/02/14/806153805/how-economists-do-valentines",
        "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/indicator/2020/02/20200214_indicator_econ_valentine_s_final-01c50f2c-b99f-4b0c-a01f-63c7cc6e1395.mp3?awCollectionId=510325&awEpisodeId=806153805&orgId=1&topicId=1006&d=591&p=510325&story=806153805&t=podcast&e=806153805&size=9441503&ft=pod&f=510325",
    ),
    (
        "2020-11-20", "A Face-Punching Legal Battle", "937158506",
        "a-face-punching-legal-battle", FEED_2020_11,
        "https://www.npr.org/2020/11/20/937158506/a-face-punching-legal-battle",
        "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2020/11/20201120_indicator_ufc_lawsuit_ready_to_publish_1.mp3?awCollectionId=510325&awEpisodeId=937158506&orgId=1&topicId=1017&d=592&p=510325&story=937158506&t=podcast&e=937158506&size=9452556&ft=pod&f=510325",
    ),
    (
        "2020-12-24", "Healthcare: The Pandemic's Financial Fallout", "949242597",
        "healthcare-the-pandemics-financial-fallout", FEED_2020_12,
        "https://www.npr.org/2020/12/22/949242597/healthcare-the-pandemics-financial-fallout",
        "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2020/12/20201224_indicator_patrick_cawley_ready_to_publish.mp3?awCollectionId=510325&awEpisodeId=949242597&orgId=1&d=599&p=510325&story=949242597&t=podcast&e=949242597&size=9578490&ft=pod&f=510325",
    ),
    (
        "2021-05-21", "Dogecoin, Retail And The Cafe Table Indicator", "999259034",
        "dogecoin-retail-and-the-cafe-table-indicator", FEED_2021,
        "https://www.npr.org/2021/05/21/999259034/dogecoin-retail-and-the-cafe-table-indicator",
        "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2021/05/20210521_indicator_indicators_of_the_week_for_publish.mp3?awCollectionId=510325&awEpisodeId=999259034&orgId=1&topicId=1006&d=556&p=510325&story=999259034&t=podcast&e=999259034&size=8906380&ft=pod&f=510325",
    ),
    (
        "2022-12-08", "Where inflation hits hardest", "1141665565",
        "where-inflation-hits-hardest", FEED_2022,
        "https://www.npr.org/2022/12/08/1141665565/where-inflation-hits-hardest",
        "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2022/12/20221208_indicator_05abf0a1-e4b0-456e-84cf-ddba16432fdd.mp3?awCollectionId=510325&awEpisodeId=1141665565&orgId=1&topicId=1017&d=561&p=510325&story=1141665565&t=podcast&e=1141665565&size=8992436&ft=pod&f=510325",
    ),
]


def stage(repo_root=REPO_ROOT, stage_dir=None):
    values = {
        "DISCOVERY": DISCOVERY,
        "DEFAULT_STAGE_DIR": DEFAULT_STAGE_DIR,
        "REPORT": REPORT,
        "EXPECTED_COUNT": len(EPISODES),
        "EPISODES": EPISODES,
        "BATCH_LABEL": "final_eight_cluster",
        "DISCOVERY_LABEL": DISCOVERY.name,
    }
    prior = {name: getattr(engine, name) for name in values}
    try:
        for name, value in values.items():
            setattr(engine, name, value)
        report = engine.stage(repo_root=repo_root, stage_dir=stage_dir)
    finally:
        for name, value in prior.items():
            setattr(engine, name, value)

    stage_dir = stage_dir or DEFAULT_STAGE_DIR
    history_path = stage_dir / "indicator_history.json"
    feed_path = stage_dir / "theindicator_feed.xml"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    history_matches = [item for item in history["episodes"] if str(item.get("story_id")) == "747044058"]
    if len(history_matches) != 1:
        raise RuntimeError("Expected one existing 747044058 history record.")
    history_matches[0]["title"] = "Workers Take A Seat At The Table?"
    engine.engine.write_json(history_path, history)

    tree = ET.parse(feed_path)
    feed_matches = [
        item for item in tree.getroot().findall(".//item")
        if (item.findtext("guid") or "").strip() == "747044058"
    ]
    if len(feed_matches) != 1:
        raise RuntimeError("Expected one existing 747044058 feed item.")
    feed_item = feed_matches[0]
    feed_item.find("title").text = "Workers Take A Seat At The Table?"
    itunes_title = feed_item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}title")
    if itunes_title is not None:
        itunes_title.text = "Workers Take A Seat At The Table?"
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    engine.engine.preserve_namespace_style(repo_root / "theindicator_feed.xml", feed_path)

    report["metadata_corrections"] = [{
        "story_id": "747044058",
        "old_title": "What If Workers Sat On Corporate Boards?",
        "new_title": "Workers Take A Seat At The Table?",
        "fields": ["title", "itunes:title"],
    }]
    report["checks"]["existing_747044058_metadata_corrected"] = True
    report["all_checks_passed"] = all(report["checks"].values())
    report["staged_sha256"]["indicator_history.json"] = engine.engine.sha256(history_path)
    report["staged_sha256"]["theindicator_feed.xml"] = engine.engine.sha256(feed_path)
    engine.engine.write_json(stage_dir / "staging_report.json", report)
    return report


if __name__ == "__main__":
    result = stage()
    engine.engine.write_json(REPORT, result)
    print(json.dumps(result["counts"], indent=2))
    print(json.dumps(result["checks"], indent=2))
    print("Staging complete. Production files were not modified.")
