#!/usr/bin/env python3
from pathlib import Path

import json
import re
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
REPO_ROOT = Path(__file__).resolve().parents[2]



OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_legacy_feed_probe.json")
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

FEEDS = [
    {
        "name": "legacy_indicator",
        "url": (
            "https://legacy.npr.org/templates/rss/"
            "podlayer.php?id=510325"
        ),
    },
    {
        "name": "legacy_indicator_double_id",
        "url": (
            "https://legacy.npr.org/templates/rss/"
            "podlayer.php?id=510325?id=510325"
        ),
    },
    {
        "name": "current_indicator",
        "url": "https://feeds.npr.org/510325/podcast.xml",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorLegacyFeedProbe/1.0)"
    )
}

TIMEOUT = 30


def fetch(url):
    request = Request(
        url,
        headers=HEADERS
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        return {
            "final_url":
                response.geturl(),

            "status_code":
                getattr(
                    response,
                    "status",
                    None
                ),

            "content_type":
                response.headers.get(
                    "Content-Type",
                    ""
                ),

            "text":
                response.read().decode(
                    "utf-8",
                    errors="replace"
                ),
        }


def clean(value):
    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value)
    ).strip()


def first_text(item, names):
    for name in names:

        element = item.find(
            name
        )

        if (
            element is not None
            and element.text
        ):
            return clean(
                element.text
            )

    return None


def parse_items(xml_text):
    try:
        root = ET.fromstring(
            xml_text
        )
    except ET.ParseError:
        return []

    records = []

    for item in root.findall(
        ".//item"
    ):

        enclosure = item.find(
            "enclosure"
        )

        records.append({
            "title":
                first_text(
                    item,
                    ["title"]
                ),

            "guid":
                first_text(
                    item,
                    ["guid"]
                ),

            "pub_date":
                first_text(
                    item,
                    ["pubDate"]
                ),

            "link":
                first_text(
                    item,
                    ["link"]
                ),

            "description":
                first_text(
                    item,
                    ["description"]
                ),

            "enclosure_url":
                (
                    enclosure.attrib.get(
                        "url"
                    )
                    if enclosure is not None
                    else None
                ),

            "enclosure_type":
                (
                    enclosure.attrib.get(
                        "type"
                    )
                    if enclosure is not None
                    else None
                ),

            "raw":
                ET.tostring(
                    item,
                    encoding="unicode"
                ),
        })

    return records


def score_item(
    target,
    item
):
    haystack = " ".join(
        clean(value)
        for value in [
            item.get("title"),
            item.get("guid"),
            item.get("pub_date"),
            item.get("link"),
            item.get("description"),
            item.get(
                "enclosure_url"
            ),
            item.get("raw"),
        ]
        if value
    ).lower()

    hits = []

    for field in [
        "story_id",
        "player_story_id",
        "audio_id",
    ]:
        value = target[field]

        if value in haystack:
            hits.append(
                field
            )

    title = target[
        "title"
    ].lower()

    if title in haystack:
        hits.append(
            "exact_title"
        )

    return hits


report = {
    "method":
        "npr-legacy-feed-probe",

    "show_id":
        "510325",

    "targets":
        TARGETS,

    "feeds":
        [],
}


for feed in FEEDS:

    print()
    print(
        "Fetching:",
        feed["name"]
    )

    result = {
        "name":
            feed["name"],

        "requested_url":
            feed["url"],

        "status":
            None,

        "item_count":
            0,

        "target_results":
            [],
    }

    try:
        response = fetch(
            feed["url"]
        )

    except HTTPError as exc:

        result[
            "status"
        ] = f"http_{exc.code}"

        result[
            "error"
        ] = str(exc)

        report[
            "feeds"
        ].append(
            result
        )

        continue

    except (
        URLError,
        TimeoutError,
    ) as exc:

        result[
            "status"
        ] = "request_error"

        result[
            "error"
        ] = str(exc)

        report[
            "feeds"
        ].append(
            result
        )

        continue

    except Exception as exc:

        result[
            "status"
        ] = "error"

        result[
            "error"
        ] = str(exc)

        report[
            "feeds"
        ].append(
            result
        )

        continue

    result[
        "status"
    ] = "fetched"

    result[
        "final_url"
    ] = response[
        "final_url"
    ]

    result[
        "status_code"
    ] = response[
        "status_code"
    ]

    result[
        "content_type"
    ] = response[
        "content_type"
    ]

    xml_text = response[
        "text"
    ]

    items = parse_items(
        xml_text
    )

    result[
        "item_count"
    ] = len(items)

    raw_lower = (
        xml_text.lower()
    )

    for target in TARGETS:

        matches = []

        for item in items:

            hits = score_item(
                target,
                item
            )

            if hits:

                matches.append({
                    "hits":
                        hits,

                    "title":
                        item.get(
                            "title"
                        ),

                    "pub_date":
                        item.get(
                            "pub_date"
                        ),

                    "guid":
                        item.get(
                            "guid"
                        ),

                    "link":
                        item.get(
                            "link"
                        ),

                    "enclosure_url":
                        item.get(
                            "enclosure_url"
                        ),

                    "enclosure_type":
                        item.get(
                            "enclosure_type"
                        ),
                })

        raw_hits = {}

        for field in [
            "story_id",
            "player_story_id",
            "audio_id",
        ]:

            value = target[
                field
            ]

            raw_hits[
                field
            ] = (
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

    report[
        "feeds"
    ].append(
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
print(
    "=============================="
)
print(
    "Legacy NPR feed probe complete"
)

for feed in report[
    "feeds"
]:

    print()
    print(
        feed["name"],
        "status:",
        feed["status"],
        "items:",
        feed["item_count"]
    )

    for target in feed.get(
        "target_results",
        []
    ):

        print(
            target["target"][
                "title"
            ],
            "matches:",
            target[
                "matching_item_count"
            ],
            "hits:",
            target[
                "raw_xml_id_hits"
            ]
        )

print()
print(
    "Saved:",
    OUTPUT_FILE
)
print(
    "=============================="
)
