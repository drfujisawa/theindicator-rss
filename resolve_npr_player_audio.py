#!/usr/bin/env python3

import html
import json
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

INPUT_FILE = "indicator_npr_audio_review.json"
OUTPUT_FILE = "indicator_npr_player_resolution.json"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRPlayerResolver/1.0)"
    )
}


def fetch(url):
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=TIMEOUT) as response:
        return (
            response.geturl(),
            response.headers.get("Content-Type", ""),
            response.read().decode(
                "utf-8",
                errors="replace"
            ),
        )


def clean(value):
    if not value:
        return ""

    value = html.unescape(value)
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")

    return value.strip()


def unique(values):
    output = []

    for value in values:
        value = clean(value)

        if value and value not in output:
            output.append(value)

    return output


def extract_audio_candidates(page):
    patterns = [
        r'https?://[^"\'<>\s\\]+\.mp3(?:\?[^"\'<>\s\\]*)?',
        r'https?://ondemand\.npr\.org/[^"\'<>\s\\]+',
        r'https?://play\.podtrac\.com/[^"\'<>\s\\]+',
        r'https?://prfx\.byspotify\.com/[^"\'<>\s\\]+',
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


def probe_audio(url):
    request = Request(
        url,
        headers={
            **HEADERS,
            "Range": "bytes=0-4095",
        },
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        final_url = response.geturl()

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        status = getattr(
            response,
            "status",
            None
        )

        sample = response.read(4096)

    return {
        "status_code": status,
        "final_url": final_url,
        "content_type": content_type,
        "sample_size": len(sample),
    }


def is_audio_content_type(value):
    if not value:
        return False

    value = value.lower()

    return (
        value.startswith("audio/")
        or "mpeg" in value
        or "mp3" in value
        or "aac" in value
        or "m4a" in value
    )


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    review = json.load(file)


records = review.get("records", [])

results = []


for record in records:
    print()
    print(
        record.get("reference_date"),
        "-",
        record.get("reference_title")
    )

    player_url = record.get("audio_url")

    result = {
        "reference_date":
            record.get("reference_date"),

        "reference_title":
            record.get("reference_title"),

        "npr_story_id":
            record.get("npr_story_id"),

        "npr_url":
            record.get("npr_url"),

        "player_url":
            player_url,

        "status":
            None,

        "audio_candidates":
            [],
    }

    try:
        (
            final_player_url,
            player_content_type,
            player_page,
        ) = fetch(player_url)

    except Exception as exc:
        result["status"] = "player_fetch_failed"
        result["error"] = str(exc)
        results.append(result)
        continue

    result["final_player_url"] = final_player_url
    result[
        "player_content_type"
    ] = player_content_type

    candidates = extract_audio_candidates(
        player_page
    )

    result["audio_candidates"] = candidates

    validated = []

    for candidate in candidates:

        try:
            probe = probe_audio(candidate)

        except HTTPError as exc:
            probe = {
                "candidate_url": candidate,
                "status": f"http_{exc.code}",
                "error": str(exc),
            }

        except (
            URLError,
            TimeoutError,
        ) as exc:
            probe = {
                "candidate_url": candidate,
                "status": "request_error",
                "error": str(exc),
            }

        except Exception as exc:
            probe = {
                "candidate_url": candidate,
                "status": "error",
                "error": str(exc),
            }

        else:
            probe[
                "candidate_url"
            ] = candidate

            probe[
                "is_audio"
            ] = is_audio_content_type(
                probe.get(
                    "content_type"
                )
            )

            if probe["is_audio"]:
                validated.append(probe)

        result.setdefault(
            "candidate_probes",
            []
        ).append(probe)

    if validated:
        result["status"] = "resolved"
        result["best_audio"] = validated[0]

        print(
            "  RESOLVED:",
            validated[0].get(
                "final_url"
            )
        )

    else:
        result["status"] = "not_resolved"
        print("  Not resolved.")

    results.append(result)


report = {
    "method":
        "npr-player-page-audio-resolution",

    "input_count":
        len(records),

    "resolved_count":
        sum(
            1
            for item in results
            if item.get("status")
            == "resolved"
        ),

    "unresolved_count":
        sum(
            1
            for item in results
            if item.get("status")
            != "resolved"
        ),

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
print("==============================")
print("NPR player resolution complete")
print("Input:", report["input_count"])
print("Resolved:", report["resolved_count"])
print("Unresolved:", report["unresolved_count"])
print("Saved:", OUTPUT_FILE)
print("==============================")
