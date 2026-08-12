#!/usr/bin/env python3
from pathlib import Path

import html
import json
import re
import time
from difflib import SequenceMatcher
from urllib.parse import urlparse, parse_qs, urlunparse
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]


INPUT_FILE = str(REPO_ROOT / "indicator_recovered_episodes.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_identities.json")
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRIdentityRecovery/1.0)"
    )
}


def fetch(url):
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode(
            "utf-8",
            errors="replace"
        )


def clean(value):
    if not value:
        return ""

    value = html.unescape(value)
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")

    return value


def normalize_title(value):
    value = clean(value).lower()

    value = value.replace("’", "'")
    value = value.replace("‘", "'")
    value = value.replace("“", '"')
    value = value.replace("”", '"')

    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def title_score(a, b):
    a = normalize_title(a)
    b = normalize_title(b)

    if not a or not b:
        return 0.0

    return round(
        SequenceMatcher(None, a, b).ratio(),
        4
    )


def extract_npr_urls(page):
    page = clean(page)

    candidates = re.findall(
        r'https?://(?:www\.)?npr\.org/[^"\'<>\s\\]+',
        page,
        re.I
    )

    output = []

    for url in candidates:
        url = clean(url)

        # Strip common HTML/JSON junk from the end.
        url = url.rstrip(
            '.,;:)]}>"\''
        )

        # Normalize www.
        url = re.sub(
            r"^https?://npr\.org/",
            "https://www.npr.org/",
            url,
            flags=re.I
        )

        if url.startswith("http://www.npr.org/"):
            url = "https://" + url[len("http://"):]

        if url not in output:
            output.append(url)

    return output


def story_id_from_url(url):
    match = re.search(
        r'npr\.org/(?:[^/?#]+/)*(\d{6,})(?:/|$|\?|#)',
        url,
        re.I
    )

    if match:
        return match.group(1)

    return None


def date_from_npr_url(url):
    match = re.search(
        r'npr\.org/(?:[^/?#]+/)*'
        r'((?:19|20)\d{2})/'
        r'(\d{2})/'
        r'(\d{2})/',
        url,
        re.I
    )

    if not match:
        return None

    return (
        f"{match.group(1)}-"
        f"{match.group(2)}-"
        f"{match.group(3)}"
    )


def slug_from_url(url):
    path = urlparse(url).path.rstrip("/")

    if not path:
        return ""

    last = path.split("/")[-1]

    # If the URL ends at the numeric story ID,
    # there isn't a useful slug.
    if last.isdigit():
        return ""

    return last.replace("-", " ")


def canonicalize_npr_url(url):
    parsed = urlparse(url)

    # Tracking parameters such as ?ft=nprml&f=593261790
    # aren't part of the canonical identity.
    return urlunparse(
        (
            "https",
            "www.npr.org",
            parsed.path.rstrip("/"),
            "",
            "",
            ""
        )
    )


def score_candidate(reference_title, reference_date, url):
    story_id = story_id_from_url(url)
    url_date = date_from_npr_url(url)
    slug = slug_from_url(url)

    slug_score = title_score(
        reference_title,
        slug
    )

    date_match = (
        bool(reference_date)
        and bool(url_date)
        and reference_date == url_date
    )

    score = slug_score

    if date_match:
        score += 1.0

    if story_id:
        score += 0.25

    return {
        "url": canonicalize_npr_url(url),
        "story_id": story_id,
        "url_date": url_date,
        "slug": slug,
        "title_score": slug_score,
        "date_match": date_match,
        "identity_score": round(score, 4),
    }


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    recovery = json.load(file)


recovered_input = recovery.get("recovered", [])

results = []

summary = {
    "input_count": len(recovered_input),
    "pages_fetched": 0,
    "pages_failed": 0,
    "pages_with_npr_urls": 0,
    "episodes_with_story_id": 0,
    "strong_identity_count": 0,
    "needs_review_count": 0,
    "no_npr_identity_count": 0,
}


