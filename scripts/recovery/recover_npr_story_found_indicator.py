#!/usr/bin/env python3
from pathlib import Path

import html
import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]



INPUT_FILE = str(REPO_ROOT / "indicator_unresolved_web_discovery.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_story_found_recovery.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRStoryRecovery/1.0)"
    )
}

TIMEOUT = 30
RETRIES = 3


def fetch(url, max_bytes=2500000, range_request=False):
    last_error = None

    headers = dict(HEADERS)

    if range_request:
        headers["Range"] = "bytes=0-4095"

    for attempt in range(1, RETRIES + 1):
        try:
            request = Request(url, headers=headers)

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

    value = html.unescape(
        str(value)
    )

    value = value.replace(
        "\\/",
        "/"
    )

    value = value.replace(
        "\\u0026",
        "&"
    )

    return value


def unique(values):
    output = []

    for value in values:
        if value and value not in output:
            output.append(value)

    return output


def extract_player_embeds(page):
    page = clean_text(page)

    patterns = [
        r'https?://(?:www\.)?npr\.org/player/embed/\d+/\d+',
        r'/player/embed/\d+/\d+',
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

    normalized = []

    for value in found:
        if value.startswith("/"):
            value = (
                "https://www.npr.org"
                + value
            )

        normalized.append(
            value
        )

    return unique(normalized)


def extract_ids_from_embed(url):
    match = re.search(
        r'/player/embed/(\d+)/(\d+)',
        url
    )

    if not match:
        return {
            "player_story_id": None,
            "audio_id": None,
        }

    return {
        "player_story_id":
            match.group(1),

        "audio_id":
            match.group(2),
    }


def extract_npr_audio(page):
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


def validate_npr_audio(url):
    try:
        response = fetch(
            url,
            range_request=True
        )

        final_url = response[
            "final_url"
        ]

        content_type = (
            response[
                "content_type"
            ]
            or ""
        ).lower()

        is_audio = (
            content_type.startswith(
                "audio/"
            )
            and "ondemand.npr.org"
            in final_url.lower()
            and "/indicator/"
            in final_url.lower()
        )

        return {
            "candidate_url":
                url,

            "status_code":
                response[
                    "status_code"
                ],

            "final_url":
                final_url,

            "content_type":
                response[
                    "content_type"
                ],

            "sample_size":
                len(
                    response["data"]
                ),

            "is_valid_npr_indicator_audio":
                is_audio,
        }

    except Exception as exc:
        return {
            "candidate_url":
                url,

            "is_valid_npr_indicator_audio":
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
        + "&fl=timestamp,original"
        + "&limit=20"
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
            dict(zip(header, row))
            for row in data[1:]
        ]

    except Exception:
        return []


def fetch_wayback_captures(url):
    rows = wayback_cdx(url)

    captures = []

    for row in rows[:6]:

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

            item[
                "player_embeds"
            ] = extract_player_embeds(
                response["text"]
            )

            item[
                "audio_candidates"
            ] = extract_npr_audio(
                response["text"]
            )

        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)

        captures.append(item)

    return captures


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:

    discovery = json.load(file)


targets = [
    item
    for item in discovery.get(
        "results",
        []
    )
    if item.get(
        "status"
    ) == "npr_story_found"
]


results = []


for index, target in enumerate(
    targets,
    start=1
):

    print()
    print(
        f"[{index}/{len(targets)}]",
        target.get("date"),
        target.get("title")
    )

    result = {
        "date":
            target.get(
                "date"
            ),

        "title":
            target.get(
                "title"
            ),

        "npr_story_urls":
            target.get(
                "npr_story_urls",
                []
            ),

        "story_pages":
            [],

        "player_pages":
            [],

        "wayback_players":
            [],

        "validated_audio":
            [],

        "status":
            "not_recovered",
    }


    all_player_embeds = []
    all_audio_candidates = []


    # ------------------------------------------
    # NPR story pages
    # ------------------------------------------

    for story_url in result[
        "npr_story_urls"
    ][:10]:

        story_report = {
            "url":
                story_url,

            "status":
                None,
        }

        try:
            page = fetch_text(
                story_url
            )

            story_report[
                "status"
            ] = "fetched"

            story_report[
                "final_url"
            ] = page[
                "final_url"
            ]

            embeds = extract_player_embeds(
                page["text"]
            )

            audio = extract_npr_audio(
                page["text"]
            )

            story_report[
                "player_embeds"
            ] = embeds

            story_report[
                "audio_candidates"
            ] = audio

            all_player_embeds.extend(
                embeds
            )

            all_audio_candidates.extend(
                audio
            )

        except Exception as exc:

            story_report[
                "status"
            ] = "error"

            story_report[
                "error"
            ] = str(exc)

        result[
            "story_pages"
        ].append(
            story_report
        )


    all_player_embeds = unique(
        all_player_embeds
    )


    # ------------------------------------------
    # Player pages
    # ------------------------------------------

    for player_url in (
        all_player_embeds[:10]
    ):

        ids = extract_ids_from_embed(
            player_url
        )

        player_report = {
            "player_url":
                player_url,

            **ids,

            "status":
                None,

            "audio_candidates":
                [],
        }

        try:
            page = fetch_text(
                player_url
            )

            player_report[
                "status"
            ] = "fetched"

            player_report[
                "final_url"
            ] = page[
                "final_url"
            ]

            audio = extract_npr_audio(
                page["text"]
            )

            player_report[
                "audio_candidates"
            ] = audio

            all_audio_candidates.extend(
                audio
            )

        except Exception as exc:

            player_report[
                "status"
            ] = "error"

            player_report[
                "error"
            ] = str(exc)

        result[
            "player_pages"
        ].append(
            player_report
        )


        # --------------------------------------
        # Wayback player captures
        # --------------------------------------

        captures = (
            fetch_wayback_captures(
                player_url
            )
        )

        result[
            "wayback_players"
        ].append({
            "player_url":
                player_url,

            "captures":
                captures,
        })

        for capture in captures:

            all_audio_candidates.extend(
                capture.get(
                    "audio_candidates",
                    []
                )
            )


    all_audio_candidates = unique(
        all_audio_candidates
    )


    # ------------------------------------------
    # Validate only NPR Indicator audio
    # ------------------------------------------

    validated = []

    for candidate in (
        all_audio_candidates[:50]
    ):

        check = validate_npr_audio(
            candidate
        )

        if check.get(
            "is_valid_npr_indicator_audio"
        ):
            validated.append(
                check
            )


    # Deduplicate by final URL
    deduped = []

    seen = set()

    for audio in validated:

        key = audio.get(
            "final_url"
        )

        if key and key not in seen:
            seen.add(key)
            deduped.append(
                audio
            )


    result[
        "validated_audio"
    ] = deduped


    if deduped:

        result[
            "status"
        ] = "recovered_npr_audio"

    elif all_player_embeds:

        result[
            "status"
        ] = "player_found_no_audio"

    else:

        result[
            "status"
        ] = "story_found_no_player"


    print(
        "  ->",
        result[
            "status"
        ]
    )

    results.append(
        result
    )


summary = {}

for item in results:

    status = item[
        "status"
    ]

    summary[
        status
    ] = (
        summary.get(
            status,
            0
        )
        + 1
    )


report = {
    "method":
        "recover-npr-story-found-indicator-episodes",

    "input_count":
        len(targets),

    "summary":
        summary,

    "results":
        results,
}


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
    "NPR STORY RECOVERY COMPLETE"
)

print(
    "Input:",
    len(targets)
)

for key, value in sorted(
    summary.items()
):
    print(
        key + ":",
        value
    )

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
