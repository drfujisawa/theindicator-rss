#!/usr/bin/env python3

import html
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


INPUT_FILE = "indicator_npr_identities.json"
OUTPUT_FILE = "indicator_npr_audio_recovery.json"

TIMEOUT = 30
REQUEST_DELAY = 0.20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRAudioRecovery/1.0)"
    )
}


def fetch(url):
    request = Request(
        url,
        headers=HEADERS,
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        return (
            response.geturl(),
            response.headers.get(
                "Content-Type",
                ""
            ),
            response.read().decode(
                "utf-8",
                errors="replace"
            ),
        )


def clean(value):
    if not value:
        return ""

    value = html.unescape(value)
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")

    return value.strip()


def unique(values):
    output = []

    for value in values:
        value = clean(value)

        if value and value not in output:
            output.append(value)

    return output


def is_image(url):
    u = url.lower()

    return (
        "/assets/img/" in u
        or any(
            ext in u
            for ext in [
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp",
                ".svg",
            ]
        )
    )


def is_station_stream(url):
    u = url.lower()

    return (
        "livestream-redirect" in u
        or "streamtheworld.com" in u
    )


def classify_audio_url(url):
    u = url.lower()

    if is_image(url):
        return "image"

    if is_station_stream(url):
        return "station_stream"

    if ".mp3" in u:
        return "direct_mp3"

    if ".m4a" in u:
        return "direct_m4a"

    if ".aac" in u:
        return "direct_aac"

    if "ondemand.npr.org" in u:
        return "npr_ondemand"

    if "play.podtrac.com" in u:
        return "podtrac"

    if "npr.org/player" in u:
        return "npr_player"

    if "audio" in u:
        return "possible_audio"

    return "other"


def extract_meta_content(page, name):
    patterns = [
        (
            rf'<meta[^>]+property=["\']{re.escape(name)}["\']'
            rf'[^>]+content=["\']([^"\']+)'
        ),
        (
            rf'<meta[^>]+content=["\']([^"\']+)["\']'
            rf'[^>]+property=["\']{re.escape(name)}["\']'
        ),
        (
            rf'<meta[^>]+name=["\']{re.escape(name)}["\']'
            rf'[^>]+content=["\']([^"\']+)'
        ),
        (
            rf'<meta[^>]+content=["\']([^"\']+)["\']'
            rf'[^>]+name=["\']{re.escape(name)}["\']'
        ),
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            page,
            re.I | re.S
        )

        if match:
            return clean(
                match.group(1)
            )

    return None


def extract_audio_candidates(page):
    candidates = []

    patterns = [
        r'https?://[^"\'<>\s\\]+\.mp3(?:\?[^"\'<>\s\\]*)?',
        r'https?://[^"\'<>\s\\]+\.m4a(?:\?[^"\'<>\s\\]*)?',
        r'https?://[^"\'<>\s\\]+\.aac(?:\?[^"\'<>\s\\]*)?',
        r'https?://ondemand\.npr\.org/[^"\'<>\s\\]+',
        r'https?://play\.podtrac\.com/[^"\'<>\s\\]+',
        r'https?://[^"\'<>\s\\]*audio[^"\'<>\s\\]+',
        r'https?://www\.npr\.org/player/[^"\'<>\s\\]+',
    ]

    for pattern in patterns:
        candidates.extend(
            re.findall(
                pattern,
                page,
                re.I
            )
        )

    candidates = unique(candidates)

    results = []

    for url in candidates:
        kind = classify_audio_url(url)

        if kind in {
            "image",
            "station_stream",
            "other",
        }:
            continue

        results.append({
            "url": url,
            "type": kind,
        })

    return results


def extract_player_ids(page):
    ids = []

    patterns = [
        r'/player/embed/(\d{6,})/(\d{6,})',
        r'"audioId"\s*:\s*"?(\d{6,})"?',
        r'"audio_id"\s*:\s*"?(\d{6,})"?',
        r'"storyId"\s*:\s*"?(\d{6,})"?',
        r'"story_id"\s*:\s*"?(\d{6,})"?',
    ]

    for pattern in patterns:

        for match in re.findall(
            pattern,
            page,
            re.I
        ):
            if isinstance(match, tuple):
                for value in match:
                    ids.append(str(value))
            else:
                ids.append(str(match))

    return unique(ids)


def extract_jsonld(page):
    useful = []

    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r'(.*?)'
        r'</script>',
        page,
        re.I | re.S
    )

    for block in blocks:
        block = clean(block)

        lower = block.lower()

        if any(
            marker in lower
            for marker in [
                "audioobject",
                "contenturl",
                "embedurl",
                '"audio"',
            ]
        ):
            useful.append(
                block[:12000]
            )

    return useful[:20]


