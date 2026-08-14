#!/usr/bin/env python3
"""Audit the Indicator catalog before the local feed's March 12, 2018 start."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FEED_PATH = REPO_ROOT / "theindicator_feed.xml"
REPORT_PATH = REPO_ROOT / "data/audits/indicator_pre_march_2018_catalog_audit.json"
SNAPSHOT_TIMESTAMP = "20180310110945"
ORIGINAL_FEED = "https://www.npr.org/rss/podcast.php?id=510325"
SNAPSHOT_URL = (
    f"https://web.archive.org/web/{SNAPSHOT_TIMESTAMP}id_/{ORIGINAL_FEED}"
)
START = datetime(2017, 12, 1, tzinfo=timezone.utc)
END = datetime(2018, 3, 12, tzinfo=timezone.utc)
USER_AGENT = "theindicator-rss historical catalog audit"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(item: ET.Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def story_id(enclosure_url: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(enclosure_url).query)
    for key in ("story", "e"):
        value = query.get(key, [""])[0]
        if value.isdigit():
            return value
    return ""


def current_audio_url(archived_url: str) -> str:
    match = re.search(r"/media/(anon\.npr-mp3/.+)$", archived_url)
    if not match:
        raise ValueError("archived enclosure has no NPR media path")
    return "https://ondemand.npr.org/" + match.group(1)


def parse_items(payload: bytes) -> list[dict[str, object]]:
    root = ET.fromstring(payload)
    records = []
    for item in root.findall("./channel/item"):
        published = parsedate_to_datetime(text(item, "pubDate"))
        published_utc = published.astimezone(timezone.utc)
        if not START <= published_utc < END:
            continue
        enclosure = item.find("enclosure")
        if enclosure is None:
            raise ValueError(f"{text(item, 'title')}: missing enclosure")
        archived_url = enclosure.get("url", "")
        duration = text(
            item, "{http://www.itunes.com/dtds/podcast-1.0.dtd}duration"
        )
        records.append(
            {
                "title": text(item, "title"),
                "description": text(item, "description"),
                "published": published.isoformat(),
                "guid": text(item, "guid"),
                "story_id": story_id(archived_url),
                "duration_seconds": int(duration) if duration.isdigit() else None,
                "archived_enclosure_url": archived_url,
                "candidate_audio_url": current_audio_url(archived_url),
                "classification": (
                    "launch_trailer" if text(item, "title") == "Coming Soon" else "episode"
                ),
            }
        )
    return records


def current_keys() -> tuple[set[str], set[str], set[str]]:
    root = ET.parse(FEED_PATH).getroot()
    guids: set[str] = set()
    story_ids: set[str] = set()
    normalized_titles: set[str] = set()
    for item in root.findall("./channel/item"):
        guids.add(text(item, "guid"))
        normalized_titles.add(" ".join(text(item, "title").casefold().split()))
        enclosure = item.find("enclosure")
        if enclosure is not None:
            candidate = story_id(enclosure.get("url", ""))
            if candidate:
                story_ids.add(candidate)
    return guids, story_ids, normalized_titles


def probe(record: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        str(record["candidate_audio_url"]),
        headers={
            "User-Agent": USER_AGENT,
            "Range": "bytes=0-0",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        response.read(1)
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes 0-0/(\d+)", content_range)
        return {
            "http_status": response.status,
            "content_type": response.headers.get_content_type(),
            "content_range": content_range,
            "content_length": int(match.group(1)) if match else None,
            "final_url": response.geturl(),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    feed_hash_before = sha256(FEED_PATH)
    snapshot = fetch(SNAPSHOT_URL)
    records = parse_items(snapshot)
    guids, story_ids, titles = current_keys()

    for record in records:
        record["present_in_current_feed"] = any(
            (
                record["guid"] in guids,
                record["story_id"] in story_ids,
                " ".join(str(record["title"]).casefold().split()) in titles,
            )
        )

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(probe, record): record for record in records}
        for future in as_completed(futures):
            futures[future]["audio_probe"] = future.result()

    records.sort(key=lambda record: str(record["published"]))
    exact_audio = sum(
        record["audio_probe"]["http_status"] == 206
        and record["audio_probe"]["content_type"] == "audio/mpeg"
        and record["audio_probe"]["content_length"] is not None
        for record in records
    )
    report = {
        "report_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "start_inclusive": START.isoformat(),
            "end_exclusive": END.isoformat(),
        },
        "source": {
            "original_feed": ORIGINAL_FEED,
            "wayback_timestamp": SNAPSHOT_TIMESTAMP,
            "snapshot_url": SNAPSHOT_URL,
            "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
        },
        "production_unchanged": feed_hash_before == sha256(FEED_PATH),
        "production_feed_sha256": feed_hash_before,
        "summary": {
            "historical_items": len(records),
            "episodes": sum(record["classification"] == "episode" for record in records),
            "launch_trailers": sum(
                record["classification"] == "launch_trailer" for record in records
            ),
            "already_in_current_feed": sum(
                bool(record["present_in_current_feed"]) for record in records
            ),
            "missing_from_current_feed": sum(
                not record["present_in_current_feed"] for record in records
            ),
            "exact_live_audio_responses": exact_audio,
        },
        "items": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Production feed unchanged: {report['production_unchanged']}")
    print(f"Report: {args.report}")
    return 0 if exact_audio == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
