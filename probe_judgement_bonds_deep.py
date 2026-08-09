#!/usr/bin/env python3

import html
import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen


OUTPUT_FILE = "indicator_judgement_bonds_probe.json"

TARGET_TITLE = "Judgement Bonds"
TARGET_DATE = "2018-10-29"

AFFILIATE_URL = (
    "https://www.delmarvapublicmedia.org/"
    "business/2018-10-29/judgement-bonds"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorJudgementBondsProbe/1.0)"
    )
}

TIMEOUT = 30
RETRIES = 3


def fetch(url, max_bytes=3000000, range_request=False):

    headers = dict(HEADERS)

    if range_request:
        headers["Range"] = "bytes=0-4095"

    last_error = None

    for attempt in range(1, RETRIES + 1):

        try:
            request = Request(
                url,
                headers=headers
            )

            with urlopen(
                request,
                timeout=TIMEOUT
            ) as response:

                data = response.read(
                    4096 if range_request else max_bytes
                )

                return {
                    "status_code":
                        getattr(
                            response,
                            "status",
                            None
                        ),

                    "final_url":
                        response.geturl(),

                    "content_type":
                        response.headers.get(
                            "Content-Type",
                            ""
                        ),

                    "data":
                        data,
                }

        except Exception as exc:

            last_error = exc

            if attempt < RETRIES:
                time.sleep(attempt * 2)

    raise last_error


def fetch_text(url):

    response = fetch(url)

    response["text"] = (
        response["data"]
        .decode(
            "utf-8",
            errors="replace"
        )
    )

    return response


def clean_text(value):

    if not value:
        return ""

    return (
        html.unescape(
            str(value)
        )
        .replace("\\/", "/")
        .replace("\\u0026", "&")
    )


def unique(values):

    output = []

    for value in values:

        if value and value not in output:
            output.append(value)

    return output


def extract_urls(page):

    page = clean_text(page)

    urls = re.findall(
        r'https?://[^"\'<>\s\\]+',
        page,
        re.I
    )

    return unique(urls)


def extract_npr_story_urls(page):

    return [
        url
        for url in extract_urls(page)
        if "npr.org/" in url.lower()
    ]


def extract_player_embeds(page):

    page = clean_text(page)

    found = re.findall(
        r'(?:https?://(?:www\.)?npr\.org)?'
        r'/player/embed/\d+/\d+',
        page,
        re.I
    )

    output = []

    for value in found:

        if value.startswith("/"):
            value = "https://www.npr.org" + value

        output.append(value)

    return unique(output)


def extract_numeric_ids(page):

    page = clean_text(page)

    ids = unique(
        re.findall(
            r'(?<!\d)(\d{8,10})(?!\d)',
            page
        )
    )

    return ids[:200]


def extract_audio_urls(page):

    page = clean_text(page)

    patterns = [
        r'https?://ondemand\.npr\.org/'
        r'[^"\'<>\s\\]+',

        r'https?://prfx\.byspotify\.com/'
        r'[^"\'<>\s\\]+',

        r'https?://play\.podtrac\.com/'
        r'[^"\'<>\s\\]+',

        r'https?://[^"\'<>\s\\]+\.mp3'
        r'(?:\?[^"\'<>\s\\]*)?',
    ]

    found = []

    for pattern in patterns:

        found.extend(
            re.findall(
                pattern,
                page,
                re.I
            )
        )

    return unique(found)


def extract_interesting_lines(page):

    page = clean_text(page)

    lines = []

    markers = [
        "npr.org",
        "ondemand.npr.org",
        "player/embed",
        ".mp3",
        "podtrac",
        "byspotify",
        "audio",
        "iframe",
        "story",
        "episode",
        "judgement bonds",
        "indicator",
    ]

    for line in page.splitlines():

        lower = line.lower()

        if any(
            marker in lower
            for marker in markers
        ):

            line = re.sub(
                r"\s+",
                " ",
                line
            ).strip()

            if line:
                lines.append(
                    line[:8000]
                )

    return unique(lines)[:300]


def validate_audio(url):

    try:

        response = fetch(
            url,
            range_request=True
        )

        final_url = (
            response["final_url"]
            or ""
        )

        content_type = (
            response["content_type"]
            or ""
        ).lower()

        valid = (
            content_type.startswith("audio/")
            and "ondemand.npr.org"
            in final_url.lower()
            and "/indicator/"
            in final_url.lower()
            and ".mp3"
            in final_url.lower()
        )

        return {
            "candidate_url":
                url,

            "status_code":
                response["status_code"],

            "final_url":
                final_url,

            "content_type":
                response["content_type"],

            "sample_size":
                len(response["data"]),

            "valid_npr_indicator_audio":
                valid,
        }

    except Exception as exc:

        return {
            "candidate_url":
                url,

            "valid_npr_indicator_audio":
                False,

            "error":
                str(exc),
        }


def wayback_cdx(url):

    query = (
        "https://web.archive.org/cdx/search/cdx"
        "?url="
        + quote(url, safe="")
        + "&output=json"
        + "&filter=statuscode:200"
        + "&collapse=digest"
        + "&fl=timestamp,original,statuscode,mimetype"
        + "&limit=50"
    )

    try:

        response = fetch_text(query)

        data = json.loads(
            response["text"]
        )

        if (
            not isinstance(data, list)
            or len(data) < 2
        ):
            return []

        header = data[0]

        return [
            dict(zip(header, row))
            for row in data[1:]
        ]

    except Exception:
        return []


