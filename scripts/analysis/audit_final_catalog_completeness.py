#!/usr/bin/env python3
"""Cross-check the live archive against NPR, Apple, and TheTVDB catalogs."""

from __future__ import annotations

import difflib
import argparse
import html
import json
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = {"User-Agent": "Mozilla/5.0"}
NPR_FEED = "https://feeds.npr.org/510325/podcast.xml"
APPLE_API = (
    "https://itunes.apple.com/lookup?id=1320118593"
    "&entity=podcastEpisode&limit=200&country=us"
)
TVDB_SEASON = (
    "https://thetvdb.com/series/"
    "the-indicator-from-planet-money-podcast/seasons/official/{year}"
)
OUTPUT = REPO_ROOT / "data/audits/indicator_final_catalog_completeness_audit.json"
LAUNCH_AUDIT = REPO_ROOT / "data/audits/indicator_pre_march_2018_catalog_audit.json"
KNOWN_NON_FEED_HISTORY_IDS = {
    "1013954358", "1029846068", "1034085667", "1038307729",
}
KNOWN_TVDB_CLASSIFICATIONS = {
    "Are you afraid of inflation?": {
        "classification": "alternate_title_for_existing_indicator_episode",
        "local_story_id": "1050665635",
        "local_title": "Night of the living inflation",
        "basis": "same 2021-10-29 Indicator release under an alternate catalog title",
    },
    "BONUS: Wisdom From The Top": {
        "classification": "non_indicator_cross_feed_promotion",
        "source_show": "Consider This from NPR",
        "basis": "59-minute promotion from another NPR show, not program 510325",
    },
}


def fetch(url: str) -> bytes:
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=USER_AGENT), timeout=30
    ).read()


def normalize(title: str | None) -> str:
    value = unicodedata.normalize("NFKD", html.unescape(title or ""))
    value = value.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def rss_rows(root: ET.Element) -> list[dict]:
    return [
        {
            "date": parsedate_to_datetime(item.findtext("pubDate") or "").date(),
            "title": item.findtext("title") or "",
            "guid": (item.findtext("guid") or "").strip(),
        }
        for item in root.findall(".//item")
    ]


def date_title_set(rows: list[dict]) -> set[tuple[str, str]]:
    return {(str(row["date"]), normalize(row["title"])) for row in rows}


def tvdb_rows() -> list[dict]:
    pattern = re.compile(
        r'<tr>\s*<td>S(\d{4})E(\d+)</td>\s*<td>\s*'
        r'<a href="([^"]+)">\s*(.*?)\s*</a>.*?</td>\s*<td>\s*'
        r'(?:<div>)?\s*([^<\r\n]+?)\s*(?:</div>)?\s*</td>',
        re.S,
    )
    rows = []
    for year in range(2018, datetime.now().year + 1):
        body = fetch(TVDB_SEASON.format(year=year)).decode("utf-8", "replace")
        for season, number, path, title, aired in pattern.findall(body):
            try:
                date = datetime.strptime(aired.strip(), "%B %d, %Y").date()
            except ValueError:
                continue
            rows.append(
                {
                    "season": int(season),
                    "episode": int(number),
                    "date": date,
                    "title": html.unescape(re.sub(r"<[^>]+>", "", title)).strip(),
                    "url": "https://thetvdb.com" + path,
                }
            )
    return rows


