#!/usr/bin/env python3
"""Cross-check the live archive against NPR, Apple, and TheTVDB catalogs."""

from __future__ import annotations

import difflib
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
    local_root = ET.parse(REPO_ROOT / "theindicator_feed.xml").getroot()
    local = rss_rows(local_root)
    history = json.loads(
        (REPO_ROOT / "indicator_history.json").read_text(encoding="utf-8")
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
        }
        for row in history
        if str(row["story_id"]) not in feed_guids
    ]

    report = {
        "local": {
            "feed_items": len(local),
            "unique_guids": len(feed_guids),
            "year_counts": dict(
                sorted(Counter(row["date"].year for row in local).items())
            ),
            "history_records_absent_from_feed": history_not_feed,
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
            "candidates": [
                {**row, "date": str(row["date"])} for row in candidates
            ],
        },
        "verdict": "not_complete_pending_candidate_investigation",
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
