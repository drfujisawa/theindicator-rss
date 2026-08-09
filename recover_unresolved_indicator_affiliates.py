#!/usr/bin/env python3

import html
import json
import re
import time
import xml.etree.ElementTree as ET

from difflib import SequenceMatcher
from urllib.parse import quote
from urllib.request import Request, urlopen


STRICT_REVIEW_FILE = "indicator_unresolved_strict_review.json"
WEB_DISCOVERY_FILE = "indicator_unresolved_web_discovery.json"

OUTPUT_FILE = "indicator_unresolved_affiliate_recovery.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorAffiliateRecovery/1.0)"
    )
}

TIMEOUT = 30
RETRIES = 3
SEARCH_DELAY = 1.0


#
# Public-radio / NPR-affiliate domains we have
# encountered or that are useful for this recovery.
#
AFFILIATE_DOMAINS = [
    "wbur.org",
    "wypr.org",
    "wamu.org",
    "kuow.org",
    "kqed.org",
    "mprnews.org",
    "delmarvapublicmedia.org",
    "wunc.org",
    "wbez.org",
    "knpr.org",
    "kpbs.org",
    "kcur.org",
    "wfdd.org",
    "wfae.org",
    "wesa.fm",
    "wrvo.org",
    "wvik.org",
    "wmra.org",
    "wkms.org",
    "wvxu.org",
    "wbaa.org",
    "wgbh.org",
    "ideastream.org",
]


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
                time.sleep(
                    attempt * 2
                )

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
        r"\s*\|\s*.*$",
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


def similarity(a, b):

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


