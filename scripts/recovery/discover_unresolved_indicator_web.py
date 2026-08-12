#!/usr/bin/env python3
from pathlib import Path

import html
import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]



INPUT_FILE = str(REPO_ROOT / "indicator_unresolved_strict_review.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_unresolved_web_discovery.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorHistoryDiscovery/1.0)"
    )
}

TIMEOUT = 25
SEARCH_DELAY = 1.0


def fetch(url, max_bytes=2000000):
    request = Request(
        url,
        headers=HEADERS
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        data = response.read(
            max_bytes
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


def normalize_title(value):
    if not value:
        return ""

    value = value.lower()
    value = value.replace("&", " and ")
    value = value.replace("’", "'")

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return " ".join(
        value.split()
    )


def clean_url(value):
    if not value:
        return None

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

    value = value.strip(
        "\"' "
    )

    if not value.startswith(
        ("http://", "https://")
    ):
        return None

    return value


def unique(values):
    output = []

    for value in values:
        if (
            value
            and value not in output
        ):
            output.append(value)

    return output


def host(url):
    try:
        return (
            urlparse(url)
            .hostname
            or ""
        ).lower()

    except Exception:
        return ""


def rejected_stream(url):
    lower = url.lower()

    return any(
        marker in lower
        for marker in [
            "streamtheworld",
            "livestream",
            "live-stream",
            "icecast",
            "shoutcast",
        ]
    )


def looks_audio(url):
    lower = url.lower()

    if rejected_stream(url):
        return False

    return (
        ".mp3" in lower
        or "ondemand.npr.org" in lower
        or "prfx.byspotify.com" in lower
        or "play.podtrac.com" in lower
    )


def is_audio_content_type(value):
    value = (
        value
        or ""
    ).lower()

    return value.startswith(
        "audio/"
    )


def validate_audio(url):
    try:
        request = Request(
            url,
            headers={
                **HEADERS,
                "Range":
                    "bytes=0-4095",
            }
        )

        with urlopen(
            request,
            timeout=TIMEOUT
        ) as response:

            sample = response.read(
                4096
            )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    ""
                )
            )

            final_url = (
                response.geturl()
            )

            return {
                "candidate_url":
                    url,

                "status_code":
                    getattr(
                        response,
                        "status",
                        None
                    ),

                "final_url":
                    final_url,

                "content_type":
                    content_type,

                "sample_size":
                    len(sample),

                "is_audio":
                    (
                        is_audio_content_type(
                            content_type
                        )
                        and not rejected_stream(
                            final_url
                        )
                    ),
            }

    except Exception as exc:

        return {
            "candidate_url":
                url,

            "is_audio":
                False,

            "error":
                str(exc),
        }


def bing_rss_search(query):
    url = (
        "https://www.bing.com/search"
        "?format=rss&q="
        + quote(query)
    )

    try:
        response = fetch_text(
            url
        )

        root = ET.fromstring(
            response["text"]
        )

    except Exception as exc:
        return {
            "query":
                query,

            "status":
                "error",

            "error":
                str(exc),

            "results":
                [],
        }

    results = []

    for item in root.findall(
        ".//item"
    ):

        title = item.findtext(
            "title"
        )

        link = item.findtext(
            "link"
        )

        description = item.findtext(
            "description"
        )

        if link:
            results.append({
                "title":
                    title,

                "url":
                    link,

                "description":
                    description,
            })

    return {
        "query":
            query,

        "status":
            "fetched",

        "results":
            results[:10],
    }


def extract_page_clues(page):
    page = (
        html.unescape(page)
        .replace("\\/", "/")
        .replace("\\u0026", "&")
    )

    npr_story_urls = unique(
        clean_url(value)
        for value in re.findall(
            r'https?://(?:www\.)?npr\.org/'
            r'[^"\'<>\s\\]+',
            page,
            re.I
        )
    )

    player_embeds = unique(
        clean_url(value)
        for value in re.findall(
            r'https?://(?:www\.)?npr\.org/'
            r'player/embed/'
            r'[^"\'<>\s\\]+',
            page,
            re.I
        )
    )

    audio_urls = []

    patterns = [
        r'https?://[^"\'<>\s\\]+\.mp3'
        r'(?:\?[^"\'<>\s\\]*)?',

        r'https?://ondemand\.npr\.org/'
        r'[^"\'<>\s\\]+',

        r'https?://prfx\.byspotify\.com/'
        r'[^"\'<>\s\\]+',

        r'https?://play\.podtrac\.com/'
        r'[^"\'<>\s\\]+',
    ]

    for pattern in patterns:

        audio_urls.extend(
            clean_url(value)
            for value in re.findall(
                pattern,
                page,
                re.I
            )
        )

    audio_urls = unique(
        value
        for value in audio_urls
        if (
            value
            and not rejected_stream(
                value
            )
        )
    )

    canonical = re.findall(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
        page,
        re.I
    )

    canonical += re.findall(
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
        page,
        re.I
    )

    return {
        "canonical_urls":
            unique(
                clean_url(value)
                for value in canonical
            ),

        "npr_story_urls":
            npr_story_urls[:50],

        "player_embeds":
            player_embeds[:20],

        "audio_urls":
            audio_urls[:50],
    }


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:

    data = json.load(file)


