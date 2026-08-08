#!/usr/bin/env python3

import html
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


AUDIT_FILE = "indicator_early_audit.json"
OUTPUT_FILE = "indicator_recovery_test.json"

TEST_LIMIT = 50
TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorArchiveRecovery/3.0)"
    )
}


# Public-radio affiliates known to carry NPR material.
#
# Each site may use slightly different section paths,
# so we'll try several URL patterns for every episode.
AFFILIATES = [
    "https://news.wypr.org",
    "https://www.wfae.org",
    "https://www.apr.org",
    "https://www.delmarvapublicmedia.org",
    "https://www.kclu.org",
]


SECTION_PREFIXES = [
    "",
    "business",
    "business-education",
    "business-economy",
    "economy",
    "npr-news",
]


def slugify(title):
    """
    Turn:

    Hurricane Joseph & The Calculator That Time Forgot

    into:

    hurricane-joseph-the-calculator-that-time-forgot
    """

    value = html.unescape(title).lower()

    value = value.replace("&", " and ")

    # Remove labels such as "(REBROADCAST)"
    value = re.sub(
        r"\([^)]*\)",
        "",
        value
    )

    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        value
    )

    return value.strip("-")


def fetch(url):
    request = Request(
        url,
        headers=HEADERS
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

        body = response.read().decode(
            "utf-8",
            errors="replace"
        )

    return final_url, content_type, body


def clean(value):
    if not value:
        return None

    value = html.unescape(value)

    value = re.sub(
        r"<[^>]+>",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def normalize(value):
    if not value:
        return ""

    value = clean(value).lower()

    value = value.replace(
        "&",
        " and "
    )

    value = re.sub(
        r"\([^)]*\)",
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


def title_score(reference, candidate):
    ref_words = set(
        normalize(reference).split()
    )

    candidate_words = set(
        normalize(candidate).split()
    )

    if not ref_words or not candidate_words:
        return 0.0

    intersection = len(
        ref_words & candidate_words
    )

    union = len(
        ref_words | candidate_words
    )

    if union == 0:
        return 0.0

    return intersection / union


def extract_meta(page):
    title = None
    description = None

    title_patterns = [
        (
            r'<meta[^>]+property=["\']og:title["\']'
            r'[^>]+content=["\']([^"\']+)'
        ),
        (
            r'<meta[^>]+content=["\']([^"\']+)["\']'
            r'[^>]+property=["\']og:title["\']'
        ),
        r"<title>(.*?)</title>",
    ]

    for pattern in title_patterns:
        match = re.search(
            pattern,
            page,
            re.I | re.S
        )

        if match:
            title = clean(
                match.group(1)
            )
            break

    description_patterns = [
        (
            r'<meta[^>]+name=["\']description["\']'
            r'[^>]+content=["\']([^"\']*)'
        ),
        (
            r'<meta[^>]+property=["\']og:description["\']'
            r'[^>]+content=["\']([^"\']*)'
        ),
    ]

    for pattern in description_patterns:
        match = re.search(
            pattern,
            page,
            re.I | re.S
        )

        if match:
            description = clean(
                match.group(1)
            )
            break

    return title, description


def extract_dates(page):
    results = []

    patterns = [
        r'datetime=["\']([^"\']+)["\']',
        (
            r'(January|February|March|April|May|June|'
            r'July|August|September|October|November|December)'
            r'\s+\d{1,2},\s+20\d{2}'
        ),
        r'20\d{2}-\d{2}-\d{2}',
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            page,
            re.I
        )

        for match in matches:
            if isinstance(
                match,
                tuple
            ):
                match = " ".join(
                    match
                )

            match = clean(
                str(match)
            )

            if (
                match
                and match not in results
            ):
                results.append(
                    match
                )

    return results[:20]


def extract_media(page):
    """
    Look for several kinds of surviving audio information.

    We aren't assuming which one the affiliate CMS uses.
    """

    audio_urls = []
    player_urls = []
    possible_ids = []

    media_patterns = [
        r'https?://[^"\'>\s\\]+\.mp3(?:\?[^"\'>\s\\]*)?',
        r'https?://ondemand\.npr\.org/[^"\'>\s\\]+',
        r'https?://play\.podtrac\.com/[^"\'>\s\\]+',
        r'https?://media\.npr\.org/[^"\'>\s\\]+',
        r'https?://[^"\'>\s\\]*triton[^"\'>\s\\]+',
        r'https?://[^"\'>\s\\]*audio[^"\'>\s\\]+\.mp3[^"\'>\s\\]*',
    ]

    for pattern in media_patterns:

        for match in re.findall(
            pattern,
            page,
            re.I
        ):
            value = html.unescape(
                match
            )

            value = value.replace(
                "\\/",
                "/"
            )

            value = value.replace(
                "\\u0026",
                "&"
            )

            if (
                value
                not in audio_urls
            ):
                audio_urls.append(
                    value
                )

    player_patterns = [
        r'https?://www\.npr\.org/player/embed/[^"\'>\s]+',
        r'/player/embed/[^"\'>\s]+',
        r'https?://[^"\'>\s]+/player/[^"\'>\s]+',
    ]

    for pattern in player_patterns:

        for match in re.findall(
            pattern,
            page,
            re.I
        ):
            value = html.unescape(
                match
            )

            if (
                value
                not in player_urls
            ):
                player_urls.append(
                    value
                )

    #
    # Look for likely NPR IDs stored in JSON/script data.
    #
    id_patterns = [
        r'"storyId"\s*:\s*"?(\d{6,})"?',
        r'"story_id"\s*:\s*"?(\d{6,})"?',
        r'"audioId"\s*:\s*"?(\d{6,})"?',
        r'"audio_id"\s*:\s*"?(\d{6,})"?',
        r'"contentId"\s*:\s*"?(\d{6,})"?',
        r'"content_id"\s*:\s*"?(\d{6,})"?',
    ]

    for pattern in id_patterns:

        for value in re.findall(
            pattern,
            page,
            re.I
        ):
            if (
                value
                not in possible_ids
            ):
                possible_ids.append(
                    value
                )

    return {
        "audio_urls":
            audio_urls[:20],

        "player_urls":
            player_urls[:20],

        "possible_ids":
            possible_ids[:20],
    }


def make_candidate_urls(
    date,
    title
):
    slug = slugify(title)

    candidates = []

    for domain in AFFILIATES:

        for prefix in SECTION_PREFIXES:

            if prefix:

                url = (
                    f"{domain}/"
                    f"{prefix}/"
                    f"{date}/"
                    f"{slug}"
                )

            else:

                url = (
                    f"{domain}/"
                    f"{date}/"
                    f"{slug}"
                )

            if (
                url
                not in candidates
            ):
                candidates.append(
                    url
                )

    return candidates


def probe_episode(
    reference_date,
    reference_title
):
    attempts = []
    matches = []

    candidate_urls = (
        make_candidate_urls(
            reference_date,
            reference_title
        )
    )

    for url in candidate_urls:

        attempt = {
            "requested_url":
                url,
        }

        try:

            (
                final_url,
                content_type,
                page,
            ) = fetch(url)

        except HTTPError as exc:

            attempt[
                "status"
            ] = (
                f"http_{exc.code}"
            )

            attempts.append(
                attempt
            )

            continue

        except (
            URLError,
            TimeoutError,
        ) as exc:

            attempt[
                "status"
            ] = "request_error"

            attempt[
                "error"
            ] = str(exc)

            attempts.append(
                attempt
            )

            continue

        except Exception as exc:

            attempt[
                "status"
            ] = "error"

            attempt[
                "error"
            ] = str(exc)

            attempts.append(
                attempt
            )

            continue

        title, description = (
            extract_meta(page)
        )

        score = title_score(
            reference_title,
            title
        )

        media = extract_media(
            page
        )

        attempt.update({
            "status":
                "page_returned",

            "final_url":
                final_url,

            "domain":
                urlparse(
                    final_url
                ).netloc,

            "content_type":
                content_type,

            "page_title":
                title,

            "title_score":
                round(
                    score,
                    3
                ),

            "description":
                description,

            "dates_found":
                extract_dates(
                    page
                ),

            "audio_urls":
                media[
                    "audio_urls"
                ],

            "player_urls":
                media[
                    "player_urls"
                ],

            "possible_ids":
                media[
                    "possible_ids"
                ],
        })

        attempts.append(
            attempt
        )

        #
        # A score this high strongly suggests
        # this is the correct story page.
        #
        if score >= 0.65:

            matches.append(
                attempt
            )

            print(
                "  FOUND:",
                title
            )

            print(
                "  URL:",
                final_url
            )

            print(
                "  Audio URLs:",
                len(
                    media[
                        "audio_urls"
                    ]
                )
            )

            print(
                "  Player URLs:",
                len(
                    media[
                        "player_urls"
                    ]
                )
            )

            #
            # One confirmed affiliate copy
            # is sufficient for this test.
            #
            break

        time.sleep(0.15)

    matches.sort(
        key=lambda item: (
            item.get(
                "title_score",
                0
            ),
            len(
                item.get(
                    "audio_urls",
                    []
                )
            ),
        ),
        reverse=True,
    )

    return (
        matches,
        attempts
    )


with open(
    AUDIT_FILE,
    "r",
    encoding="utf-8"
) as file:

    audit = json.load(
        file
    )


missing = audit.get(
    "possible_missing",
    []
)[:TEST_LIMIT]


report = {
    "proof_of_concept": True,

    "method":
        "direct-affiliate-probe",

    "attempted_count":
        len(missing),

    "affiliate_page_found_count":
        0,

    "audio_found_count":
        0,

    "player_found_count":
        0,

    "results":
        [],
}


for number, episode in enumerate(
    missing,
    start=1
):

    reference_date = (
        episode["date"]
    )

    reference_title = (
        episode["title"]
    )

    print()
    print(
        f"[{number}/"
        f"{len(missing)}] "
        f"{reference_date} — "
        f"{reference_title}"
    )

    matches, attempts = (
        probe_episode(
            reference_date,
            reference_title
        )
    )

    result = {
        "reference_date":
            reference_date,

        "reference_title":
            reference_title,

        "reference_year":
            episode.get(
                "reference_year"
            ),

        "reference_episode":
            episode.get(
                "reference_episode"
            ),

        "matches":
            matches,

        "attempts":
            attempts,
    }

    if matches:

        report[
            "affiliate_page_found_count"
        ] += 1

        best = matches[0]

        if best.get(
            "audio_urls"
        ):
            report[
                "audio_found_count"
            ] += 1

        if best.get(
            "player_urls"
        ):
            report[
                "player_found_count"
            ] += 1

    report[
        "results"
    ].append(
        result
    )

    time.sleep(0.5)


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
    "Direct affiliate probe complete"
)

print(
    "Attempted:",
    report[
        "attempted_count"
    ]
)

print(
    "Affiliate pages found:",
    report[
        "affiliate_page_found_count"
    ]
)

print(
    "Pages exposing audio:",
    report[
        "audio_found_count"
    ]
)

print(
    "Pages exposing players:",
    report[
        "player_found_count"
    ]
)

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
