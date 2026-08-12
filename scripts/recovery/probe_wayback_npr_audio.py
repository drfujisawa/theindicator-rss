#!/usr/bin/env python3
from pathlib import Path

import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
REPO_ROOT = Path(__file__).resolve().parents[2]



OUTPUT_FILE = str(REPO_ROOT / "indicator_wayback_npr_probe.json")
TARGETS = [
    {
        "title": "Paranormal Profits",
        "date": "2018-10-31",
        "npr_url": (
            "https://www.npr.org/sections/money/"
            "2018/10/31/662708285/paranormal-profits"
        ),
        "story_id": "662708285",
        "player_story_id": "662706955",
        "audio_id": "662707862",
    },
    {
        "title": "The Traffic Tariff",
        "date": "2019-04-22",
        "npr_url": (
            "https://www.npr.org/2019/04/22/"
            "716132270/the-traffic-tariff"
        ),
        "story_id": "716132270",
        "player_story_id": "716127469",
        "audio_id": "730102905",
    },
]

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorWaybackProbe/1.0)"
    )
}


def fetch(url):
    request = Request(
        url,
        headers=HEADERS,
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:
        return {
            "final_url": response.geturl(),
            "status_code": getattr(
                response,
                "status",
                None
            ),
            "content_type": response.headers.get(
                "Content-Type",
                ""
            ),
            "text": response.read().decode(
                "utf-8",
                errors="replace"
            ),
        }


def cdx_url(original_url):
    return (
        "https://web.archive.org/cdx/search/cdx"
        "?url="
        + quote(original_url, safe="")
        + "&output=json"
        + "&filter=statuscode:200"
        + "&filter=mimetype:text/html"
        + "&collapse=digest"
        + "&fl=timestamp,original,statuscode,mimetype,digest"
    )


def unique(values):
    result = []

    for value in values:
        if value and value not in result:
            result.append(value)

    return result


def extract_audio_clues(page):
    mp3_urls = unique(
        re.findall(
            r'https?://[^"\'<>\s\\]+\.mp3'
            r'(?:\?[^"\'<>\s\\]*)?',
            page,
            re.I
        )
    )

    ondemand_urls = unique(
        re.findall(
            r'https?://ondemand\.npr\.org/'
            r'[^"\'<>\s\\]+',
            page,
            re.I
        )
    )

    podtrac_urls = unique(
        re.findall(
            r'https?://play\.podtrac\.com/'
            r'[^"\'<>\s\\]+',
            page,
            re.I
        )
    )

    player_embeds = unique(
        re.findall(
            r'(?:https?://www\.npr\.org)?'
            r'/player/embed/'
            r'[^"\'<>\s\\]+',
            page,
            re.I
        )
    )

    all_audioish = unique(
        re.findall(
            r'https?://[^"\'<>\s\\]+'
            r'(?:audio|player|ondemand|podtrac)'
            r'[^"\'<>\s\\]*',
            page,
            re.I
        )
    )

    return {
        "mp3_urls": mp3_urls[:50],
        "ondemand_urls": ondemand_urls[:50],
        "podtrac_urls": podtrac_urls[:50],
        "player_embeds": player_embeds[:50],
        "audioish_urls": all_audioish[:100],
    }


def find_id_context(page, ids):
    result = {}

    for value in ids:
        if not value:
            continue

        matches = []

        start = 0

        while True:
            pos = page.find(value, start)

            if pos == -1:
                break

            left = max(0, pos - 500)
            right = min(
                len(page),
                pos + len(value) + 500
            )

            snippet = re.sub(
                r"\s+",
                " ",
                page[left:right]
            )

            if snippet not in matches:
                matches.append(snippet)

            start = pos + len(value)

        result[value] = matches[:20]

    return result


report = {
    "method": "wayback-original-npr-page-audio-probe",
    "targets": [],
}


for target in TARGETS:
    print()
    print(
        "Looking up Wayback captures for:",
        target["title"]
    )

    result = {
        "title": target["title"],
        "date": target["date"],
        "npr_url": target["npr_url"],
        "story_id": target["story_id"],
        "player_story_id": target["player_story_id"],
        "audio_id": target["audio_id"],
        "cdx_status": None,
        "capture_count": 0,
        "captures": [],
    }

    try:
        cdx = fetch(
            cdx_url(
                target["npr_url"]
            )
        )

        result["cdx_status"] = "fetched"
        result["cdx_final_url"] = (
            cdx["final_url"]
        )

        data = json.loads(
            cdx["text"]
        )

    except Exception as exc:
        result["cdx_status"] = "error"
        result["cdx_error"] = str(exc)
        report["targets"].append(result)
        continue

    rows = []

    if (
        isinstance(data, list)
        and len(data) > 1
    ):
        header = data[0]

        for row in data[1:]:
            rows.append(
                dict(zip(header, row))
            )

    result["capture_count"] = len(rows)

    print(
        "Captures found:",
        len(rows)
    )

    # Try oldest and newest captures, plus a few
    # evenly distributed ones if lots exist.
    selected = []

    if rows:
        selected.append(rows[0])

    if len(rows) > 1:
        selected.append(rows[-1])

    if len(rows) > 4:
        selected.append(
            rows[len(rows) // 3]
        )
        selected.append(
            rows[
                (len(rows) * 2) // 3
            ]
        )

    seen_timestamps = set()

    for capture in selected:
        timestamp = capture.get(
            "timestamp"
        )

        if (
            not timestamp
            or timestamp
            in seen_timestamps
        ):
            continue

        seen_timestamps.add(
            timestamp
        )

        archived_url = (
            "https://web.archive.org/web/"
            f"{timestamp}id_/"
            f"{target['npr_url']}"
        )

        print(
            "Fetching capture:",
            timestamp
        )

        capture_result = {
            "timestamp": timestamp,
            "archived_url": archived_url,
            "status": None,
        }

        try:
            archived = fetch(
                archived_url
            )

            page = archived["text"]

            capture_result[
                "status"
            ] = "fetched"

            capture_result[
                "final_url"
            ] = archived[
                "final_url"
            ]

            capture_result[
                "content_type"
            ] = archived[
                "content_type"
            ]

            capture_result[
                "audio_clues"
            ] = extract_audio_clues(
                page
            )

            capture_result[
                "id_context"
            ] = find_id_context(
                page,
                [
                    target[
                        "story_id"
                    ],
                    target[
                        "player_story_id"
                    ],
                    target[
                        "audio_id"
                    ],
                ]
            )

        except HTTPError as exc:
            capture_result[
                "status"
            ] = f"http_{exc.code}"

            capture_result[
                "error"
            ] = str(exc)

        except (
            URLError,
            TimeoutError,
        ) as exc:
            capture_result[
                "status"
            ] = "request_error"

            capture_result[
                "error"
            ] = str(exc)

        except Exception as exc:
            capture_result[
                "status"
            ] = "error"

            capture_result[
                "error"
            ] = str(exc)

        result["captures"].append(
            capture_result
        )

    report["targets"].append(
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
    "================================"
)
print(
    "Wayback NPR probe complete"
)
print(
    "Saved:",
    OUTPUT_FILE
)
print(
    "================================"
)
