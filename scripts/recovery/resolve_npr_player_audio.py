#!/usr/bin/env python3
from pathlib import Path

import html
import json
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
REPO_ROOT = Path(__file__).resolve().parents[2]


INPUT_FILE = str(REPO_ROOT / "indicator_npr_audio_review.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_player_resolution.json")
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRPlayerResolver/2.0)"
    )
}


def fetch_text(url):
    request = Request(url, headers=HEADERS)

    with urlopen(request, timeout=TIMEOUT) as response:
        return (
            response.geturl(),
            response.headers.get("Content-Type", ""),
            response.read().decode("utf-8", errors="replace"),
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


def extract_embed_ids(page):
    """
    Find NPR player embeds such as:

    /player/embed/662706955/662707862
    """

    patterns = [
        r'https?://www\.npr\.org/player/embed/([^/"\'<>\s]+)/([^/?"\'<>\s]+)',
        r'/player/embed/([^/"\'<>\s]+)/([^/?"\'<>\s]+)',
    ]

    pairs = []

    for pattern in patterns:
        for first_id, second_id in re.findall(
            pattern,
            page,
            re.I
        ):
            pair = {
                "player_story_id": clean(first_id),
                "audio_id": clean(second_id),
            }

            if pair not in pairs:
                pairs.append(pair)

    return pairs


def extract_audio_candidates(page):
    patterns = [
        r'https?://[^"\'<>\s\\]+\.mp3(?:\?[^"\'<>\s\\]*)?',
        r'https?://[^"\'<>\s\\]+\.m4a(?:\?[^"\'<>\s\\]*)?',
        r'https?://ondemand\.npr\.org/[^"\'<>\s\\]+',
        r'https?://play\.podtrac\.com/[^"\'<>\s\\]+',
        r'https?://prfx\.byspotify\.com/[^"\'<>\s\\]+',
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


def probe_audio(url):
    request = Request(
        url,
        headers={
            **HEADERS,
            "Range": "bytes=0-4095",
        },
    )

    with urlopen(request, timeout=TIMEOUT) as response:
        final_url = response.geturl()

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        status = getattr(
            response,
            "status",
            None
        )

        sample = response.read(4096)

    return {
        "candidate_url": url,
        "status_code": status,
        "final_url": final_url,
        "content_type": content_type,
        "sample_size": len(sample),
    }


def is_audio_content_type(value):
    if not value:
        return False

    value = value.lower()

    return (
        value.startswith("audio/")
        or "mpeg" in value
        or "mp3" in value
        or "aac" in value
        or "m4a" in value
    )


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    review = json.load(file)


records = review.get("records", [])

results = []


for record in records:
    title = record.get("reference_title")
    npr_url = record.get("npr_url")

    print()
    print(
        record.get("reference_date"),
        "-",
        title
    )

    result = {
        "reference_date":
            record.get("reference_date"),

        "reference_title":
            title,

        "npr_story_id":
            record.get("npr_story_id"),

        "npr_url":
            npr_url,

        "status":
            None,

        "embed_players":
            [],

        "candidate_probes":
            [],
    }

    #
    # Step 1:
    # Re-open the ORIGINAL NPR story page.
    #
    try:
        (
            final_story_url,
            story_content_type,
            story_page,
        ) = fetch_text(npr_url)

    except Exception as exc:
        result["status"] = "story_fetch_failed"
        result["error"] = str(exc)
        results.append(result)
        continue

    result["final_story_url"] = final_story_url
    result["story_content_type"] = story_content_type

    #
    # Step 2:
    # Extract the real player IDs from the story page.
    #
    embed_pairs = extract_embed_ids(
        story_page
    )

    result["embed_players"] = embed_pairs

    print(
        "  Embed player(s):",
        len(embed_pairs)
    )

    #
    # Also check whether the story HTML itself
    # contains a usable audio URL.
    #
    candidates = extract_audio_candidates(
        story_page
    )

    #
    # Step 3:
    # Open each exact NPR embed URL and inspect it too.
    #
    for pair in embed_pairs:
        embed_url = (
            "https://www.npr.org/player/embed/"
            f"{pair['player_story_id']}/"
            f"{pair['audio_id']}"
        )

        pair["embed_url"] = embed_url

        try:
            (
                final_embed_url,
                embed_content_type,
                embed_page,
            ) = fetch_text(embed_url)

            pair[
                "final_embed_url"
            ] = final_embed_url

            pair[
                "embed_content_type"
            ] = embed_content_type

            pair_candidates = (
                extract_audio_candidates(
                    embed_page
                )
            )

            pair[
                "audio_candidates"
            ] = pair_candidates

            candidates.extend(
                pair_candidates
            )

        except Exception as exc:
            pair[
                "embed_fetch_error"
            ] = str(exc)

    candidates = unique(
        candidates
    )

    result[
        "audio_candidates"
    ] = candidates

    #
    # Step 4:
    # Validate every candidate and accept only
    # something that actually returns audio.
    #
    validated = []

    for candidate in candidates:

        try:
            probe = probe_audio(
                candidate
            )

            probe[
                "is_audio"
            ] = is_audio_content_type(
                probe.get(
                    "content_type"
                )
            )

            result[
                "candidate_probes"
            ].append(
                probe
            )

            if probe["is_audio"]:
                validated.append(
                    probe
                )

        except HTTPError as exc:
            result[
                "candidate_probes"
            ].append({
                "candidate_url":
                    candidate,

                "status":
                    f"http_{exc.code}",

                "error":
                    str(exc),
            })

        except (
            URLError,
            TimeoutError,
        ) as exc:
            result[
                "candidate_probes"
            ].append({
                "candidate_url":
                    candidate,

                "status":
                    "request_error",

                "error":
                    str(exc),
            })

        except Exception as exc:
            result[
                "candidate_probes"
            ].append({
                "candidate_url":
                    candidate,

                "status":
                    "error",

                "error":
                    str(exc),
            })

    if validated:
        result["status"] = "resolved"
        result["best_audio"] = validated[0]

        print(
            "  RESOLVED:"
        )

        print(
            "  Player story ID:",
            embed_pairs[0][
                "player_story_id"
            ] if embed_pairs else None
        )

        print(
            "  Audio ID:",
            embed_pairs[0][
                "audio_id"
            ] if embed_pairs else None
        )

        print(
            "  Final audio:",
            validated[0].get(
                "final_url"
            )
        )

    elif embed_pairs:
        result[
            "status"
        ] = "player_ids_found_but_audio_unresolved"

        print(
            "  Player IDs found, "
            "but audio URL still unresolved."
        )

    else:
        result[
            "status"
        ] = "no_embed_player_found"

        print(
            "  No embed player found."
        )

    results.append(
        result
    )


report = {
    "method":
        "npr-story-to-embed-to-audio-resolution",

    "input_count":
        len(records),

    "embed_player_found_count":
        sum(
            1
            for item in results
            if item.get("embed_players")
        ),

    "resolved_count":
        sum(
            1
            for item in results
            if item.get("status")
            == "resolved"
        ),

    "unresolved_count":
        sum(
            1
            for item in results
            if item.get("status")
            != "resolved"
        ),

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
    "NPR player resolver v2 complete"
)
print(
    "Input:",
    report["input_count"]
)
print(
    "Embed players found:",
    report[
        "embed_player_found_count"
    ]
)
print(
    "Resolved:",
    report["resolved_count"]
)
print(
    "Unresolved:",
    report["unresolved_count"]
)
print(
    "Saved:",
    OUTPUT_FILE
)
print(
    "================================"
)
