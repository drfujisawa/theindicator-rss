#!/usr/bin/env python3

import html
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


AUDIT_FILE = "indicator_early_audit.json"
OUTPUT_FILE = "indicator_recovered_episodes.json"

TIMEOUT = 20
REQUEST_DELAY = 0.15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorArchiveRecovery/4.0)"
    )
}

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
    value = html.unescape(title).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def fetch(url):
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=TIMEOUT) as response:
        return (
            response.geturl(),
            response.headers.get("Content-Type", ""),
            response.read().decode("utf-8", errors="replace"),
        )


def clean(value):
    if not value:
        return None

    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize(value):
    if not value:
        return ""

    value = clean(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


def title_score(reference, candidate):
    ref_words = set(normalize(reference).split())
    candidate_words = set(normalize(candidate).split())

    if not ref_words or not candidate_words:
        return 0.0

    return (
        len(ref_words & candidate_words)
        / len(ref_words | candidate_words)
    )


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
        match = re.search(pattern, page, re.I | re.S)
        if match:
            title = clean(match.group(1))
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
        match = re.search(pattern, page, re.I | re.S)
        if match:
            description = clean(match.group(1))
            break

    return title, description


def extract_media(page):
    audio_urls = []
    possible_ids = []

    patterns = [
        r'https?://[^"\'>\s\\]+\.mp3(?:\?[^"\'>\s\\]*)?',
        r'https?://ondemand\.npr\.org/[^"\'>\s\\]+',
        r'https?://play\.podtrac\.com/[^"\'>\s\\]+',
        r'https?://media\.npr\.org/[^"\'>\s\\]+',
        r'https?://[^"\'>\s\\]*triton[^"\'>\s\\]+',
    ]

    for pattern in patterns:
        for match in re.findall(pattern, page, re.I):
            value = html.unescape(match)
            value = value.replace("\\/", "/")
            value = value.replace("\\u0026", "&")

            if value not in audio_urls:
                audio_urls.append(value)

    id_patterns = [
        r'"storyId"\s*:\s*"?(\d{6,})"?',
        r'"story_id"\s*:\s*"?(\d{6,})"?',
        r'"audioId"\s*:\s*"?(\d{6,})"?',
        r'"audio_id"\s*:\s*"?(\d{6,})"?',
        r'"contentId"\s*:\s*"?(\d{6,})"?',
        r'"content_id"\s*:\s*"?(\d{6,})"?',
    ]

    for pattern in id_patterns:
        for value in re.findall(pattern, page, re.I):
            if value not in possible_ids:
                possible_ids.append(value)

    return audio_urls, possible_ids


def make_candidate_urls(date, title):
    slug = slugify(title)
    urls = []

    for domain in AFFILIATES:
        for prefix in SECTION_PREFIXES:
            if prefix:
                url = f"{domain}/{prefix}/{date}/{slug}"
            else:
                url = f"{domain}/{date}/{slug}"

            if url not in urls:
                urls.append(url)

    return urls


def recover_episode(reference):
    title = reference["title"]
    date = reference["date"]

    urls = make_candidate_urls(date, title)

    failure_counts = {
        "http_404": 0,
        "other_http": 0,
        "request_error": 0,
        "page_mismatch": 0,
        "no_audio": 0,
    }

    for url in urls:
        try:
            final_url, content_type, page = fetch(url)

        except HTTPError as exc:
            if exc.code == 404:
                failure_counts["http_404"] += 1
            else:
                failure_counts["other_http"] += 1

            continue

        except (URLError, TimeoutError):
            failure_counts["request_error"] += 1
            continue

        except Exception:
            failure_counts["request_error"] += 1
            continue

        page_title, description = extract_meta(page)
        score = title_score(title, page_title)

        if score < 0.65:
            failure_counts["page_mismatch"] += 1
            continue

        audio_urls, possible_ids = extract_media(page)

        if not audio_urls:
            failure_counts["no_audio"] += 1
            continue

        return {
            "status": "recovered",
            "reference_date": date,
            "reference_title": title,
            "reference_year": reference.get("reference_year"),
            "reference_episode": reference.get("reference_episode"),
            "source_url": final_url,
            "source_domain": urlparse(final_url).netloc,
            "source_title": page_title,
            "title_score": round(score, 3),
            "description": description,
            "audio_url": audio_urls[0],
            "all_audio_urls": audio_urls[:10],
            "possible_ids": possible_ids[:10],
        }

        time.sleep(REQUEST_DELAY)

    return {
        "status": "not_recovered",
        "reference_date": date,
        "reference_title": title,
        "reference_year": reference.get("reference_year"),
        "reference_episode": reference.get("reference_episode"),
        "failure_summary": failure_counts,
    }


with open(AUDIT_FILE, "r", encoding="utf-8") as file:
    audit = json.load(file)

missing = audit.get("possible_missing", [])

recovered = []
failed = []

for number, episode in enumerate(missing, start=1):
    print(
        f"[{number}/{len(missing)}] "
        f"{episode['date']} — {episode['title']}"
    )

    result = recover_episode(episode)

    if result["status"] == "recovered":
        recovered.append(result)

        print(
            "  RECOVERED:",
            result["source_domain"],
            result["audio_url"]
        )
    else:
        failed.append(result)
        print("  Not recovered.")

    time.sleep(0.5)


report = {
    "method": "direct-affiliate-recovery",
    "reference_missing_count": len(missing),
    "recovered_count": len(recovered),
    "failed_count": len(failed),
    "recovered": recovered,
    "failed": failed,
}


with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    json.dump(
        report,
        file,
        indent=2,
        ensure_ascii=False
    )


print()
print("================================")
print("Full early-history recovery complete")
print("Missing reference entries:", len(missing))
print("Recovered:", len(recovered))
print("Failed:", len(failed))
print("Saved:", OUTPUT_FILE)
print("================================")
