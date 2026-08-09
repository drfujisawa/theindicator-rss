#!/usr/bin/env python3

import html
import json
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


OUTPUT_FILE = "indicator_wbur_traffic_tariff_probe.json"

PAGE_URL = (
    "https://www.wbur.org/npr/"
    "716127469/the-traffic-tariff"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorWBURProbe/1.0)"
    )
}

TIMEOUT = 30


def fetch_text(url):
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
    if not value:
        return ""

    value = html.unescape(
        value
    )

    value = value.replace(
        "\\/",
        "/"
    )

    value = value.replace(
        "\\u0026",
        "&"
    )

    value = value.replace(
        "\\u003d",
        "="
    )

    return value


def unique(values):
    output = []

    for value in values:
        value = clean(value).strip()

        if (
            value
            and value not in output
        ):
            output.append(
                value
            )

    return output


def extract_candidates(page):
    patterns = [
        r'https?://[^"\'<>\s\\]+\.mp3'
        r'(?:\?[^"\'<>\s\\]*)?',

        r'https?://ondemand\.npr\.org/'
        r'[^"\'<>\s\\]+',

        r'https?://play\.podtrac\.com/'
        r'[^"\'<>\s\\]+',

        r'https?://prfx\.byspotify\.com/'
        r'[^"\'<>\s\\]+',

        r'https?://[^"\'<>\s\\]+'
        r'(?:download|audio|enclosure)'
        r'[^"\'<>\s\\]*',
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

    return unique(
        found
    )


def extract_interesting_lines(page):
    lines = []

    for line in page.splitlines():
        lower = line.lower()

        if any(
            marker in lower
            for marker in [
                ".mp3",
                "ondemand.npr.org",
                "podtrac",
                "byspotify",
                "audio",
                "download",
                "enclosure",
                "716127469",
                "730102905",
            ]
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

    return unique(
        lines
    )[:200]


def probe_audio(url):
    request = Request(
        url,
        headers={
            **HEADERS,
            "Range":
                "bytes=0-4095",
        },
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        sample = response.read(
            4096
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

            "content_length":
                response.headers.get(
                    "Content-Length"
                ),

            "sample_size":
                len(sample),
        }


def is_audio_type(value):
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


report = {
    "method":
        "wbur-traffic-tariff-audio-probe",

    "page_url":
        PAGE_URL,

    "npr_player_story_id":
        "716127469",

    "known_audio_id":
        "730102905",

    "status":
        None,

    "candidates":
        [],

    "validated_audio":
        [],
}


try:
    response = fetch_text(
        PAGE_URL
    )

except Exception as exc:
    report["status"] = (
        "page_fetch_failed"
    )

    report["error"] = str(exc)

else:
    report["status"] = "page_fetched"

    report["final_page_url"] = (
        response["final_url"]
    )

    report["page_content_type"] = (
        response["content_type"]
    )

    page = clean(
        response["text"]
    )

    candidates = extract_candidates(
        page
    )

    report[
        "candidate_count"
    ] = len(candidates)

    report[
        "interesting_lines"
    ] = extract_interesting_lines(
        page
    )

    for candidate in candidates:

        item = {
            "candidate_url":
                candidate
        }

        try:
            probe = probe_audio(
                candidate
            )

            item.update(
                probe
            )

            item["is_audio"] = (
                is_audio_type(
                    probe.get(
                        "content_type"
                    )
                )
            )

            if item[
                "is_audio"
            ]:
                report[
                    "validated_audio"
                ].append(
                    item
                )

        except HTTPError as exc:
            item["status"] = (
                f"http_{exc.code}"
            )

            item["error"] = str(exc)

        except (
            URLError,
            TimeoutError,
        ) as exc:
            item["status"] = (
                "request_error"
            )

            item["error"] = str(exc)

        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)

        report[
            "candidates"
        ].append(
            item
        )


report[
    "validated_audio_count"
] = len(
    report["validated_audio"]
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
    "WBUR Traffic Tariff probe complete"
)

print(
    "Status:",
    report["status"]
)

print(
    "Candidates:",
    report.get(
        "candidate_count",
        0
    )
)

print(
    "Validated audio:",
    report[
        "validated_audio_count"
    ]
)

for item in report[
    "validated_audio"
]:
    print()
    print(
        "AUDIO:",
        item.get(
            "final_url"
        )
    )

    print(
        "Type:",
        item.get(
            "content_type"
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
