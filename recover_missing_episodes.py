#!/usr/bin/env python3

import html
import json
import re
import time
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


AUDIT_FILE = "indicator_early_audit.json"
OUTPUT_FILE = "indicator_recovery_test.json"

TEST_LIMIT = 10
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; "
        "IndicatorArchiveRecovery/2.0)"
    )
}


def fetch(url):
    request = Request(
        url,
        headers=HEADERS
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:
        return response.read().decode(
            "utf-8",
            errors="replace"
        )


def clean(value):
    if not value:
        return ""

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
    value = clean(value).lower()
    value = value.replace("&", " and ")

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return " ".join(
        value.split()
    )


def title_score(a, b):
    a_words = set(
        normalize(a).split()
    )

    b_words = set(
        normalize(b).split()
    )

    if not a_words or not b_words:
        return 0.0

    return (
        len(a_words & b_words)
        / len(a_words | b_words)
    )


def search_web(title, date):
    """
    Use DuckDuckGo's lightweight HTML search.

    We are intentionally searching the wider public-radio
    ecosystem rather than NPR's broken historical search.
    """

    query = (
        f'"{title}" '
        f'"{date[:4]}" '
        f'NPR'
    )

    url = (
        "https://html.duckduckgo.com/html/"
        "?q=" + quote(query)
    )

    page = fetch(url)

    links = []

    patterns = [
        r'class="result__a"[^>]+href="([^"]+)"',
        r"class='result__a'[^>]+href='([^']+)'",
    ]

    for pattern in patterns:
        for match in re.findall(
            pattern,
            page,
            re.I
        ):
            match = html.unescape(match)

            # DuckDuckGo sometimes wraps URLs.
            redirect = re.search(
                r"uddg=([^&]+)",
                match
            )

            if redirect:
                from urllib.parse import unquote
                match = unquote(
                    redirect.group(1)
                )

            if (
                match.startswith("http")
                and match not in links
            ):
                links.append(match)

    return links[:15]


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


def extract_audio_urls(page):
    """
    Search raw page source for likely MP3/audio URLs.

    Affiliate CMS pages often keep these URLs in JSON,
    script data or audio-player attributes even when
    they aren't visible in the article text.
    """

    urls = []

    patterns = [
        r'https?://[^"\'>\s]+\.mp3(?:\?[^"\'>\s]*)?',
        r'https?://ondemand\.npr\.org/[^"\'>\s]+',
        r'https?://play\.podtrac\.com/[^"\'>\s]+',
        r'https?://[^"\'>\s]*npr[^"\'>\s]*\.mp3[^"\'>\s]*',
    ]

    for pattern in patterns:
        for match in re.findall(
            pattern,
            page,
            re.I
        ):
            match = html.unescape(
                match
            )

            match = match.replace(
                "\\u0026",
                "&"
            )

            match = match.replace(
                "\\/",
                "/"
            )

            if match not in urls:
                urls.append(match)

    return urls


def candidate_page(url, reference_title):
    try:
        page = fetch(url)

    except Exception as exc:
        return {
            "url": url,
            "error": str(exc)
        }

    page_title, description = (
        extract_meta(page)
    )

    if not page_title:
        return {
            "url": url,
            "error": "No page title"
        }

    score = title_score(
        reference_title,
        page_title
    )

    audio_urls = extract_audio_urls(
        page
    )

    return {
        "url": url,
        "domain": urlparse(url).netloc,
        "page_title": page_title,
        "description": description,
        "title_score": round(
            score,
            3
        ),
        "audio_urls": audio_urls,
    }


with open(
    AUDIT_FILE,
    "r",
    encoding="utf-8"
) as f:
    audit = json.load(f)


missing = audit.get(
    "possible_missing",
    []
)[:TEST_LIMIT]


report = {
    "proof_of_concept": True,
    "method": "affiliate-search",
    "attempted_count": len(missing),
    "candidate_page_count": 0,
    "audio_found_count": 0,
    "results": []
}


for number, episode in enumerate(
    missing,
    start=1
):
    title = episode["title"]
    date = episode["date"]

    print()
    print(
        f"[{number}/{len(missing)}] "
        f"{date} — {title}"
    )

    result = {
        "reference_date": date,
        "reference_title": title,
        "reference_year":
            episode.get(
                "reference_year"
            ),
        "reference_episode":
            episode.get(
                "reference_episode"
            ),
        "search_results": [],
    }

    try:
        links = search_web(
            title,
            date
        )

        print(
            f"Found {len(links)} "
            "web result(s)."
        )

    except Exception as exc:
        result["search_error"] = (
            str(exc)
        )

        report["results"].append(
            result
        )

        continue

    candidates = []

    for url in links:
        candidate = candidate_page(
            url,
            title
        )

        candidates.append(
            candidate
        )

        if (
            candidate.get(
                "title_score",
                0
            )
            >= 0.6
        ):
            report[
                "candidate_page_count"
            ] += 1

        if candidate.get(
            "audio_urls"
        ):
            report[
                "audio_found_count"
            ] += 1

        time.sleep(0.5)

    candidates.sort(
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

    result["search_results"] = (
        candidates
    )

    if candidates:
        result["best_candidate"] = (
            candidates[0]
        )

        print(
            "Best:",
            candidates[0].get(
                "page_title"
            ),
            candidates[0].get(
                "title_score"
            )
        )

        print(
            "Audio URLs:",
            len(
                candidates[0].get(
                    "audio_urls",
                    []
                )
            )
        )

    report["results"].append(
        result
    )

    time.sleep(1)


with open(
    OUTPUT_FILE,
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        report,
        f,
        indent=2,
        ensure_ascii=False
    )


print()
print(
    "================================"
)
print(
    "Affiliate recovery test complete"
)
print(
    "Attempted:",
    report["attempted_count"]
)
print(
    "Matching candidate pages:",
    report["candidate_page_count"]
)
print(
    "Candidates containing audio:",
    report["audio_found_count"]
)
print(
    "Saved:",
    OUTPUT_FILE
)
print(
    "================================"
)