for index, episode in enumerate(
    recovered_input,
    start=1
):
    reference_title = episode.get(
        "reference_title",
        ""
    )

    reference_date = episode.get(
        "reference_date",
        ""
    )

    source_url = episode.get(
        "source_url",
        ""
    )

    print(
        f"[{index}/{len(recovered_input)}] "
        f"{reference_date} - {reference_title}"
    )

    result = {
        "reference_date": reference_date,
        "reference_title": reference_title,
        "reference_year": episode.get(
            "reference_year"
        ),
        "reference_episode": episode.get(
            "reference_episode"
        ),
        "affiliate_url": source_url,
        "affiliate_domain": episode.get(
            "source_domain"
        ),
        "status": None,
        "selected_npr_url": None,
        "npr_story_id": None,
        "candidates": [],
    }

    try:
        page = fetch(source_url)
        summary["pages_fetched"] += 1

    except Exception as exc:
        summary["pages_failed"] += 1

        result["status"] = "affiliate_fetch_failed"
        result["error"] = str(exc)

        results.append(result)
        continue

    urls = extract_npr_urls(page)

    candidates_by_url = {}

    for url in urls:
        candidate = score_candidate(
            reference_title,
            reference_date,
            url
        )

        canonical = candidate["url"]

        # Same NPR URL may appear many times in HTML.
        existing = candidates_by_url.get(canonical)

        if (
            existing is None
            or candidate["identity_score"]
            > existing["identity_score"]
        ):
            candidates_by_url[canonical] = candidate

    candidates = list(
        candidates_by_url.values()
    )

    candidates.sort(
        key=lambda item: item["identity_score"],
        reverse=True
    )

    result["candidates"] = candidates[:20]

    if candidates:
        summary["pages_with_npr_urls"] += 1

    best = candidates[0] if candidates else None

    if best:
        result["selected_npr_url"] = best["url"]
        result["npr_story_id"] = best["story_id"]
        result["selected_url_date"] = best["url_date"]
        result["selected_slug"] = best["slug"]
        result["selected_title_score"] = best["title_score"]
        result["selected_date_match"] = best["date_match"]
        result["identity_score"] = best["identity_score"]

    if best and best["story_id"]:
        summary["episodes_with_story_id"] += 1

    # Strong means:
    #   * NPR numeric story ID exists
    #   * URL contains the exact reference date
    #   * slug/title similarity is high
    #
    # This should classify our Bonds test as strong.
    if (
        best
        and best["story_id"]
        and best["date_match"]
        and best["title_score"] >= 0.80
    ):
        result["status"] = "strong_npr_identity"
        summary["strong_identity_count"] += 1

    elif best and best["story_id"]:
        result["status"] = "needs_review"
        summary["needs_review_count"] += 1

    else:
        result["status"] = "no_npr_identity"
        summary["no_npr_identity_count"] += 1

    results.append(result)

    # Be polite to affiliate servers.
    time.sleep(0.15)


report = {
    "method": "affiliate-to-original-npr-identity-recovery",
    "validation_policy": {
        "strong_identity_requires": [
            "numeric NPR story ID",
            "NPR URL date exactly matches reference date",
            "NPR URL slug title similarity >= 0.80"
        ],
        "note": (
            "No recovered identities are written to "
            "indicator_history.json by this script."
        )
    },
    "summary": summary,
    "results": results,
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
print("======================================")
print("NPR identity recovery complete")
print("Input:", summary["input_count"])
print("Fetched:", summary["pages_fetched"])
print("Failed:", summary["pages_failed"])

print(
    "Pages with NPR URLs:",
    summary["pages_with_npr_urls"]
)

print(
    "Episodes with NPR story ID:",
    summary["episodes_with_story_id"]
)

print(
    "Strong identities:",
    summary["strong_identity_count"]
)

print(
    "Needs review:",
    summary["needs_review_count"]
)

print(
    "No NPR identity:",
    summary["no_npr_identity_count"]
)

print("Saved:", OUTPUT_FILE)
print("======================================")
