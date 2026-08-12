#!/usr/bin/env python3
from pathlib import Path

import json
from collections import Counter
from urllib.parse import urlparse, parse_qs
REPO_ROOT = Path(__file__).resolve().parents[2]



RECOVERY_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_recovery.json")
VALIDATION_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_validation.json")
OUTPUT_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_redirect_mapping.json")
def load_json(filename):
    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def clean_id(value):
    if value is None:
        return None

    value = str(value).strip()

    digits = "".join(
        character
        for character in value
        if character.isdigit()
    )

    return digits or None


def query_values(url):
    if not url:
        return {}

    parsed = urlparse(url)

    return parse_qs(
        parsed.query
    )


recovery = load_json(
    RECOVERY_FILE
)

validation = load_json(
    VALIDATION_FILE
)


recovery_by_story = {}

for item in recovery.get(
    "results",
    []
):
    story_id = clean_id(
        item.get(
            "npr_story_id"
        )
    )

    if story_id:
        recovery_by_story[
            story_id
        ] = item


comparison_records = []

counts = Counter()


for item in validation.get(
    "validated_audio",
    []
):

    story_id = clean_id(
        item.get(
            "npr_story_id"
        )
    )

    recovery_item = (
        recovery_by_story.get(
            story_id,
            {}
        )
    )

    player_ids = []

    for value in recovery_item.get(
        "player_ids",
        []
    ):
        cleaned = clean_id(
            value
        )

        if (
            cleaned
            and cleaned
            not in player_ids
        ):
            player_ids.append(
                cleaned
            )

    candidate_url = item.get(
        "audio_url"
    )

    final_url = item.get(
        "final_url"
    )

    candidate_query = (
        query_values(
            candidate_url
        )
    )

    final_query = query_values(
        final_url
    )

    candidate_e = (
        candidate_query.get(
            "e",
            [None]
        )[0]
    )

    candidate_p = (
        candidate_query.get(
            "p",
            [None]
        )[0]
    )

    final_e = (
        final_query.get(
            "e",
            [None]
        )[0]
    )

    final_p = (
        final_query.get(
            "p",
            [None]
        )[0]
    )

    candidate_e = clean_id(
        candidate_e
    )

    final_e = clean_id(
        final_e
    )

    matches_story = (
        bool(candidate_e)
        and candidate_e
        == story_id
    )

    matches_player = (
        bool(candidate_e)
        and candidate_e
        in player_ids
    )

    if candidate_e:
        counts[
            "candidate_has_e"
        ] += 1

    if candidate_p == "510325":
        counts[
            "candidate_p_is_510325"
        ] += 1

    if matches_story:
        counts[
            "e_matches_story_id"
        ] += 1

    if matches_player:
        counts[
            "e_matches_player_id"
        ] += 1

    if (
        candidate_e
        and not matches_story
        and not matches_player
    ):
        counts[
            "e_matches_neither"
        ] += 1

    if final_e:
        counts[
            "final_has_e"
        ] += 1

    if final_p == "510325":
        counts[
            "final_p_is_510325"
        ] += 1

    comparison_records.append({
        "reference_date":
            item.get(
                "reference_date"
            ),

        "reference_title":
            item.get(
                "reference_title"
            ),

        "npr_story_id":
            story_id,

        "player_ids":
            player_ids,

        "candidate_audio_url":
            candidate_url,

        "candidate_query_e":
            candidate_e,

        "candidate_query_p":
            candidate_p,

        "final_audio_url":
            final_url,

        "final_query_e":
            final_e,

        "final_query_p":
            final_p,

        "e_matches_story_id":
            matches_story,

        "e_matches_player_id":
            matches_player,
    })


unresolved = [
    {
        "reference_date":
            "2018-10-31",

        "reference_title":
            "Paranormal Profits",

        "npr_story_id":
            "662708285",

        "player_story_id":
            "662706955",

        "audio_id":
            "662707862",
    },

    {
        "reference_date":
            "2019-04-22",

        "reference_title":
            "The Traffic Tariff",

        "npr_story_id":
            "716132270",

        "player_story_id":
            "716127469",

        "audio_id":
            "730102905",
    },
]


report = {
    "method":
        "analyze-npr-podcast-redirect-query-mapping",

    "summary": {
        "working_count":
            len(comparison_records),

        **dict(counts),
    },

    "unresolved":
        unresolved,

    "working_examples":
        comparison_records[:25],

    "all_working_records":
        comparison_records,
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
    "NPR redirect mapping analysis complete"
)

for key, value in report[
    "summary"
].items():

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
