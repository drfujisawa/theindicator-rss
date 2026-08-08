#!/usr/bin/env python3

import html
import json
import re
from urllib.request import Request, urlopen

RECOVERED_FILE = "indicator_recovered_episodes.json"
OUTPUT_FILE = "indicator_npr_identity_probe.json"

TARGET_TITLE = "Bonds... Japanese Bonds"
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRIdentityProbe/1.0)"
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
    output = []

    for value in values:
        value = clean(str(value)).strip()

        if value and value not in output:
            output.append(value)

    return output


def snippets(page, term, radius=500):
    results = []
    lower = page.lower()
    term_lower = term.lower()

    start = 0

    while True:
        position = lower.find(term_lower, start)

        if position == -1:
            break

        left = max(0, position - radius)
        right = min(len(page), position + len(term) + radius)

        text = clean(page[left:right])
        text = re.sub(r"\s+", " ", text)

        if text not in results:
            results.append(text)

        start = position + len(term)

    return results[:25]


with open(
    RECOVERED_FILE,
    "r",
    encoding="utf-8"
) as file:
    recovery = json.load(file)


target = None

for episode in recovery.get("recovered", []):
    if episode.get("reference_title") == TARGET_TITLE:
        target = episode
        break


if target is None:
    raise RuntimeError(
        f"Could not find recovered episode: {TARGET_TITLE}"
    )


source_url = target["source_url"]

print("Target:", target["reference_title"])
print("Date:", target["reference_date"])
print("Affiliate:", source_url)

page = fetch(source_url)


# Every URL containing NPR anywhere in it.
npr_urls = unique(
    re.findall(
        r'https?://[^"\'<>\s\\]*npr[^"\'<>\s\\]*',
        page,
        re.I
    )
)


# Specifically look for www.npr.org links.
npr_story_links = unique(
    re.findall(
        r'https?://(?:www\.)?npr\.org/[^"\'<>\s\\]+',
        page,
        re.I
    )
)


# Canonical / alternate / related links.
link_tags = unique(
    re.findall(
        r'<link[^>]+(?:canonical|alternate|related)[^>]*>',
        page,
        re.I
    )
)


# Meta tags that might identify the original content.
meta_tags = unique(
    re.findall(
        r'<meta[^>]+>',
        page,
        re.I
    )
)


interesting_meta = []

for tag in meta_tags:
    lower = tag.lower()

    if any(
        word in lower
        for word in [
            "npr",
            "source",
            "original",
            "story",
            "article",
            "content",
            "audio",
            "guid",
            "id",
        ]
    ):
        interesting_meta.append(tag)


# Numeric IDs attached to likely story/content/audio fields.
id_patterns = {
    "story_id": [
        r'"storyId"\s*:\s*"?(\d{5,})"?',
        r'"story_id"\s*:\s*"?(\d{5,})"?',
        r'storyId[=:]["\']?(\d{5,})',
    ],

    "audio_id": [
        r'"audioId"\s*:\s*"?(\d{5,})"?',
        r'"audio_id"\s*:\s*"?(\d{5,})"?',
        r'audioId[=:]["\']?(\d{5,})',
    ],

    "content_id": [
        r'"contentId"\s*:\s*"?(\d{5,})"?',
        r'"content_id"\s*:\s*"?(\d{5,})"?',
        r'contentId[=:]["\']?(\d{5,})',
    ],

    "npr_numeric_url_ids": [
        r'npr\.org/(?:[^"\'<>\s/]+/)*(\d{6,})(?:/|["\'?<>\s])',
    ],
}


ids = {}

for category, patterns in id_patterns.items():
    found = []

    for pattern in patterns:
        found.extend(
            re.findall(
                pattern,
                page,
                re.I
            )
        )

    ids[category] = unique(found)


# Look for JSON-LD. Even if it doesn't contain audio,
# it may contain identifiers, sameAs, publisher or URLs.
jsonld_blocks = re.findall(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
    r'(.*?)'
    r'</script>',
    page,
    re.I | re.S
)

jsonld_results = []

for block in jsonld_blocks:
    cleaned = clean(block)

    if any(
        word in cleaned.lower()
        for word in [
            "npr",
            "identifier",
            "sameas",
            "ispartof",
            "publisher",
            "url",
        ]
    ):
        jsonld_results.append(
            cleaned[:10000]
        )


# Capture context around especially interesting terms.
terms = [
    "npr.org",
    "NPR",
    "storyId",
    "story_id",
    "audioId",
    "audio_id",
    "contentId",
    "content_id",
    "original",
    "canonical",
    "sameAs",
    "identifier",
    "isPartOf",
]

context = {}

for term in terms:
    found = snippets(page, term)

    if found:
        context[term] = found


report = {
    "method": "single-affiliate-npr-identity-probe",

    "target": {
        "reference_date":
            target.get("reference_date"),

        "reference_title":
            target.get("reference_title"),

        "source_url":
            source_url,

        "source_domain":
            target.get("source_domain"),

        "title_score":
            target.get("title_score"),
    },

    "npr_story_links":
        npr_story_links,

    "all_npr_urls":
        npr_urls,

    "possible_ids":
        ids,

    "interesting_link_tags":
        link_tags[:100],

    "interesting_meta_tags":
        interesting_meta[:100],

    "jsonld_blocks":
        jsonld_results[:25],

    "context":
        context,
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
print("NPR identity probe complete")
print("NPR story links:", len(npr_story_links))
print("All NPR URLs:", len(npr_urls))

print(
    "Possible story IDs:",
    len(ids["story_id"])
)

print(
    "Possible audio IDs:",
    len(ids["audio_id"])
)

print(
    "Numeric NPR URL IDs:",
    len(ids["npr_numeric_url_ids"])
)

print("Saved:", OUTPUT_FILE)
print("==============================")
