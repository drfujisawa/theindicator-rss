#!/usr/bin/env python3
from pathlib import Path

import html
import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]



OUTPUT_FILE = str(REPO_ROOT / "indicator_judgement_bonds_npr_story_id_probe.json")
TARGET_TITLE = "Judgement Bonds"
TARGET_DATE = "2018-10-29"
NPR_STORY_ID = "661827210"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorJudgementBondsNPRIDProbe/1.0)"
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
                    4096
                    if range_request
                    else max_bytes
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
            value = (
                "https://www.npr.org"
                + value
            )

        output.append(value)

    return unique(output)


def extract_audio_urls(page):
    page = clean_text(page)

    patterns = [
        r'https?://ondemand\.npr\.org/'
        r'[^"\'<>\s\\]+\.mp3'
        r'(?:\?[^"\'<>\s\\]*)?',

        r'https?://prfx\.byspotify\.com/'
        r'[^"\'<>\s\\]+\.mp3'
        r'(?:\?[^"\'<>\s\\]*)?',

        r'https?://play\.podtrac\.com/'
        r'[^"\'<>\s\\]+\.mp3'
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


def extract_npr_urls(page):
    page = clean_text(page)

    found = re.findall(
        r'https?://(?:www\.)?npr\.org/'
        r'[^"\'<>\s\\]+',
        page,
        re.I
    )

    return unique(found)


def extract_numeric_ids(page):
    page = clean_text(page)

    return unique(
        re.findall(
            r'(?<!\d)(\d{8,10})(?!\d)',
            page
        )
    )[:200]


def extract_interesting_lines(page):
    page = clean_text(page)

    lines = []

    markers = [
        NPR_STORY_ID,
        "judgement bonds",
        "indicator",
        "player/embed",
        "ondemand.npr.org",
        ".mp3",
        "story",
        "audio",
        "podtrac",
        "byspotify",
    ]

    for line in page.splitlines():
        lower = line.lower()

        if any(
            marker.lower() in lower
            for marker in markers
        ):
            line = re.sub(
                r"\s+",
                " ",
                line
            ).strip()

            if line:
                lines.append(
                    line[:10000]
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
                len(
                    response["data"]
                ),

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


def wayback_cdx(pattern):
    query = (
        "https://web.archive.org/cdx/search/cdx"
        "?url="
        + quote(
            pattern,
            safe=""
        )
        + "&output=json"
        + "&filter=statuscode:200"
        + "&collapse=urlkey"
        + "&fl=timestamp,original,statuscode,mimetype,digest"
        + "&limit=200"
    )

    try:
        response = fetch_text(
            query
        )

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
            dict(
                zip(
                    header,
                    row
                )
            )
            for row in data[1:]
        ]

    except Exception:
        return []


def fetch_wayback_capture(
    timestamp,
    original
):
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

        item["final_url"] = (
            response["final_url"]
        )

        item["content_type"] = (
            response["content_type"]
        )

        page = response["text"]

        item["npr_urls"] = (
            extract_npr_urls(page)
        )

        item["player_embeds"] = (
            extract_player_embeds(page)
        )

        item["audio_candidates"] = (
            extract_audio_urls(page)
        )

        item["numeric_ids"] = (
            extract_numeric_ids(page)
        )

        item["interesting_lines"] = (
            extract_interesting_lines(page)
        )

    except Exception as exc:
        item["status"] = "error"
        item["error"] = str(exc)

    return item


PATTERNS = [
    (
        "npr_anywhere_https",
        f"https://www.npr.org/*{NPR_STORY_ID}*"
    ),
    (
        "npr_anywhere_http",
        f"http://www.npr.org/*{NPR_STORY_ID}*"
    ),
    (
        "npr_sections_https",
        f"https://www.npr.org/sections/*/*{NPR_STORY_ID}*"
    ),
    (
        "npr_player_embed_story_id",
        f"https://www.npr.org/player/embed/{NPR_STORY_ID}/*"
    ),
    (
        "npr_player_anywhere",
        f"https://www.npr.org/player/*{NPR_STORY_ID}*"
    ),
]


report = {
    "method":
        "targeted-wayback-probe-for-npr-story-id",

    "target_title":
        TARGET_TITLE,

    "target_date":
        TARGET_DATE,

    "npr_story_id":
        NPR_STORY_ID,

    "queries":
        [],

    "unique_captures":
        [],

    "player_pages":
        [],

    "validated_audio":
        [],
}


all_rows = []


for name, pattern in PATTERNS:
    print()
    print(
        "Searching:",
        pattern
    )

    rows = wayback_cdx(
        pattern
    )

    report[
        "queries"
    ].append({
        "name":
            name,

        "pattern":
            pattern,

        "capture_count":
            len(rows),

        "rows":
            rows,
    })

    all_rows.extend(
        rows
    )


# --------------------------------------------------
# Deduplicate captures
# --------------------------------------------------

seen_capture = set()
unique_rows = []


for row in all_rows:
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
        timestamp
        and original
        and key not in seen_capture
    ):
        seen_capture.add(key)
        unique_rows.append(row)


# Prefer older captures first.
unique_rows.sort(
    key=lambda row:
        row.get(
            "timestamp",
            ""
        )
)


all_players = []
all_audio = []


for row in unique_rows[:30]:
    capture = fetch_wayback_capture(
        row["timestamp"],
        row["original"]
    )

    report[
        "unique_captures"
    ].append(
        capture
    )

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


# --------------------------------------------------
# Probe discovered player pages directly + Wayback
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
        response = fetch_text(
            player_url
        )

        player_report["live"] = {
            "status":
                "fetched",

            "final_url":
                response["final_url"],

            "audio_candidates":
                extract_audio_urls(
                    response["text"]
                ),

            "numeric_ids":
                extract_numeric_ids(
                    response["text"]
                ),

            "interesting_lines":
                extract_interesting_lines(
                    response["text"]
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


    player_rows = wayback_cdx(
        player_url
    )

    for row in player_rows[:8]:
        capture = fetch_wayback_capture(
            row["timestamp"],
            row["original"]
        )

        player_report[
            "wayback"
        ].append(
            capture
        )

        all_audio.extend(
            capture.get(
                "audio_candidates",
                []
            )
        )


    report[
        "player_pages"
    ].append(
        player_report
    )


# --------------------------------------------------
# Validate NPR Indicator audio
# --------------------------------------------------

all_audio = unique(
    all_audio
)

seen_final_audio = set()


for candidate in all_audio[:100]:
    check = validate_audio(
        candidate
    )

    if check.get(
        "valid_npr_indicator_audio"
    ):
        final_url = (
            check.get(
                "final_url"
            )
        )

        if (
            final_url
            and final_url
            not in seen_final_audio
        ):
            seen_final_audio.add(
                final_url
            )

            report[
                "validated_audio"
            ].append(
                check
            )


report[
    "query_capture_total"
] = sum(
    query[
        "capture_count"
    ]
    for query in report[
        "queries"
    ]
)

report[
    "unique_capture_count"
] = len(
    report[
        "unique_captures"
    ]
)

report[
    "player_page_count"
] = len(
    report[
        "player_pages"
    ]
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
    "JUDGEMENT BONDS NPR STORY-ID PROBE COMPLETE"
)

print(
    "Total query captures:",
    report[
        "query_capture_total"
    ]
)

print(
    "Unique captures fetched:",
    report[
        "unique_capture_count"
    ]
)

print(
    "Player pages found:",
    report[
        "player_page_count"
    ]
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
