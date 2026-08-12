#!/usr/bin/env python3
from pathlib import Path

import json
import re
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
REPO_ROOT = Path(__file__).resolve().parents[2]


OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_feed_probe.json")
FEEDS = [
    {
        "name": "official_npr_indicator_feed",
        "url": "https://feeds.npr.org/510325/podcast.xml",
    },
]

TARGETS = [
    {
        "title": "Paranormal Profits",
        "date": "2018-10-31",
        "story_id": "662708285",
        "player_story_id": "662706955",
        "audio_id": "662707862",
    },
    {
        "title": "The Traffic Tariff",
        "date": "2019-04-22",
        "story_id": "716132270",
        "player_story_id": "716127469",
        "audio_id": "730102905",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRFeedProbe/1.0)"
    )
}

TIMEOUT = 30


def fetch(url):
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=TIMEOUT) as response:
        return {
            "final_url": response.geturl(),
            "status_code": getattr(response, "status", None),
            "content_type": response.headers.get("Content-Type", ""),
            "text": response.read().decode("utf-8", errors="replace"),
        }


def clean(value):
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def text_of(element, names):
    for name in names:
        found = element.find(name)

        if found is not None and found.text:
            return clean(found.text)

    return None


def extract_items(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items = []

    for item in root.findall(".//item"):
        title = text_of(item, ["title"])
        guid = text_of(item, ["guid"])
        pub_date = text_of(item, ["pubDate"])
        link = text_of(item, ["link"])
        description = text_of(
            item,
            ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"]
        )

        enclosure = item.find("enclosure")

        enclosure_url = (
            enclosure.attrib.get("url")
            if enclosure is not None
            else None
        )

        enclosure_type = (
            enclosure.attrib.get("type")
            if enclosure is not None
            else None
        )

        enclosure_length = (
            enclosure.attrib.get("length")
            if enclosure is not None
            else None
        )

        raw = ET.tostring(
            item,
            encoding="unicode"
        )

        items.append({
            "title": title,
            "guid": guid,
            "pub_date": pub_date,
            "link": link,
            "description": description,
            "enclosure_url": enclosure_url,
            "enclosure_type": enclosure_type,
            "enclosure_length": enclosure_length,
            "raw_item": raw,
        })

    return items


def match_target(target, item):
    haystack = " ".join(
        clean(value)
        for value in [
            item.get("title"),
            item.get("guid"),
            item.get("pub_date"),
            item.get("link"),
            item.get("description"),
            item.get("enclosure_url"),
            item.get("raw_item"),
        ]
        if value
    ).lower()

    reasons = []

    for field in [
        "story_id",
        "player_story_id",
        "audio_id",
    ]:
        value = target[field]

        if value in haystack:
            reasons.append(field)

    title = target["title"].lower()

    if title in haystack:
        reasons.append("exact_title")

    date = target["date"]

    if date in haystack:
        reasons.append("iso_date")

    return reasons


report = {
    "method": "official-npr-indicator-feed-probe",
    "show_id": "510325",
    "targets": TARGETS,
    "feeds": [],
}


for feed in FEEDS:
    result = {
        "name": feed["name"],
        "requested_url": feed["url"],
        "status": None,
        "item_count": 0,
        "target_results": [],
    }

    try:
        response = fetch(feed["url"])

    except HTTPError as exc:
        result["status"] = f"http_{exc.code}"
        result["error"] = str(exc)
        report["feeds"].append(result)
        continue

    except (
        URLError,
        TimeoutError,
    ) as exc:
        result["status"] = "request_error"
        result["error"] = str(exc)
        report["feeds"].append(result)
        continue

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        report["feeds"].append(result)
        continue

    result["status"] = "fetched"
    result["final_url"] = response["final_url"]
    result["status_code"] = response["status_code"]
    result["content_type"] = response["content_type"]

    xml_text = response["text"]
    items = extract_items(xml_text)

    result["item_count"] = len(items)

    # Also test raw XML in case the target appears
    # somewhere outside a normal RSS item.
    raw_lower = xml_text.lower()

    for target in TARGETS:
        matches = []

        for item in items:
            reasons = match_target(
                target,
                item
            )

            if reasons:
                matches.append({
                    "reasons": reasons,
                    "title": item.get("title"),
                    "guid": item.get("guid"),
                    "pub_date": item.get("pub_date"),
                    "link": item.get("link"),
                    "enclosure_url": item.get("enclosure_url"),
                    "enclosure_type": item.get("enclosure_type"),
                    "enclosure_length": item.get("enclosure_length"),
                })

        raw_hits = {}

        for field in [
            "story_id",
            "player_story_id",
            "audio_id",
        ]:
            value = target[field]

            raw_hits[field] = (
                value.lower()
                in raw_lower
            )

        result[
            "target_results"
        ].append({
            "target":
                target,

            "matching_item_count":
                len(matches),

            "matches":
                matches,

            "raw_xml_id_hits":
                raw_hits,
        })

    report["feeds"].append(
        result
    )


with open(
    OUTPUT_FILE,
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        report,
        file,
        indent=2,
        ensure_ascii=False
    )


print()
print("==============================")
print("NPR feed probe complete")

for feed in report["feeds"]:
    print()
    print("Feed:", feed["name"])
    print("Status:", feed["status"])
    print("Items:", feed["item_count"])

    for target in feed.get(
        "target_results",
        []
    ):
        print(
            target["target"]["title"],
            "- matches:",
            target["matching_item_count"],
            "- ID hits:",
            target["raw_xml_id_hits"],
        )

print()
print("Saved:", OUTPUT_FILE)
print("==============================")
