#!/usr/bin/env python3

import json
import os
import re
from collections import defaultdict
from urllib.parse import urlparse


INPUT_FILE = "indicator_unresolved_batch_recovery.json"
OUTPUT_FILE = "indicator_unresolved_strict_review.json"


REJECT_DOMAINS = {
    "playerservices.streamtheworld.com",
    "streamtheworld.com",
}

GOOD_AUDIO_DOMAINS = {
    "ondemand.npr.org",
}

AUDIO_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".aac",
    ".mp4",
)


def load_json(filename):
    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)


def clean_url(url):
    if not url:
        return None

    return str(url).strip()


def hostname(url):
    try:
        return (
            urlparse(url)
            .hostname
            or ""
        ).lower()

    except Exception:
        return ""


def path_lower(url):
    try:
        return (
            urlparse(url)
            .path
            .lower()
        )

    except Exception:
        return ""


def is_rejected_stream(url):
    host = hostname(url)

    if host in REJECT_DOMAINS:
        return True

    lower = url.lower()

    rejection_markers = [
        "livestream",
        "live-stream",
        "/live/",
        "streamtheworld",
        "icecast",
        "shoutcast",
        "radio-stream",
    ]

    return any(
        marker in lower
        for marker in rejection_markers
    )


def looks_episode_specific(url):
    if not url:
        return False

    host = hostname(url)
    path = path_lower(url)

    if is_rejected_stream(url):
        return False

    # NPR-hosted episode audio is strongest.
    if (
        host in GOOD_AUDIO_DOMAINS
        and path.endswith(
            AUDIO_EXTENSIONS
        )
    ):
        return True

    # Podcast delivery wrappers can be valid
    # if they clearly contain an episode file.
    if (
        "prfx.byspotify.com"
        in host
        or "play.podtrac.com"
        in host
    ):
        if ".mp3" in url.lower():
            return True

    # Generic direct MP3 may still be useful,
    # but keep it as candidate rather than proven.
    if path.endswith(
        AUDIO_EXTENSIONS
    ):
        return True

    return False


def normalize_title(value):
    if not value:
        return ""

    value = value.lower()
    value = value.replace("&", " and ")
    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return " ".join(
        value.split()
    )


data = load_json(
    INPUT_FILE
)

results = []


for item in data.get(
    "results",
    []
):

    status = item.get(
        "status"
    )

    title = item.get(
        "title"
    )

    date = item.get(
        "date"
    )

    direct_audio_candidates = []
    rejected_streams = []
    useful_pages = []
    ids = {}
    evidence_sources = []


    # ------------------------------------------
    # Prior evidence
    # ------------------------------------------

    for evidence in item.get(
        "prior_evidence",
        []
    ):

        evidence_sources.append({
            "file":
                evidence.get(
                    "file"
                ),

            "path":
                evidence.get(
                    "path"
                ),
        })

        for key, value in (
            evidence.get(
                "ids",
                {}
            )
            or {}
        ).items():

            if value:
                ids[key] = value

        for url in (
            evidence.get(
                "urls",
                []
            )
            or []
        ):

            url = clean_url(
                url
            )

            if not url:
                continue

            if is_rejected_stream(
                url
            ):
                rejected_streams.append(
                    url
                )

            elif looks_episode_specific(
                url
            ):
                direct_audio_candidates.append(
                    url
                )

            else:
                useful_pages.append(
                    url
                )


    # ------------------------------------------
    # Previous page probes
    # ------------------------------------------

    for probe in item.get(
        "page_probes",
        []
    ):

        url = probe.get(
            "final_url"
        ) or probe.get(
            "url"
        )

        if url:
            useful_pages.append(
                url
            )

        for candidate in probe.get(
            "audio_candidates",
            []
        ):

            if is_rejected_stream(
                candidate
            ):
                rejected_streams.append(
                    candidate
                )

            elif looks_episode_specific(
                candidate
            ):
                direct_audio_candidates.append(
                    candidate
                )


    # ------------------------------------------
    # Previous "validated" audio
    # ------------------------------------------

    for audio in item.get(
        "validated_audio",
        []
    ):

        url = (
            audio.get(
                "final_url"
            )
            or audio.get(
                "candidate_url"
            )
        )

        if not url:
            continue

        if is_rejected_stream(
            url
        ):
            rejected_streams.append(
                url
            )

        elif looks_episode_specific(
            url
        ):
            direct_audio_candidates.append(
                url
            )


    # ------------------------------------------
    # Deduplicate
    # ------------------------------------------

    direct_audio_candidates = list(
        dict.fromkeys(
            direct_audio_candidates
        )
    )

    rejected_streams = list(
        dict.fromkeys(
            rejected_streams
        )
    )

    useful_pages = list(
        dict.fromkeys(
            useful_pages
        )
    )


    # ------------------------------------------
    # Strict classification
    # ------------------------------------------

    if item.get(
        "duplicate_reference_dates"
    ):

        strict_status = (
            "possible_duplicate_or_rebroadcast"
        )

    elif direct_audio_candidates:

        # Still call this a candidate until
        # separately HTTP-validated.
        strict_status = (
            "episode_audio_candidate_found"
        )

    elif ids or useful_pages:

        strict_status = (
            "has_identity_or_page_evidence"
        )

    else:

        strict_status = (
            "needs_fresh_discovery"
        )


    results.append({
        "date":
            date,

        "title":
            title,

        "reference_year":
            item.get(
                "reference_year"
            ),

        "reference_episode":
            item.get(
                "reference_episode"
            ),

        "previous_status":
            status,

        "strict_status":
            strict_status,

        "duplicate_reference_dates":
            item.get(
                "duplicate_reference_dates",
                []
            ),

        "known_ids":
            ids,

        "episode_audio_candidates":
            direct_audio_candidates,

        "rejected_stream_urls":
            rejected_streams,

        "useful_page_urls":
            useful_pages,

        "evidence_sources":
            evidence_sources,
    })


counts = defaultdict(int)

for item in results:
    counts[
        item[
            "strict_status"
        ]
    ] += 1


report = {
    "method":
        "strict-review-of-unresolved-indicator-evidence",

    "input_count":
        len(results),

    "summary": {
        "possible_duplicate_or_rebroadcast":
            counts[
                "possible_duplicate_or_rebroadcast"
            ],

        "episode_audio_candidate_found":
            counts[
                "episode_audio_candidate_found"
            ],

        "has_identity_or_page_evidence":
            counts[
                "has_identity_or_page_evidence"
            ],

        "needs_fresh_discovery":
            counts[
                "needs_fresh_discovery"
            ],

        "rejected_stream_record_count":
            sum(
                1
                for item in results
                if item[
                    "rejected_stream_urls"
                ]
            ),
    },

    "results":
        results,
}


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
    "STRICT UNRESOLVED REVIEW"
)

print(
    "================================"
)

print(
    "Input:",
    report[
        "input_count"
    ]
)

for key, value in report[
    "summary"
].items():

    print(
        key + ":",
        value
    )

print()
print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
