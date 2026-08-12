#!/usr/bin/env python3
from pathlib import Path

import html
import json
import re
import time
import xml.etree.ElementTree as ET

from difflib import SequenceMatcher
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]



INPUT_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_story_found_recovery.json")
OUTPUT_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_story_identity_strict.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorStrictIdentityResolver/1.0)"
    )
}

TIMEOUT = 30
RETRIES = 3
SEARCH_DELAY = 1.0


# --------------------------------------------------
# Network
# --------------------------------------------------

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


# --------------------------------------------------
# Text/title helpers
# --------------------------------------------------

def normalize_title(value):

    if not value:
        return ""

    value = html.unescape(
        str(value)
    ).lower()

    value = value.replace(
        "&",
        " and "
    )

    value = value.replace(
        "’",
        "'"
    )

    value = re.sub(
        r"\s*\|\s*npr.*$",
        "",
        value
    )

    value = re.sub(
        r"\s*-\s*npr.*$",
        "",
        value
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return " ".join(
        value.split()
    )


def title_similarity(a, b):

    a = normalize_title(a)
    b = normalize_title(b)

    if not a or not b:
        return 0.0

    return round(
        SequenceMatcher(
            None,
            a,
            b
        ).ratio(),
        3
    )


def unique(values):

    output = []

    for value in values:

        if value and value not in output:
            output.append(value)

    return output


# --------------------------------------------------
# Metadata extraction
# --------------------------------------------------

def extract_meta(page):

    page = html.unescape(
        page
    )

    result = {
        "html_title": None,
        "og_title": None,
        "canonical": None,
        "dates": [],
    }


    title_match = re.search(
        r"<title[^>]*>(.*?)</title>",
        page,
        re.I | re.S
    )

    if title_match:
        result["html_title"] = re.sub(
            r"\s+",
            " ",
            title_match.group(1)
        ).strip()


    patterns = [
        (
            "og_title",
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)'
        ),
        (
            "og_title",
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']'
        ),
        (
            "canonical",
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)'
        ),
        (
            "canonical",
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']'
        ),
    ]


    for key, pattern in patterns:

        match = re.search(
            pattern,
            page,
            re.I
        )

        if match and not result[key]:
            result[key] = match.group(1)


    date_patterns = [
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"dateModified"\s*:\s*"([^"]+)"',
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        r'<time[^>]+datetime=["\']([^"\']+)',
    ]


    for pattern in date_patterns:

        for value in re.findall(
            pattern,
            page,
            re.I
        ):

            match = re.search(
                r"(\d{4}-\d{2}-\d{2})",
                value
            )

            if match:
                result["dates"].append(
                    match.group(1)
                )


    result["dates"] = unique(
        result["dates"]
    )

    return result


def extract_player_embeds(page):

    page = (
        html.unescape(page)
        .replace("\\/", "/")
    )

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


def extract_npr_audio(page):

    page = (
        html.unescape(page)
        .replace("\\/", "/")
        .replace("\\u0026", "&")
    )

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


# --------------------------------------------------
# Search
# --------------------------------------------------

def bing_npr_search(title, date):

    year = (
        date[:4]
        if date
        else ""
    )

    queries = [
        f'site:npr.org "{title}" "{year}"',
        f'site:npr.org "{title}" "The Indicator"',
    ]

    collected = []
    reports = []


    for query in queries:

        url = (
            "https://www.bing.com/search"
            "?format=rss&q="
            + quote(query)
        )

        report = {
            "query": query,
            "status": None,
            "results": [],
        }

        try:
            response = fetch_text(url)

            root = ET.fromstring(
                response["text"]
            )

            for item in root.findall(
                ".//item"
            ):

                link = item.findtext(
                    "link"
                )

                title_text = item.findtext(
                    "title"
                )

                if (
                    link
                    and "npr.org"
                    in link.lower()
                ):

                    report[
                        "results"
                    ].append({
                        "title":
                            title_text,

                        "url":
                            link,
                    })

                    collected.append(
                        link
                    )

            report["status"] = "fetched"

        except Exception as exc:

            report["status"] = "error"
            report["error"] = str(exc)

        reports.append(report)

        time.sleep(
            SEARCH_DELAY
        )


    return (
        unique(collected),
        reports
    )


# --------------------------------------------------
# Story page scoring
# --------------------------------------------------