def extract_metadata(page):

    page = html.unescape(page)

    result = {
        "html_title": None,
        "og_title": None,
        "canonical": None,
        "dates": [],
    }


    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        page,
        re.I | re.S
    )

    if match:

        result["html_title"] = re.sub(
            r"\s+",
            " ",
            match.group(1)
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


def extract_npr_story_urls(page):

    page = (
        html.unescape(page)
        .replace("\\/", "/")
    )

    urls = re.findall(
        r'https?://(?:www\.)?npr\.org/'
        r'[^"\'<>\s\\]+',
        page,
        re.I
    )

    return unique(urls)


def extract_player_embeds(page):

    page = (
        html.unescape(page)
        .replace("\\/", "/")
    )

    values = re.findall(
        r'(?:https?://(?:www\.)?npr\.org)?'
        r'/player/embed/\d+/\d+',
        page,
        re.I
    )

    output = []

    for value in values:

        if value.startswith("/"):
            value = (
                "https://www.npr.org"
                + value
            )

        output.append(value)

    return unique(output)


def extract_audio_urls(page):

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


def validate_npr_indicator_audio(url):

    try:

        response = fetch(
            url,
            range_request=True
        )

        final_url = (
            response[
                "final_url"
            ]
            or ""
        )

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
                    response[
                        "data"
                    ]
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


def score_affiliate_page(
    expected_title,
    expected_date,
    metadata,
    final_url
):

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


    for candidate in candidate_titles:

        value = similarity(
            expected_title,
            candidate
        )

        if value > best_similarity:

            best_similarity = value
            best_title = candidate


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
        in (
            final_url
            or ""
        )
    )


    #
    # Some affiliates may not expose a machine-readable
    # publication date. Require a very strong title match
    # in that situation.
    #
    qualified = (
        (
            best_similarity >= 0.75
            and (
                date_match
                or url_date_match
            )
        )
        or (
            best_similarity >= 0.92
        )
    )


    score = 0
    reasons = []


    if best_similarity >= 0.92:

        score += 8
        reasons.append(
            "near_exact_title"
        )

    elif best_similarity >= 0.75:

        score += 5
        reasons.append(
            "strong_title_match"
        )


    if date_match:

        score += 5
        reasons.append(
            "exact_date"
        )

    elif url_date_match:

        score += 3
        reasons.append(
            "date_in_url"
        )


    canonical = (
        metadata.get(
            "canonical"
        )
        or ""
    )


    if "npr.org" in canonical.lower():

        score += 4
        reasons.append(
            "canonical_points_to_npr"
        )


    return {
        "qualified":
            qualified,

        "score":
            score,

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


def bing_search(query):

    url = (
        "https://www.bing.com/search"
        "?format=rss&q="
        + quote(query)
    )

    report = {
        "query":
            query,

        "status":
            None,

        "results":
            [],
    }


    try:

        response = fetch_text(url)

        root = ET.fromstring(
            response[
                "text"
            ]
        )


        for item in root.findall(
            ".//item"
        ):

            link = item.findtext(
                "link"
            )

            title = item.findtext(
                "title"
            )

            description = (
                item.findtext(
                    "description"
                )
            )


            if link:

                report[
                    "results"
                ].append({
                    "title":
                        title,

                    "url":
                        link,

                    "description":
                        description,
                })


        report[
            "status"
        ] = "fetched"


    except Exception as exc:

        report[
            "status"
        ] = "error"

        report[
            "error"
        ] = str(exc)


    return report


#
# Load the clean 52-record review.
#

with open(
    STRICT_REVIEW_FILE,
    "r",
    encoding="utf-8"
) as file:

    strict = json.load(file)


#
# We want the 50 non-duplicate records.
#

targets = [
    item
    for item in strict.get(
        "results",
        []
    )
    if item.get(
        "strict_status"
    ) != (
        "possible_duplicate_or_rebroadcast"
    )
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


    year = (
        date[:4]
        if date
        else ""
    )


    search_reports = []

    candidate_urls = []


    #
    # First search exact title broadly with public-radio
    # terminology.
    #

    broad_queries = [
        f'"{title}" NPR public radio',
        f'"{title}" "{year}" NPR',
    ]


    for query in broad_queries:

        report = bing_search(
            query
        )

        search_reports.append(
            report
        )


        for result in report.get(
            "results",
            []
        ):

            candidate_urls.append(
                result.get(
                    "url"
                )
            )


        time.sleep(
            SEARCH_DELAY
        )


    #
    # Then do targeted affiliate-domain searches.
    #
    # Split into groups so we do not perform 20+
    # searches per episode.
    #

    domain_groups = [
        AFFILIATE_DOMAINS[
            0:7
        ],

        AFFILIATE_DOMAINS[
            7:14
        ],

        AFFILIATE_DOMAINS[
            14:
        ],
    ]


    for group in domain_groups:

        domain_query = " OR ".join(
            f"site:{domain}"
            for domain in group
        )

        query = (
            f'"{title}" '
            f'({domain_query})'
        )


        report = bing_search(
            query
        )

        search_reports.append(
            report
        )


        for search_result in (
            report.get(
                "results",
                []
            )
        ):

            candidate_urls.append(
                search_result.get(
                    "url"
                )
            )


        time.sleep(
            SEARCH_DELAY
        )


    #
    # Include any pages we already knew.
    #

    candidate_urls.extend(
        target.get(
            "useful_page_urls",
            []
        )
    )


    candidate_urls = unique(
        value
        for value in candidate_urls
        if value
    )


    page_reports = []

    qualified_pages = []

    all_npr_story_urls = []

    all_player_embeds = []

    all_audio_candidates = []


    for page_url in (
        candidate_urls[:30]
    ):

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
                "final_url"
            ] = page[
                "final_url"
            ]


            metadata = (
                extract_metadata(
                    page[
                        "text"
                    ]
                )
            )


            scoring = score_affiliate_page(
                title,
                date,
                metadata,
                page[
                    "final_url"
                ]
            )


            report.update(
                scoring
            )


            if scoring[
                "qualified"
            ]:

                npr_urls = (
                    extract_npr_story_urls(
                        page[
                            "text"
                        ]
                    )
                )

                players = (
                    extract_player_embeds(
                        page[
                            "text"
                        ]
                    )
                )

                audio = (
                    extract_audio_urls(
                        page[
                            "text"
                        ]
                    )
                )


                report[
                    "npr_story_urls"
                ] = npr_urls

                report[
                    "player_embeds"
                ] = players

                report[
                    "audio_candidates"
                ] = audio


                qualified_pages.append(
                    report
                )


                all_npr_story_urls.extend(
                    npr_urls
                )

                all_player_embeds.extend(
                    players
                )

                all_audio_candidates.extend(
                    audio
                )


                canonical = (
                    scoring.get(
                        "canonical"
                    )
                    or ""
                )


                if (
                    "npr.org"
                    in canonical.lower()
                ):

                    all_npr_story_urls.append(
                        canonical
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


    qualified_pages.sort(
        key=lambda item:
            item.get(
                "score",
                0
            ),
        reverse=True
    )


    all_npr_story_urls = unique(
        all_npr_story_urls
    )

    all_player_embeds = unique(
        all_player_embeds
    )

    all_audio_candidates = unique(
        all_audio_candidates
    )


    validated_audio = []


    for audio_url in (
        all_audio_candidates[:30]
    ):

        check = (
            validate_npr_indicator_audio(
                audio_url
            )
        )


        if check.get(
            "valid_npr_indicator_audio"
        ):

            validated_audio.append(
                check
            )


    #
    # Deduplicate final NPR audio.
    #

    deduped_audio = []

    seen_audio = set()


    for audio in validated_audio:

        final_url = audio.get(
            "final_url"
        )


        if (
            final_url
            and final_url
            not in seen_audio
        ):

            seen_audio.add(
                final_url
            )

            deduped_audio.append(
                audio
            )


    #
    # Classification.
    #

    if deduped_audio:

        status = (
            "affiliate_recovered_npr_audio"
        )


    elif all_player_embeds:

        status = (
            "affiliate_player_found"
        )


    elif all_npr_story_urls:

        status = (
            "affiliate_found_npr_identity"
        )


    elif qualified_pages:

        status = (
            "verified_affiliate_page_only"
        )


    else:

        status = (
            "no_verified_affiliate_match"
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

        "search_reports":
            search_reports,

        "candidate_page_count":
            len(
                candidate_urls
            ),

        "qualified_page_count":
            len(
                qualified_pages
            ),

        "qualified_pages":
            qualified_pages,

        "npr_story_urls":
            all_npr_story_urls,

        "npr_player_embeds":
            all_player_embeds,

        "validated_npr_audio":
            deduped_audio,

        "page_reports":
            page_reports,
    }


    print(
        "  ->",
        status,
        "| verified pages:",
        len(
            qualified_pages
        ),
        "| NPR URLs:",
        len(
            all_npr_story_urls
        ),
        "| players:",
        len(
            all_player_embeds
        ),
        "| audio:",
        len(
            deduped_audio
        )
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
        "strict-affiliate-first-recovery-of-unresolved-indicator-episodes",

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
    "AFFILIATE RECOVERY COMPLETE"
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