def plausible_match(candidate: dict, local_by_date: dict, local_titles: set[str]) -> bool:
    title = normalize(candidate["title"])
    if title in local_titles:
        return True
    for offset in range(-2, 3):
        for local_title in local_by_date[candidate["date"] + timedelta(days=offset)]:
            if difflib.SequenceMatcher(None, title, normalize(local_title)).ratio() >= 0.72:
                return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="exit nonzero unless the defined program-feed scope is complete",
    )
    args = parser.parse_args()
    local_root = ET.parse(REPO_ROOT / "theindicator_feed.xml").getroot()
    local = rss_rows(local_root)
    history = json.loads(
        (REPO_ROOT / "indicator_history.json").read_text(encoding="utf-8")
    )["episodes"]
    enclosure_map = json.loads(
        (REPO_ROOT / "indicator_enclosure_map.json").read_text(encoding="utf-8")
    )["episodes"]

    npr = rss_rows(ET.fromstring(fetch(NPR_FEED)))
    apple_payload = json.loads(fetch(APPLE_API))
    apple = [
        {
            "date": datetime.fromisoformat(item["releaseDate"].replace("Z", "+00:00")).date(),
            "title": item["trackName"],
            "guid": item.get("episodeGuid", ""),
        }
        for item in apple_payload["results"]
        if item.get("wrapperType") == "podcastEpisode"
    ]

    local_keys = date_title_set(local)
    local_by_date = defaultdict(list)
    local_titles = set()
    for row in local:
        local_by_date[row["date"]].append(row["title"])
        local_titles.add(normalize(row["title"]))

    external = tvdb_rows()
    candidates = [
        row for row in external if not plausible_match(row, local_by_date, local_titles)
    ]
    candidate_counts = Counter(row["season"] for row in candidates)
    feed_guids = {row["guid"] for row in local}
    history_not_feed = [
        {
            "story_id": str(row["story_id"]),
            "date": row["date"],
            "title": row["title"],
            "enclosure_status": enclosure_map[str(row["story_id"])]["status"],
            "classification": (
                "planet_money_compilation_reusing_indicator_segments"
                if str(row["story_id"]) in KNOWN_NON_FEED_HISTORY_IDS
                else "non_feed_history_record_pending_scope_review"
            ),
        }
        for row in history
        if str(row["story_id"]) not in feed_guids
    ]

    classified_tvdb = []
    unresolved_tvdb = []
    for row in candidates:
        serialized = {**row, "date": str(row["date"])}
        classification = KNOWN_TVDB_CLASSIFICATIONS.get(row["title"])
        if classification:
            classified_tvdb.append({**serialized, **classification})
        else:
            unresolved_tvdb.append(serialized)

    launch_audit = json.loads(LAUNCH_AUDIT.read_text(encoding="utf-8"))
    enclosure_lengths = [
        int(item.find("enclosure").get("length", "0"))
        for item in local_root.findall(".//item")
    ]
    current_feed_complete = (
        not (date_title_set(npr) - local_keys)
        and not (date_title_set(apple) - local_keys)
        and not unresolved_tvdb
        and all(
            item["classification"] == "planet_money_compilation_reusing_indicator_segments"
            for item in history_not_feed
        )
        and launch_audit["summary"]["historical_items"] == 59
        and launch_audit["summary"]["exact_live_audio_responses"] == 59
        and len(local) == len(feed_guids)
        and all(length > 0 for length in enclosure_lengths)
    )
    report = {
        "report_version": 2,
        "generated_at": datetime.now().astimezone().isoformat(),
        "scope": "original NPR program 510325 feed from its 2017-12-01 trailer through present",
        "local": {
            "feed_items": len(local),
            "unique_guids": len(feed_guids),
            "year_counts": dict(
                sorted(Counter(row["date"].year for row in local).items())
            ),
            "history_records_absent_from_feed": history_not_feed,
            "unknown_or_zero_enclosure_lengths": sum(
                length <= 0 for length in enclosure_lengths
            ),
        },
        "npr_current_feed": {
            "items": len(npr),
            "missing_from_local_by_date_title": sorted(date_title_set(npr) - local_keys),
        },
        "apple_current_catalog": {
            "items": len(apple),
            "missing_from_local_by_date_title": sorted(date_title_set(apple) - local_keys),
        },
        "tvdb": {
            "items": len(external),
            "candidate_omissions": len(candidates),
            "candidate_counts_by_year": dict(sorted(candidate_counts.items())),
            "classified_catalog_mismatches": classified_tvdb,
            "unresolved_candidate_omissions": unresolved_tvdb,
        },
        "original_launch_feed": {
            "source_snapshot": launch_audit["source"],
            "historical_items": launch_audit["summary"]["historical_items"],
            "episodes": launch_audit["summary"]["episodes"],
            "launch_trailers": launch_audit["summary"]["launch_trailers"],
            "exact_live_audio_responses": launch_audit["summary"]["exact_live_audio_responses"],
            "all_present_in_current_feed": all(
                item["story_id"] in feed_guids for item in launch_audit["items"]
            ),
        },
        "verdict": (
            "complete_for_defined_program_feed_scope"
            if current_feed_complete
            else "not_complete_pending_candidate_investigation"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.require_complete and report["verdict"] != "complete_for_defined_program_feed_scope":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
