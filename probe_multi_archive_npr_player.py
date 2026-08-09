#!/usr/bin/env python3

import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


OUTPUT_FILE = "indicator_multi_archive_player_probe.json"

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
        "(compatible; IndicatorMultiArchiveProbe/1.0)"
    )
}

TIMEOUT = 45
RETRIES = 3


def fetch(url):
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            request = Request(
                url,
                headers=HEADERS
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

        except Exception as exc:
            last_error = exc

            if attempt < RETRIES:
                time.sleep(
                    attempt * 3
                )

    raise last_error


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


def extract_audio_clues(page):
    return {
        "mp3_urls": unique(
            re.findall(
                r'https?://[^"\'<>\s\\]+\.mp3'
                r'(?:\?[^"\'<>\s\\]*)?',
                page,
                re.I
            )
        )[:100],

        "ondemand_urls": unique(
            re.findall(
                r'https?://ondemand\.npr\.org/'
                r'[^"\'<>\s\\]+',
                page,
                re.I
            )
        )[:100],

        "podtrac_urls": unique(
            re.findall(
                r'https?://play\.podtrac\.com/'
                r'[^"\'<>\s\\]+',
                page,
                re.I
            )
        )[:100],

        "spotify_urls": unique(
            re.findall(
                r'https?://prfx\.byspotify\.com/'
                r'[^"\'<>\s\\]+',
                page,
                re.I
            )
        )[:100],

        "npr_player_urls": unique(
            re.findall(
                r'https?://(?:www\.)?npr\.org/'
                r'player/[^"\'<>\s\\]+',
                page,
                re.I
            )
        )[:100],
    }


def wayback_cdx_urls(player_url, player_story_id, audio_id):
    variants = [
        player_url,

        player_url.replace(
            "https://",
            "http://"
        ),

        player_url + "/",

        (
            "https://www.npr.org/player/embed/"
            f"{player_story_id}/*"
        ),

        (
            "https://www.npr.org/player/*"
            f"{audio_id}*"
        ),
    ]

    return [
        (
            "https://web.archive.org/"
            "cdx/search/cdx"
            "?url="
            + quote(
                variant,
                safe=""
            )
            + "&output=json"
            + "&filter=statuscode:200"
            + "&collapse=digest"
            + "&fl=timestamp,original,statuscode,mimetype,digest"
            + "&limit=50"
        )
        for variant in variants
    ]


def parse_cdx(text):
    try:
        data = json.loads(text)
    except Exception:
        return []

    if (
        not isinstance(data, list)
        or len(data) < 2
    ):
        return []

    header = data[0]

    return [
        dict(
            zip(
                header,
                row
            )
        )
        for row in data[1:]
    ]


def probe_wayback(target, player_url):
    result = {
        "archive": "wayback",
        "queries": [],
        "captures": [],
    }

    seen = set()

    for query_url in wayback_cdx_urls(
        player_url,
        target["player_story_id"],
        target["audio_id"]
    ):
        query_result = {
            "query_url": query_url,
            "status": None,
            "capture_count": 0,
        }

        try:
            response = fetch(
                query_url
            )

            rows = parse_cdx(
                response["text"]
            )

            query_result["status"] = "fetched"
            query_result[
                "capture_count"
            ] = len(rows)

        except Exception as exc:
            query_result["status"] = "error"
            query_result["error"] = str(exc)
            result["queries"].append(
                query_result
            )
            continue

        result["queries"].append(
            query_result
        )

        for row in rows:
            timestamp = row.get(
                "timestamp"
            )

            original = row.get(
                "original"
            )

            key = (
                timestamp,
                original
            )

            if (
                not timestamp
                or not original
                or key in seen
            ):
                continue

            seen.add(key)

            result["captures"].append({
                "timestamp":
                    timestamp,

                "original":
                    original,
            })

    #
    # Only fetch a few actual archived pages.
    #
    fetched = []

    for capture in result[
        "captures"
    ][:10]:

        archived_url = (
            "https://web.archive.org/web/"
            f"{capture['timestamp']}id_/"
            f"{capture['original']}"
        )

        item = {
            **capture,
            "archived_url":
                archived_url,
            "status":
                None,
        }

        try:
            response = fetch(
                archived_url
            )

            item["status"] = "fetched"
            item["content_type"] = (
                response[
                    "content_type"
                ]
            )

            item["audio_clues"] = (
                extract_audio_clues(
                    response["text"]
                )
            )

        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)

        fetched.append(item)

    result[
        "fetched_captures"
    ] = fetched

    return result


def arquivo_text_search_url(player_url):
    #
    # Arquivo.pt's API can search preserved page
    # content/metadata. We search the exact NPR
    # player URL as text.
    #
    return (
        "https://arquivo.pt/textsearch?"
        "versionHistory="
        + quote(
            player_url,
            safe=""
        )
        + "&maxItems=50"
    )


def probe_arquivo(target, player_url):
    result = {
        "archive": "arquivo.pt",
        "status": None,
        "query_url":
            arquivo_text_search_url(
                player_url
            ),
        "results": [],
    }

    try:
        response = fetch(
            result["query_url"]
        )

        result["status"] = "fetched"
        result["content_type"] = (
            response[
                "content_type"
            ]
        )

        text = response[
            "text"
        ]

        #
        # Keep a compact copy of anything that
        # contains our relevant IDs or audio URLs.
        #
        lines = []

        for line in text.splitlines():
            lower = line.lower()

            if (
                target["player_story_id"]
                in line
                or target["audio_id"]
                in line
                or "ondemand.npr.org"
                in lower
                or ".mp3"
                in lower
                or "npr.org/player"
                in lower
            ):
                line = re.sub(
                    r"\s+",
                    " ",
                    line
                ).strip()

                if line:
                    lines.append(
                        line[:5000]
                    )

        result[
            "interesting_lines"
        ] = unique(
            lines
        )[:100]

        result[
            "audio_clues"
        ] = extract_audio_clues(
            text
        )

        result[
            "raw_length"
        ] = len(text)

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


report = {
    "method":
        "multi-web-archive-npr-player-probe",

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
        "Target:",
        target["title"]
    )

    target_result = {
        **target,

        "player_url":
            player_url,

        "wayback":
            probe_wayback(
                target,
                player_url
            ),

        "arquivo_pt":
            probe_arquivo(
                target,
                player_url
            ),
    }

    report["targets"].append(
        target_result
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
    "Multi-archive probe complete"
)
print(
    "Saved:",
    OUTPUT_FILE
)
print(
    "================================"
)
