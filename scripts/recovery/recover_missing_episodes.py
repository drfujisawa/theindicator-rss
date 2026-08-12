#!/usr/bin/env python3
from pathlib import Path

import html
import json
import re
import time
from urllib.request import Request, urlopen
from urllib.parse import urljoin
REPO_ROOT = Path(__file__).resolve().parents[2]


AUDIT_FILE = str(REPO_ROOT / "indicator_early_audit.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_recovery_test.json")
TEST_LIMIT = 10
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorArchiveRecovery/5.0)"
    )
}

# Cardiff's WNYC archive has paginated historical Indicator entries.
WNYC_PAGES = [
    f"https://www.wnyc.org/people/cardiff-garcia/{page}/"
    for page in range(1, 25)
]


def fetch(url):
    req = Request(url, headers=HEADERS)

    with urlopen(req, timeout=TIMEOUT) as response:
        return response.read().decode(
            "utf-8",
            errors="replace"
        )


def clean(value):
    if not value:
        return ""

    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize(value):
    value = clean(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


def title_score(a, b):
    a_words = set(normalize(a).split())
    b_words = set(normalize(b).split())

    if not a_words or not b_words:
        return 0.0

    return (
        len(a_words & b_words)
        / len(a_words | b_words)
    )


def build_wnyc_index():
    """
    Crawl Cardiff Garcia's WNYC archive and locate
    NPR links associated with historical Indicator entries.
    """

    records = []

    for page_url in WNYC_PAGES:
        print("Reading WNYC:", page_url)

        try:
            page = fetch(page_url)
        except Exception as exc:
            print("  Failed:", exc)
            continue

        # Capture blocks around NPR links.
        npr_links = re.findall(
            r'href=["\']'
            r'(https?://www\.npr\.org/[^"\']+)'
            r'["\']',
            page,
            re.I
        )

        for npr_url in npr_links:
            npr_url = html.unescape(npr_url)

            # Locate surrounding HTML so we can recover
            # the WNYC title/date associated with this link.
            position = page.find(npr_url)

            start = max(0, position - 2500)
            end = min(len(page), position + 1000)

            block = page[start:end]

            headings = re.findall(
                r"<h[1-4][^>]*>(.*?)</h[1-4]>",
                block,
                re.I | re.S
            )

            title = None

            if headings:
                # The last heading before the NPR link is
                # usually the story title.
                title = clean(headings[-1])

            date_match = re.search(
                r"(January|February|March|April|May|June|"
                r"July|August|September|October|November|December)"
                r"\s+\d{1,2},\s+201[89]",
                clean(block),
                re.I
            )

            date = (
                date_match.group(0)
                if date_match
                else None
            )

            if title:
                records.append({
                    "title": title,
                    "date": date,
                    "npr_url": npr_url,
                })

        time.sleep(0.3)

    return records


def extract_player(npr_page):
    patterns = [
        (
            r"/player/embed/"
            r"([^/\"'<>\s]+)/"
            r"([^/?\"'<>\s]+)"
        ),
        (
            r"www\.npr\.org/player/embed/"
            r"([^/\"'<>\s]+)/"
            r"([^/?\"'<>\s]+)"
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            npr_page
        )

        if match:
            return {
                "story_id": match.group(1),
                "audio_id": match.group(2),
            }

    return None


with open(
    AUDIT_FILE,
    "r",
    encoding="utf-8"
) as file:
    audit = json.load(file)


missing = audit.get(
    "possible_missing",
    []
)[:TEST_LIMIT]


print("Building WNYC historical index...")

wnyc_records = build_wnyc_index()

print(
    f"WNYC records discovered: "
    f"{len(wnyc_records)}"
)


results = []
found = 0
player_found = 0


for reference in missing:

    ref_title = reference["title"]

    candidates = []

    for record in wnyc_records:
        score = title_score(
            ref_title,
            record["title"]
        )

        if score >= 0.65:
            candidates.append(
                (
                    score,
                    record
                )
            )

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    result = {
        "reference_date":
            reference["date"],

        "reference_title":
            ref_title,

        "status":
            "not_found",
    }

    if candidates:

        score, best = candidates[0]

        result.update({
            "status":
                "npr_url_found",

            "wnyc_title":
                best["title"],

            "wnyc_date":
                best["date"],

            "title_score":
                round(score, 3),

            "npr_url":
                best["npr_url"],
        })

        found += 1

        print()
        print(
            "FOUND NPR URL:",
            ref_title
        )

        print(
            best["npr_url"]
        )

        try:

            npr_page = fetch(
                best["npr_url"]
            )

            player = extract_player(
                npr_page
            )

            if player:

                result.update(
                    player
                )

                result[
                    "status"
                ] = "player_found"

                player_found += 1

                print(
                    "Player:",
                    player
                )

        except Exception as exc:

            result[
                "npr_fetch_error"
            ] = str(exc)

    results.append(
        result
    )


report = {
    "method":
        "wnyc-to-original-npr",

    "attempted_count":
        len(missing),

    "wnyc_index_count":
        len(wnyc_records),

    "npr_url_found_count":
        found,

    "player_found_count":
        player_found,

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
print("============================")
print("WNYC → NPR recovery test")
print("Attempted:", len(missing))
print("NPR URLs found:", found)
print("NPR players found:", player_found)
print("Saved:", OUTPUT_FILE)
print("============================")
