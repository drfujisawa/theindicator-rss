#!/usr/bin/env python3
from pathlib import Path

import json
import re
from urllib.parse import urlparse
REPO_ROOT = Path(__file__).resolve().parents[2]



INPUT_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_unresolved_web_discovery.json")
OUTPUT_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_unresolved_web_audio_strict_validation.json")
def load_json(filename):
    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def normalize_title(value):
    if not value:
        return ""

    value = value.lower()
    value = value.replace("&", " and ")
    value = value.replace("’", "'")

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return " ".join(
        value.split()
    )


def hostname(url):
    try:
        return (
            urlparse(url)
            .hostname
            or ""
        ).lower()
    except Exception:
        return ""


def path(url):
    try:
        return (
            urlparse(url)
            .path
            or ""
        ).lower()
    except Exception:
        return ""


def indicator_npr_audio(url):
    if not url:
        return False

    host = hostname(url)
    audio_path = path(url)

    return (
        host == "ondemand.npr.org"
        and "/indicator/" in audio_path
        and audio_path.endswith(".mp3")
    )


def title_tokens(title):
    ignored = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "is",
        "are",
        "was",
        "were",
        "why",
        "what",
        "how",
        "did",
        "do",
        "does",
        "from",
        "that",
        "this",
    }

    return [
        word
        for word in normalize_title(
            title
        ).split()
        if (
            len(word) >= 4
            and word not in ignored
        )
    ]


def page_target_score(
    target_title,
    target_date,
    report
):
    score = 0
    reasons = []

    requested = (
        report.get(
            "requested_url",
            ""
        )
        or ""
    ).lower()

    final_url = (
        report.get(
            "final_url",
            ""
        )
        or ""
    ).lower()

    blob = " ".join([
        requested,
        final_url,
        json.dumps(
            report.get(
                "clues",
                {}
            ),
            ensure_ascii=False
        ).lower(),
    ])

    normalized_blob = normalize_title(
        blob
    )

    tokens = title_tokens(
        target_title
    )

    matched = [
        token
        for token in tokens
        if token in normalized_blob
    ]

    if tokens:
        ratio = (
            len(matched)
            / len(tokens)
        )
    else:
        ratio = 0

    if ratio >= 0.75:
        score += 4
        reasons.append(
            "strong_title_overlap"
        )

    elif ratio >= 0.5:
        score += 2
        reasons.append(
            "moderate_title_overlap"
        )

    if (
        target_date
        and target_date
        in blob
    ):
        score += 2
        reasons.append(
            "date_present"
        )

    clues = report.get(
        "clues",
        {}
    )

    if clues.get(
        "npr_story_urls"
    ):
        score += 3
        reasons.append(
            "npr_story_url_present"
        )

    if clues.get(
        "player_embeds"
    ):
        score += 3
        reasons.append(
            "npr_player_present"
        )

    if any(
        "ondemand.npr.org"
        in (
            value or ""
        )
        for value in clues.get(
            "audio_urls",
            []
        )
    ):
        score += 3
        reasons.append(
            "npr_audio_present"
        )

    return score, reasons


data = load_json(
    INPUT_FILE
)


targets = [
    item
    for item in data.get(
        "results",
        []
    )
    if item.get(
        "status"
    ) == "episode_audio_recovered"
]


results = []


for item in targets:

    title = item.get(
        "title"
    )

    date = item.get(
        "date"
    )

    validations = item.get(
        "validated_audio",
        []
    )

    page_reports = item.get(
        "page_reports",
        []
    )


    # ------------------------------------------
    # Audio quality
    # ------------------------------------------

    good_audio = []

    non_npr_audio = []

    for audio in validations:

        final_url = (
            audio.get(
                "final_url"
            )
            or audio.get(
                "candidate_url"
            )
        )

        if indicator_npr_audio(
            final_url
        ):
            good_audio.append(
                audio
            )
        else:
            non_npr_audio.append(
                audio
            )


    # ------------------------------------------
    # Source-page relationship
    # ------------------------------------------

    scored_pages = []

    for report in page_reports:

        score, reasons = (
            page_target_score(
                title,
                date,
                report
            )
        )

        if score > 0:
            scored_pages.append({
                "requested_url":
                    report.get(
                        "requested_url"
                    ),

                "final_url":
                    report.get(
                        "final_url"
                    ),

                "score":
                    score,

                "reasons":
                    reasons,
            })


    scored_pages.sort(
        key=lambda record:
            record["score"],
        reverse=True
    )

    best_page_score = (
        scored_pages[0][
            "score"
        ]
        if scored_pages
        else 0
    )


    # ------------------------------------------
    # Final classification
    # ------------------------------------------

    reasons = []


    if good_audio:
        reasons.append(
            "validated_npr_indicator_mp3"
        )


    if best_page_score >= 5:
        reasons.append(
            "strong_source_page_relationship"
        )


    if (
        good_audio
        and best_page_score >= 5
    ):

        status = (
            "confirmed_npr_episode_audio"
        )

    elif good_audio:

        status = (
            "suspicious_audio_candidate"
        )

        reasons.append(
            "npr_audio_but_source_relationship_weak"
        )

    else:

        status = (
            "rejected_audio_candidate"
        )

        reasons.append(
            "no_validated_npr_indicator_mp3"
        )


    results.append({
        "date":
            date,

        "title":
            title,

        "status":
            status,

        "reasons":
            reasons,

        "npr_indicator_audio":
            good_audio,

        "non_npr_audio":
            non_npr_audio,

        "best_page_score":
            best_page_score,

        "best_page_evidence":
            scored_pages[:5],
    })


summary = {
    "confirmed_npr_episode_audio":
        sum(
            1
            for item in results
            if item["status"]
            == "confirmed_npr_episode_audio"
        ),

    "suspicious_audio_candidate":
        sum(
            1
            for item in results
            if item["status"]
            == "suspicious_audio_candidate"
        ),

    "rejected_audio_candidate":
        sum(
            1
            for item in results
            if item["status"]
            == "rejected_audio_candidate"
        ),
}


report = {
    "method":
        "strict-validation-of-web-discovered-indicator-audio",

    "input_candidate_count":
        len(results),

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
    "STRICT WEB AUDIO VALIDATION"
)

print(
    "================================"
)

print(
    "Input candidates:",
    len(results)
)

for key, value in (
    summary.items()
):
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
