#!/usr/bin/env python3

import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen


OUTPUT_FILE = "indicator_wayback_player_probe.json"

TARGETS = [
    {
        "title": "Paranormal Profits",
        "date": "2018-10-31",
        "player_story_id": "662706955",
        "audio_id": "662707862",
    },
    {
        "title": "The Traffic Tariff",
        "date": "2019-04-22",
        "player_story_id": "716127469",
        "audio_id": "730102905",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorWaybackPlayerProbe/1.0)"
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
            "text": response.read().decode(
                "utf-8",
                errors="replace"
            ),
        }


def unique(values):
    output = []

    for value in values:
        value = (
            str(value)
            .replace("\\/", "/")
            .replace("\\u0026", "&")
            .replace("&amp;", "&")
        )

        if value and value not in output:
            output.append(value)

    return output


def cdx_url(url):
    return (
        "https://web.archive.org/cdx/search/cdx"
        "?url="
        + quote(url, safe="")
        + "&output=json"
        + "&filter=statuscode:200"
        + "&collapse=digest"
        + "&fl=timestamp,original,statuscode,mimetype,digest"
    )


def extract_audio(page):
    patterns = {
        "mp3_urls": [
            r'https?://[^"\'<>\s\\]+\.mp3(?:\?[^"\'<>\s\\]*)?',
        ],

        "ondemand_urls": [
            r'https?://ondemand\.npr\.org/[^"\'<>\s\\]+',
        ],

        "podtrac_urls": [
            r'https?://play\.podtrac\.com/[^"\'<>\s\\]+',
        ],

        "spotify_redirect_urls": [
            r'https?://prfx\.byspotify\.com/[^"\'<>\s\\]+',
        ],

        "audioish_urls": [
            r'https?://[^"\'<>\s\\]+'
            r'(?:audio|mp3|ondemand|podtrac|stream)'
            r'[^"\'<>\s\\]*',
        ],
    }

    result = {}

    for key, regexes in patterns.items():
        found = []

        for regex in regexes:
            found.extend(
                re.findall(
                    regex,
                    page,
                    re.I
                )
            )

        result[key] = unique(found)[:100]

    return result


def id_context(page, ids):
    result = {}

    for value in ids:
        snippets = []
        start = 0

        while True:
            position = page.find(value, start)

            if position == -1:
                break

            left = max(
                0,
                position - 1000
            )

            right = min(
                len(page),
                position + len(value) + 1000
            )

            snippet = re.sub(
                r"\s+",
                " ",
                page[left:right]
            )

            if snippet not in snippets:
                snippets.append(snippet)

            start = (
                position
                + len(value)
            )

        result[value] = snippets[:25]

    return result


report = {
    "method":
        "wayback-npr-player-page-probe",

    "targets":
        [],
}


for target in TARGETS:

    player_url = (
        "https://www.npr.org/player/embed/"
        f"{target['player_story_id']}/"
        f"{target['audio_id']}"
    )

    print()
    print(
        "Checking:",
        target["title"]
    )

    result = {
        "title":
            target["title"],

        "date":
            target["date"],

        "player_story_id":
            target[
                "player_story_id"
            ],

        "audio_id":
            target["audio_id"],

        "player_url":
            player_url,

        "capture_count":
            0,

        "captures":
            [],
    }

    try:
        cdx_response = fetch(
            cdx_url(player_url)
        )

        data = json.loads(
            cdx_response["text"]
        )

    except Exception as exc:
        result["cdx_error"] = str(exc)
        report["targets"].append(
            result
        )
        continue

    rows = []

    if (
        isinstance(data, list)
        and len(data) > 1
    ):
        header = data[0]

        for row in data[1:]:
            rows.append(
                dict(
                    zip(
                        header,
                        row
                    )
                )
            )

    result[
        "capture_count"
    ] = len(rows)

    print(
        "Player captures:",
        len(rows)
    )

    #
    # Try up to 8 captures, prioritizing
    # the oldest ones closest to publication.
    #
    selected = rows[:8]

    for row in selected:

        timestamp = row.get(
            "timestamp"
        )

        if not timestamp:
            continue

        archived_url = (
            "https://web.archive.org/web/"
            f"{timestamp}id_/"
            f"{player_url}"
        )

        capture = {
            "timestamp":
                timestamp,

            "archived_url":
                archived_url,

            "status":
                None,
        }

        try:
            archived = fetch(
                archived_url
            )

            page = archived[
                "text"
            ]

            capture[
                "status"
            ] = "fetched"

            capture[
                "content_type"
            ] = archived[
                "content_type"
            ]

            capture[
                "audio_clues"
            ] = extract_audio(
                page
            )

            capture[
                "id_context"
            ] = id_context(
                page,
                [
                    target[
                        "player_story_id"
                    ],
                    target[
                        "audio_id"
                    ],
                ]
            )

        except Exception as exc:

            capture[
                "status"
            ] = "error"

            capture[
                "error"
            ] = str(exc)

        result[
            "captures"
        ].append(
            capture
        )

    report[
        "targets"
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
    "================================"
)

print(
    "Wayback NPR player probe complete"
)

for target in report[
    "targets"
]:

    print()
    print(
        target["title"]
    )

    print(
        "Captures:",
        target[
            "capture_count"
        ]
    )

    for capture in target.get(
        "captures",
        []
    ):
        clues = capture.get(
            "audio_clues",
            {}
        )

        print(
            capture.get(
                "timestamp"
            ),
            "MP3:",
            len(
                clues.get(
                    "mp3_urls",
                    []
                )
            ),
            "ondemand:",
            len(
                clues.get(
                    "ondemand_urls",
                    []
                )
            )
        )

print()
print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
