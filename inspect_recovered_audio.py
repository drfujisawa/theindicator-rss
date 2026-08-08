#!/usr/bin/env python3

import html
import json
import re
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


RECOVERED_FILE = "indicator_recovered_episodes.json"
OUTPUT_FILE = "indicator_audio_inspection.json"

TEST_LIMIT = 10
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorAudioInspector/1.0)"
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


def unique(values):
    results = []

    for value in values:
        value = clean(value)

        if value and value not in results:
            results.append(value)

    return results


def inspect_page(page):
    findings = {
        "mp3_urls": [],
        "audio_urls": [],
        "iframe_urls": [],
        "npr_urls": [],
        "jsonld_audio_objects": [],
        "possible_ids": [],
        "interesting_lines": [],
    }

    # Direct MP3-like URLs
    findings["mp3_urls"] = unique(
        re.findall(
            r'https?://[^"\'>\s\\]+\.mp3(?:\?[^"\'>\s\\]*)?',
            page,
            re.I
        )
    )[:50]

    # Anything that looks audio-related
    findings["audio_urls"] = unique(
        re.findall(
            r'https?://[^"\'>\s\\]+(?:audio|stream|triton|ondemand|player)[^"\'>\s\\]*',
            page,
            re.I
        )
    )[:100]

    # Iframes
    findings["iframe_urls"] = unique(
        re.findall(
            r'<iframe[^>]+src=["\']([^"\']+)["\']',
            page,
            re.I
        )
    )[:50]

    # NPR links in page source
    findings["npr_urls"] = unique(
        re.findall(
            r'https?://(?:www\.)?npr\.org/[^"\'>\s\\]+',
            page,
            re.I
        )
    )[:100]

    # JSON-LD blocks mentioning audio
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S
    ):
        text = clean(block)

        if (
            "AudioObject" in text
            or '"audio"' in text
            or '"contentUrl"' in text
            or '"embedUrl"' in text
        ):
            findings["jsonld_audio_objects"].append(
                text[:5000]
            )

    # Possible NPR-ish IDs
    id_patterns = [
        r'"storyId"\s*:\s*"?(\d{6,})"?',
        r'"story_id"\s*:\s*"?(\d{6,})"?',
        r'"audioId"\s*:\s*"?(\d{6,})"?',
        r'"audio_id"\s*:\s*"?(\d{6,})"?',
        r'"contentId"\s*:\s*"?(\d{6,})"?',
        r'"content_id"\s*:\s*"?(\d{6,})"?',
        r'/player/embed/(\d{6,})/(\d{6,})',
    ]

    ids = []

    for pattern in id_patterns:
        for match in re.findall(
            pattern,
            page,
            re.I
        ):
            if isinstance(match, tuple):
                for item in match:
                    ids.append(str(item))
            else:
                ids.append(str(match))

    findings["possible_ids"] = unique(ids)[:50]

    # Save snippets around interesting keywords
    keywords = [
        "AudioObject",
        "contentUrl",
        "embedUrl",
        "ondemand.npr.org",
        "media.npr.org",
        "player/embed",
        "audioId",
        "storyId",
        "triton",
        "StreamTheWorld",
    ]

    lines = page.splitlines()

    interesting = []

    for line in lines:
        lower = line.lower()

        if any(
            keyword.lower() in lower
            for keyword in keywords
        ):
            snippet = clean(line.strip())

            if len(snippet) > 1500:
                snippet = snippet[:1500]

            if snippet and snippet not in interesting:
                interesting.append(snippet)

    findings["interesting_lines"] = interesting[:100]

    return findings


with open(
    RECOVERED_FILE,
    "r",
    encoding="utf-8"
) as file:
    data = json.load(file)


episodes = data.get("recovered", [])[:TEST_LIMIT]

results = []


for number, episode in enumerate(
    episodes,
    start=1
):
    source_url = episode.get("source_url")

    print()
    print(
        f"[{number}/{len(episodes)}] "
        f"{episode.get('reference_date')} — "
        f"{episode.get('reference_title')}"
    )

    result = {
        "reference_date":
            episode.get("reference_date"),

        "reference_title":
            episode.get("reference_title"),

        "source_url":
            source_url,

        "source_domain":
            episode.get("source_domain"),

        "status":
            "not_checked",
    }

    if not source_url:
        result["status"] = "no_source_url"
        results.append(result)
        continue

    try:
        page = fetch(source_url)

        findings = inspect_page(page)

        result["status"] = "inspected"
        result["findings"] = findings

        print(
            "  MP3 URLs:",
            len(findings["mp3_urls"])
        )

        print(
            "  Audio-like URLs:",
            len(findings["audio_urls"])
        )

        print(
            "  NPR URLs:",
            len(findings["npr_urls"])
        )

        print(
            "  Possible IDs:",
            len(findings["possible_ids"])
        )

    except HTTPError as exc:
        result["status"] = f"http_{exc.code}"

    except (URLError, TimeoutError) as exc:
        result["status"] = "request_error"
        result["error"] = str(exc)

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    results.append(result)

    time.sleep(0.5)


report = {
    "method": "affiliate-audio-forensics",
    "test_limit": TEST_LIMIT,
    "inspected_count": sum(
        1
        for item in results
        if item.get("status") == "inspected"
    ),
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
print("==============================")
print("Affiliate audio inspection done")
print("Inspected:", report["inspected_count"])
print("Saved:", OUTPUT_FILE)
print("==============================")