def score_story_page(
    expected_title,
    expected_date,
    requested_url,
    page
):

    metadata = extract_meta(
        page
    )

    candidate_titles = unique([
        metadata.get(
            "og_title"
        ),
        metadata.get(
            "html_title"
        ),
    ])


    best_similarity = 0.0
    best_title = None

    for candidate_title in candidate_titles:

        similarity = title_similarity(
            expected_title,
            candidate_title
        )

        if similarity > best_similarity:

            best_similarity = similarity
            best_title = candidate_title


    date_match = (
        expected_date
        in metadata[
            "dates"
        ]
    )


    url_date_match = (
        expected_date.replace(
            "-",
            "/"
        )
        in requested_url
    )


    score = 0
    reasons = []


    if best_similarity >= 0.90:

        score += 7
        reasons.append(
            "very_strong_title_match"
        )

    elif best_similarity >= 0.75:

        score += 5
        reasons.append(
            "strong_title_match"
        )

    elif best_similarity >= 0.60:

        score += 2
        reasons.append(
            "moderate_title_match"
        )


    if date_match:

        score += 5
        reasons.append(
            "exact_published_date_match"
        )

    elif url_date_match:

        score += 3
        reasons.append(
            "date_matches_url"
        )


    canonical = (
        metadata.get(
            "canonical"
        )
        or ""
    )

    if "npr.org" in canonical.lower():

        score += 1
        reasons.append(
            "canonical_is_npr"
        )


    # Conservative acceptance:
    # strong title + some date evidence,
    # or nearly exact title with exact date.
    qualified = (
        (
            best_similarity >= 0.75
            and (
                date_match
                or url_date_match
            )
        )
        or (
            best_similarity >= 0.90
            and date_match
        )
    )


    return {
        "score":
            score,

        "qualified":
            qualified,

        "best_title":
            best_title,

        "title_similarity":
            best_similarity,

        "dates":
            metadata[
                "dates"
            ],

        "date_match":
            date_match,

        "url_date_match":
            url_date_match,

        "canonical":
            canonical,

        "reasons":
            reasons,
    }


# --------------------------------------------------
# Wayback
# --------------------------------------------------