targets = [
    item
    for item in data.get(
        "results",
        []
    )
    if item.get(
        "strict_status"
    ) not in {
        "possible_duplicate_or_rebroadcast",
    }
]


results = []


for number, target in enumerate(
    targets,
    start=1
):

    title = target.get(
        "title"
    )

    date = target.get(
        "date"
    )

    print()
    print(
        f"[{number}/{len(targets)}]",
        date,
        title
    )

    queries = [
        f'"{title}" NPR "The Indicator"',
        f'"{title}" NPR {date}',
    ]

    search_reports = []

    discovered_pages = []


    for query in queries:

        search = bing_rss_search(
            query
        )

        search_reports.append(
            search
        )

        for result in search.get(
            "results",
            []
        ):

            url = result.get(
                "url"
            )

            if url:
                discovered_pages.append(
                    url
                )

        time.sleep(
            SEARCH_DELAY
        )


    # Include anything useful we already knew.
    discovered_pages.extend(
        target.get(
            "useful_page_urls",
            []
        )
    )

    discovered_pages = unique(
        discovered_pages
    )


    page_reports = []

    all_audio_candidates = []

    all_npr_urls = []

    all_player_embeds = []


    for page_url in discovered_pages[:12]:

        report = {
            "requested_url":
                page_url,

            "status":
                None,
        }

        try:
            page = fetch_text(
                page_url
            )

            report[
                "status"
            ] = "fetched"

            report[
                "status_code"
            ] = page[
                "status_code"
            ]

            report[
                "final_url"
            ] = page[
                "final_url"
            ]

            clues = (
                extract_page_clues(
                    page["text"]
                )
            )

            report[
                "clues"
            ] = clues

            all_audio_candidates.extend(
                clues[
                    "audio_urls"
                ]
            )

            all_npr_urls.extend(
                clues[
                    "npr_story_urls"
                ]
            )

            all_player_embeds.extend(
                clues[
                    "player_embeds"
                ]
            )

        except Exception as exc:

            report[
                "status"
            ] = "error"

            report[
                "error"
            ] = str(exc)

        page_reports.append(
            report
        )


    all_audio_candidates = unique(
        all_audio_candidates
    )

    all_npr_urls = unique(
        all_npr_urls
    )

    all_player_embeds = unique(
        all_player_embeds
    )


    validated = []

    for candidate in (
        all_audio_candidates[:30]
    ):

        if not looks_audio(
            candidate
        ):
            continue

        check = validate_audio(
            candidate
        )

        if check.get(
            "is_audio"
        ):
            validated.append(
                check
            )


    # Prefer NPR-hosted validated results.
    validated.sort(
        key=lambda item:
            0
            if "ondemand.npr.org"
            in (
                item.get(
                    "final_url",
                    ""
                )
            )
            else 1
    )


    if validated:

        status = (
            "episode_audio_recovered"
        )

    elif all_player_embeds:

        status = (
            "npr_player_found"
        )

    elif all_npr_urls:

        status = (
            "npr_story_found"
        )

    elif discovered_pages:

        status = (
            "pages_found_no_npr_identity"
        )

    else:

        status = (
            "no_search_results"
        )


    result = {
        "date":
            date,

        "title":
            title,

        "reference_year":
            target.get(
                "reference_year"
            ),

        "reference_episode":
            target.get(
                "reference_episode"
            ),

        "status":
            status,

        "searches":
            search_reports,

        "discovered_page_count":
            len(
                discovered_pages
            ),

        "page_reports":
            page_reports,

        "npr_story_urls":
            all_npr_urls,

        "npr_player_embeds":
            all_player_embeds,

        "audio_candidate_count":
            len(
                all_audio_candidates
            ),

        "validated_audio":
            validated,
    }


    print(
        "  ->",
        status
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
        "web-discovery-for-unresolved-indicator-reference-episodes",

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
    "WEB DISCOVERY COMPLETE"
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