def choose_best_audio(candidates):
    priorities = {
        "npr_ondemand": 100,
        "direct_mp3": 90,
        "direct_m4a": 85,
        "direct_aac": 80,
        "podtrac": 70,
        "npr_player": 60,
        "possible_audio": 40,
    }

    if not candidates:
        return None

    ordered = sorted(
        candidates,
        key=lambda item: priorities.get(
            item["type"],
            0
        ),
        reverse=True
    )

    return ordered[0]


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    identity_data = json.load(file)


identity_results = identity_data.get(
    "results",
    []
)

# We will probe strong identities plus the six
# "needs_review" records, but not entries with no NPR URL.
targets = [
    item
    for item in identity_results
    if item.get("selected_npr_url")
]


summary = {
    "identity_input_count":
        len(identity_results),

    "npr_pages_attempted":
        len(targets),

    "npr_pages_fetched":
        0,

    "npr_pages_failed":
        0,

    "pages_with_audio_candidates":
        0,

    "pages_with_player_ids":
        0,

    "high_confidence_audio_count":
        0,

    "no_audio_found_count":
        0,
}


results = []


for number, identity in enumerate(
    targets,
    start=1
):

    npr_url = identity.get(
        "selected_npr_url"
    )

    print()
    print(
        f"[{number}/{len(targets)}] "
        f"{identity.get('reference_date')} - "
        f"{identity.get('reference_title')}"
    )

    result = {
        "reference_date":
            identity.get(
                "reference_date"
            ),

        "reference_title":
            identity.get(
                "reference_title"
            ),

        "identity_status":
            identity.get(
                "status"
            ),

        "npr_story_id":
            identity.get(
                "npr_story_id"
            ),

        "npr_url":
            npr_url,

        "status":
            None,
    }

    try:
        (
            final_url,
            content_type,
            page,
        ) = fetch(npr_url)

        summary[
            "npr_pages_fetched"
        ] += 1

    except HTTPError as exc:
        summary[
            "npr_pages_failed"
        ] += 1

        result["status"] = (
            f"http_{exc.code}"
        )

        results.append(result)
        continue

    except (
        URLError,
        TimeoutError,
    ) as exc:
        summary[
            "npr_pages_failed"
        ] += 1

        result["status"] = (
            "request_error"
        )

        result["error"] = str(exc)

        results.append(result)
        continue

    except Exception as exc:
        summary[
            "npr_pages_failed"
        ] += 1

        result["status"] = "error"
        result["error"] = str(exc)

        results.append(result)
        continue

    audio_candidates = (
        extract_audio_candidates(
            page
        )
    )

    player_ids = extract_player_ids(
        page
    )

    jsonld = extract_jsonld(
        page
    )

    og_audio = (
        extract_meta_content(
            page,
            "og:audio"
        )
    )

    twitter_player = (
        extract_meta_content(
            page,
            "twitter:player"
        )
    )

    if og_audio:
        candidate = {
            "url": og_audio,
            "type": classify_audio_url(
                og_audio
            ),
            "source": "og:audio",
        }

        if candidate["type"] not in {
            "image",
            "station_stream",
            "other",
        }:
            audio_candidates.append(
                candidate
            )

    if audio_candidates:
        summary[
            "pages_with_audio_candidates"
        ] += 1

    if player_ids:
        summary[
            "pages_with_player_ids"
        ] += 1

    best_audio = choose_best_audio(
        audio_candidates
    )

    high_confidence = (
        best_audio is not None
        and best_audio.get("type")
        in {
            "npr_ondemand",
            "direct_mp3",
            "direct_m4a",
            "direct_aac",
        }
    )

    if high_confidence:
        summary[
            "high_confidence_audio_count"
        ] += 1

        status = (
            "high_confidence_audio"
        )

    elif (
        audio_candidates
        or player_ids
        or twitter_player
    ):
        status = (
            "audio_identity_found"
        )

    else:
        status = "no_audio_found"

        summary[
            "no_audio_found_count"
        ] += 1

    result.update({
        "status":
            status,

        "final_npr_url":
            final_url,

        "content_type":
            content_type,

        "best_audio":
            best_audio,

        "audio_candidates":
            audio_candidates[:30],

        "player_ids":
            player_ids[:30],

        "og_audio":
            og_audio,

        "twitter_player":
            twitter_player,

        "jsonld_audio_blocks":
            jsonld,
    })

    results.append(result)

    print(
        "  Status:",
        status
    )

    if best_audio:
        print(
            "  Best audio:",
            best_audio["type"],
            best_audio["url"]
        )

    if player_ids:
        print(
            "  Player IDs:",
            player_ids[:5]
        )

    time.sleep(
        REQUEST_DELAY
    )


report = {
    "method":
        "original-npr-page-audio-recovery",

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
    "NPR audio recovery complete"
)

for key, value in summary.items():
    print(
        f"{key}: {value}"
    )

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