def wayback_cdx(url):

    query = (
        "https://web.archive.org/"
        "cdx/search/cdx"
        "?url="
        + quote(
            url,
            safe=""
        )
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
            not isinstance(
                data,
                list
            )
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


def probe_wayback(url):

    rows = wayback_cdx(
        url
    )

    results = []

    for row in rows[:6]:

        timestamp = row.get(
            "timestamp"
        )

        original = row.get(
            "original"
        )

        if not timestamp or not original:
            continue

        archived_url = (
            "https://web.archive.org/web/"
            f"{timestamp}id_/"
            f"{original}"
        )

        item = {
            "timestamp":
                timestamp,

            "archived_url":
                archived_url,

            "status":
                None,
        }

        try:
            page = fetch_text(
                archived_url
            )

            item["status"] = "fetched"

            item["player_embeds"] = (
                extract_player_embeds(
                    page["text"]
                )
            )

            item["audio_candidates"] = (
                extract_npr_audio(
                    page["text"]
                )
            )

        except Exception as exc:

            item["status"] = "error"
            item["error"] = str(exc)

        results.append(item)

    return results


# --------------------------------------------------
# Audio validation
# --------------------------------------------------

def validate_audio(url):

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


        valid = (
            content_type.startswith(
                "audio/"
            )
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

            "final_url":
                final_url,

            "status_code":
                response[
                    "status_code"
                ],

            "content_type":
                response[
                    "content_type"
                ],

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


# --------------------------------------------------
# Load targets
# --------------------------------------------------

with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:

    previous = json.load(file)


targets = previous.get(
    "results",
    []
)


results = []


for index, target in enumerate(
    targets,
    start=1
):

    expected_title = target.get(
        "title"
    )

    expected_date = target.get(
        "date"
    )


    print()
    print(
        f"[{index}/{len(targets)}]",
        expected_date,
        expected_title
    )


    existing_urls = target.get(
        "npr_story_urls",
        []
    )


    searched_urls, search_reports = (
        bing_npr_search(
            expected_title,
            expected_date
        )
    )


    candidate_story_urls = unique(
        existing_urls
        + searched_urls
    )


    story_candidates = []

    qualified_stories = []


    for story_url in (
        candidate_story_urls[:20]
    ):

        report = {
            "url":
                story_url,

            "status":
                None,
        }

        try:
            page = fetch_text(
                story_url
            )

            report["status"] = "fetched"
            report["final_url"] = (
                page[
                    "final_url"
                ]
            )

            scoring = score_story_page(
                expected_title,
                expected_date,
                page[
                    "final_url"
                ],
                page[
                    "text"
                ]
            )

            report.update(
                scoring
            )


            if scoring[
                "qualified"
            ]:

                report[
                    "player_embeds"
                ] = (
                    extract_player_embeds(
                        page[
                            "text"
                        ]
                    )
                )

                report[
                    "audio_candidates"
                ] = (
                    extract_npr_audio(
                        page[
                            "text"
                        ]
                    )
                )

                qualified_stories.append(
                    report
                )


        except Exception as exc:

            report["status"] = "error"
            report["error"] = str(exc)


        story_candidates.append(
            report
        )


    qualified_stories.sort(
        key=lambda item:
            item.get(
                "score",
                0
            ),
        reverse=True
    )


    all_players = []

    all_audio_candidates = []

    wayback_story_reports = []


    # Only trust qualified NPR stories.
    for story in qualified_stories[:3]:

        all_players.extend(
            story.get(
                "player_embeds",
                []
            )
        )

        all_audio_candidates.extend(
            story.get(
                "audio_candidates",
                []
            )
        )


        archived = probe_wayback(
            story.get(
                "final_url"
            )
            or story[
                "url"
            ]
        )


        wayback_story_reports.append({
            "story_url":
                story.get(
                    "final_url"
                )
                or story[
                    "url"
                ],

            "captures":
                archived,
        })


        for capture in archived:

            all_players.extend(
                capture.get(
                    "player_embeds",
                    []
                )
            )

            all_audio_candidates.extend(
                capture.get(
                    "audio_candidates",
                    []
                )
            )


    all_players = unique(
        all_players
    )


    player_reports = []
    wayback_player_reports = []


    for player_url in all_players[:10]:

        player_report = {
            "player_url":
                player_url,

            "status":
                None,
        }


        try:
            page = fetch_text(
                player_url
            )

            player_report[
                "status"
            ] = "fetched"

            player_report[
                "audio_candidates"
            ] = (
                extract_npr_audio(
                    page[
                        "text"
                    ]
                )
            )

            all_audio_candidates.extend(
                player_report[
                    "audio_candidates"
                ]
            )

        except Exception as exc:

            player_report[
                "status"
            ] = "error"

            player_report[
                "error"
            ] = str(exc)


        player_reports.append(
            player_report
        )


        archived = probe_wayback(
            player_url
        )


        wayback_player_reports.append({
            "player_url":
                player_url,

            "captures":
                archived,
        })


        for capture in archived:

            all_audio_candidates.extend(
                capture.get(
                    "audio_candidates",
                    []
                )
            )


    all_audio_candidates = unique(
        all_audio_candidates
    )


    validated = []


    for candidate in (
        all_audio_candidates[:50]
    ):

        check = validate_audio(
            candidate
        )

        if check.get(
            "valid_npr_indicator_audio"
        ):
            validated.append(
                check
            )


    # Deduplicate final audio URLs.
    deduped_audio = []
    seen_audio = set()


    for item in validated:

        key = item.get(
            "final_url"
        )

        if key and key not in seen_audio:

            seen_audio.add(
                key
            )

            deduped_audio.append(
                item
            )


    if deduped_audio:

        status = (
            "recovered_npr_audio"
        )

    elif qualified_stories and all_players:

        status = (
            "strong_story_and_player_no_audio"
        )

    elif qualified_stories:

        status = (
            "strong_npr_story_no_player"
        )

    else:

        status = (
            "no_strong_npr_story_match"
        )


    result = {
        "date":
            expected_date,

        "title":
            expected_title,

        "status":
            status,

        "search_reports":
            search_reports,

        "candidate_story_count":
            len(
                story_candidates
            ),

        "qualified_story_count":
            len(
                qualified_stories
            ),

        "qualified_stories":
            qualified_stories,

        "story_candidates":
            story_candidates,

        "wayback_story_reports":
            wayback_story_reports,

        "player_reports":
            player_reports,

        "wayback_player_reports":
            wayback_player_reports,

        "validated_audio":
            deduped_audio,
    }


    print(
        "  ->",
        status,
        "| strong stories:",
        len(
            qualified_stories
        ),
        "| players:",
        len(
            all_players
        ),
        "| audio:",
        len(
            deduped_audio
        )
    )


    results.append(
        result
    )


# --------------------------------------------------
# Summary
# --------------------------------------------------

summary = {}


for item in results:

    key = item[
        "status"
    ]

    summary[key] = (
        summary.get(
            key,
            0
        )
        + 1
    )


report = {
    "method":
        "strict-npr-story-identity-resolution",

    "input_count":
        len(results),

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
    "STRICT NPR IDENTITY RESOLUTION COMPLETE"
)
print(
    "Input:",
    len(results)
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