def probe_wayback(url):

    rows = wayback_cdx(url)

    captures = []

    for row in rows[:12]:

        timestamp = row.get(
            "timestamp"
        )

        original = row.get(
            "original"
        )

        if not timestamp or not original:
            continue

        archive_url = (
            "https://web.archive.org/web/"
            f"{timestamp}id_/"
            f"{original}"
        )

        item = {
            "timestamp":
                timestamp,

            "original":
                original,

            "archive_url":
                archive_url,

            "status":
                None,
        }

        try:

            response = fetch_text(
                archive_url
            )

            item["status"] = "fetched"

            page = response["text"]

            item["npr_story_urls"] = (
                extract_npr_story_urls(page)
            )

            item["player_embeds"] = (
                extract_player_embeds(page)
            )

            item["numeric_ids"] = (
                extract_numeric_ids(page)
            )

            item["audio_candidates"] = (
                extract_audio_urls(page)
            )

            item["interesting_lines"] = (
                extract_interesting_lines(page)
            )

        except Exception as exc:

            item["status"] = "error"
            item["error"] = str(exc)

        captures.append(item)

    return captures


report = {
    "method":
        "deep-probe-judgement-bonds-affiliate-and-wayback",

    "target_title":
        TARGET_TITLE,

    "target_date":
        TARGET_DATE,

    "affiliate_url":
        AFFILIATE_URL,

    "live_page":
        {},

    "wayback_captures":
        [],

    "candidate_player_pages":
        [],

    "validated_audio":
        [],
}


# --------------------------------------------------
# Live affiliate page
# --------------------------------------------------

try:

    live = fetch_text(
        AFFILIATE_URL
    )

    page = live["text"]

    report["live_page"] = {
        "status":
            "fetched",

        "status_code":
            live["status_code"],

        "final_url":
            live["final_url"],

        "content_type":
            live["content_type"],

        "npr_story_urls":
            extract_npr_story_urls(page),

        "player_embeds":
            extract_player_embeds(page),

        "numeric_ids":
            extract_numeric_ids(page),

        "audio_candidates":
            extract_audio_urls(page),

        "interesting_lines":
            extract_interesting_lines(page),
    }

except Exception as exc:

    report["live_page"] = {
        "status":
            "error",

        "error":
            str(exc),
    }


# --------------------------------------------------
# Wayback copies of the affiliate page
# --------------------------------------------------

report["wayback_captures"] = (
    probe_wayback(
        AFFILIATE_URL
    )
)


all_players = []
all_audio = []


for source in [
    report["live_page"]
]:

    all_players.extend(
        source.get(
            "player_embeds",
            []
        )
    )

    all_audio.extend(
        source.get(
            "audio_candidates",
            []
        )
    )


for capture in report[
    "wayback_captures"
]:

    all_players.extend(
        capture.get(
            "player_embeds",
            []
        )
    )

    all_audio.extend(
        capture.get(
            "audio_candidates",
            []
        )
    )


all_players = unique(
    all_players
)

all_audio = unique(
    all_audio
)


# --------------------------------------------------
# Probe any NPR player pages we discover
# --------------------------------------------------

for player_url in all_players[:20]:

    player_report = {
        "player_url":
            player_url,

        "live":
            {},

        "wayback":
            [],
    }

    try:

        page = fetch_text(
            player_url
        )

        player_report["live"] = {
            "status":
                "fetched",

            "final_url":
                page["final_url"],

            "audio_candidates":
                extract_audio_urls(
                    page["text"]
                ),

            "numeric_ids":
                extract_numeric_ids(
                    page["text"]
                ),

            "interesting_lines":
                extract_interesting_lines(
                    page["text"]
                ),
        }

        all_audio.extend(
            player_report[
                "live"
            ][
                "audio_candidates"
            ]
        )

    except Exception as exc:

        player_report["live"] = {
            "status":
                "error",

            "error":
                str(exc),
        }


    player_report["wayback"] = (
        probe_wayback(
            player_url
        )
    )


    for capture in player_report[
        "wayback"
    ]:

        all_audio.extend(
            capture.get(
                "audio_candidates",
                []
            )
        )


    report[
        "candidate_player_pages"
    ].append(
        player_report
    )


# --------------------------------------------------
# Validate all NPR-looking audio
# --------------------------------------------------

all_audio = unique(
    all_audio
)


for candidate in all_audio[:100]:

    check = validate_audio(
        candidate
    )

    if check.get(
        "valid_npr_indicator_audio"
    ):

        report[
            "validated_audio"
        ].append(
            check
        )


report[
    "validated_audio_count"
] = len(
    report[
        "validated_audio"
    ]
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
    "JUDGEMENT BONDS PROBE COMPLETE"
)

print(
    "Wayback captures:",
    len(
        report[
            "wayback_captures"
        ]
    )
)

print(
    "Player pages found:",
    len(
        report[
            "candidate_player_pages"
        ]
    )
)

print(
    "Validated NPR Indicator audio:",
    report[
        "validated_audio_count"
    ]
)

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
