#!/usr/bin/env python3
from pathlib import Path

import json
import re
import time
from datetime import datetime
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]



OUTPUT_FILE = str(REPO_ROOT / "archive" / "recovery" / "indicator_april2019_wayback_mp3s.json")
TARGET_DATE = "2019-04-22"

WAYBACK_PATTERN = (
    "https://ondemand.npr.org/"
    "anon.npr-mp3/npr/indicator/2019/04/*"
)

KEYWORDS = [
    "traffic",
    "tariff",
    "trade",
    "china",
    "trump",
    "import",
    "imports",
    "duty",
    "duties",
    "tax",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorApril2019Probe/1.0)"
    )
}

TIMEOUT = 45
RETRIES = 4


def fetch(url):
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
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

        except Exception as exc:
            last_error = exc

            if attempt < RETRIES:
                time.sleep(attempt * 3)

    raise last_error


def cdx_url():
    return (
        "https://web.archive.org/cdx/search/cdx"
        "?url="
        + quote(WAYBACK_PATTERN, safe="")
        + "&output=json"
        + "&filter=statuscode:200"
        + "&collapse=urlkey"
        + "&fl=timestamp,original,statuscode,mimetype"
        + "&limit=5000"
    )


def parse_cdx(text):
    data = json.loads(text)

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


def filename_from_url(url):
    parsed = urlparse(url)

    return (
        parsed.path
        .rstrip("/")
        .split("/")[-1]
    )


def extract_date(filename):
    match = re.match(
        r"(\d{8})_indicator_",
        filename
    )

    if not match:
        return None

    try:
        return datetime.strptime(
            match.group(1),
            "%Y%m%d"
        ).date()
    except ValueError:
        return None


target_date = datetime.strptime(
    TARGET_DATE,
    "%Y-%m-%d"
).date()


rows = []

try:
    rows = parse_cdx(
        fetch(cdx_url())
    )

    cdx_status = "fetched"

except Exception as exc:
    cdx_status = "error"
    cdx_error = str(exc)


candidates = []


for row in rows:
    original = row.get("original")

    if not original:
        continue

    filename = filename_from_url(
        original
    )

    file_date = extract_date(
        filename
    )

    day_distance = None

    if file_date:
        day_distance = abs(
            (file_date - target_date).days
        )

    lower = filename.lower()

    keyword_hits = [
        keyword
        for keyword in KEYWORDS
        if keyword in lower
    ]

    score = 0

    if day_distance is not None:
        if day_distance == 0:
            score += 20
        elif day_distance == 1:
            score += 15
        elif day_distance <= 3:
            score += 10
        elif day_distance <= 7:
            score += 5

    score += len(keyword_hits) * 8

    candidates.append({
        "timestamp":
            row.get("timestamp"),

        "original":
            original,

        "filename":
            filename,

        "file_date":
            (
                str(file_date)
                if file_date
                else None
            ),

        "day_distance":
            day_distance,

        "keyword_hits":
            keyword_hits,

        "score":
            score,

        "mimetype":
            row.get("mimetype"),
    })


candidates.sort(
    key=lambda item: (
        item["score"],
        -(
            item["day_distance"]
            if item["day_distance"]
            is not None
            else 999
        )
    ),
    reverse=True
)


nearby = [
    item
    for item in candidates
    if (
        item["day_distance"]
        is not None
        and item["day_distance"] <= 7
    )
]


keyword_matches = [
    item
    for item in candidates
    if item["keyword_hits"]
]


report = {
    "method":
        "wayback-enumerate-april-2019-indicator-mp3s",

    "target_date":
        TARGET_DATE,

    "cdx_status":
        cdx_status,

    "total_archived_urls":
        len(candidates),

    "nearby_count":
        len(nearby),

    "keyword_match_count":
        len(keyword_matches),

    "top_candidates":
        candidates[:50],

    "nearby":
        nearby,

    "keyword_matches":
        keyword_matches,
}


if cdx_status == "error":
    report["cdx_error"] = cdx_error


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
    "April 2019 Indicator MP3 enumeration complete"
)

print(
    "CDX status:",
    cdx_status
)

print(
    "Total archived URLs:",
    len(candidates)
)

print(
    "Nearby:",
    len(nearby)
)

print(
    "Keyword matches:",
    len(keyword_matches)
)

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
