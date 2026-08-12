#!/usr/bin/env python3
from pathlib import Path

import json
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse, parse_qs
REPO_ROOT = Path(__file__).resolve().parents[2]



RECOVERY_FILE = str(REPO_ROOT / "indicator_npr_audio_recovery.json")
VALIDATION_FILE = str(REPO_ROOT / "indicator_npr_audio_validation.json")
PLAYER_FILE = str(REPO_ROOT / "indicator_npr_player_resolution.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_audio_mapping.json")
def load_json(filename):
    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)


def clean_id(value):
    if value is None:
        return None

    value = str(value)

    match = re.search(
        r"\d{5,}",
        value
    )

    return (
        match.group(0)
        if match
        else None
    )


def numeric_tokens(value):
    if not value:
        return []

    return re.findall(
        r"\d{5,}",
        str(value)
    )


def analyze_url(url):
    if not url:
        return {}

    parsed = urlparse(url)

    return {
        "scheme": parsed.scheme,
        "domain": parsed.netloc,
        "path": parsed.path,
        "filename":
            parsed.path.rstrip("/").split("/")[-1]
            if parsed.path
            else None,
        "query": parse_qs(parsed.query),
        "numeric_tokens": numeric_tokens(url),
    }


recovery = load_json(
    RECOVERY_FILE
)

validation = load_json(
    VALIDATION_FILE
)

player_resolution = load_json(
    PLAYER_FILE
)


#
# Index recovery records by NPR story ID.
#
recovery_by_story = {}

for item in recovery.get(
    "results",
    []
):
    story_id = clean_id(
        item.get("npr_story_id")
    )

    if story_id:
        recovery_by_story[
            story_id
        ] = item


working = []

audio_id_occurrence_counts = Counter()
player_id_occurrence_counts = Counter()

domain_counts = Counter()
path_patterns = Counter()

examples_by_domain = defaultdict(list)


#
# Examine the 232 successfully validated records.
#
for item in validation.get(
    "validated_audio",
    []
):

    story_id = clean_id(
        item.get("npr_story_id")
    )

    recovery_item = (
        recovery_by_story.get(
            story_id,
            {}
        )
    )

    player_ids = [
        clean_id(value)
        for value
        in recovery_item.get(
            "player_ids",
            []
        )
    ]

    player_ids = [
        value
        for value in player_ids
        if value
    ]

    audio_url = item.get(
        "audio_url"
    )

    final_url = item.get(
        "final_url"
    )

    final_analysis = analyze_url(
        final_url
    )

    candidate_analysis = analyze_url(
        audio_url
    )

    final_tokens = set(
        final_analysis.get(
            "numeric_tokens",
            []
        )
    )

    candidate_tokens = set(
        candidate_analysis.get(
            "numeric_tokens",
            []
        )
    )

    id_matches = []

    for player_id in player_ids:

        appears_in_final = (
            player_id in final_tokens
            or player_id in (
                final_url or ""
            )
        )

        appears_in_candidate = (
            player_id in candidate_tokens
            or player_id in (
                audio_url or ""
            )
        )

        if appears_in_final:
            audio_id_occurrence_counts[
                "player_id_in_final_url"
            ] += 1

        if appears_in_candidate:
            audio_id_occurrence_counts[
                "player_id_in_candidate_url"
            ] += 1

        id_matches.append({
            "player_id":
                player_id,

            "appears_in_candidate_url":
                appears_in_candidate,

            "appears_in_final_url":
                appears_in_final,
        })

    if story_id:

        if (
            story_id in final_tokens
            or story_id in (
                final_url or ""
            )
        ):
            player_id_occurrence_counts[
                "story_id_in_final_url"
            ] += 1

        if (
            story_id in candidate_tokens
            or story_id in (
                audio_url or ""
            )
        ):
            player_id_occurrence_counts[
                "story_id_in_candidate_url"
            ] += 1

    domain = final_analysis.get(
        "domain"
    )

    if domain:
        domain_counts[
            domain
        ] += 1

    path = final_analysis.get(
        "path",
        ""
    )

    #
    # Replace long numeric sequences to reveal
    # common URL templates.
    #
    path_pattern = re.sub(
        r"\d{5,}",
        "{ID}",
        path
    )

    if path_pattern:
        path_patterns[
            path_pattern
        ] += 1

    record = {
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
            audio_url,

        "validated_final_url":
            final_url,

        "candidate_url_analysis":
            candidate_analysis,

        "final_url_analysis":
            final_analysis,

        "player_id_matches":
            id_matches,
    }

    working.append(
        record
    )

    if (
        domain
        and len(
            examples_by_domain[
                domain
            ]
        ) < 5
    ):
        examples_by_domain[
            domain
        ].append(
            record
        )


#
# Pull the two unresolved NPR embed identities.
#
unresolved = []

for item in player_resolution.get(
    "results",
    []
):

    embed_players = []

    seen_pairs = set()

    for player in item.get(
        "embed_players",
        []
    ):

        player_story_id = clean_id(
            player.get(
                "player_story_id"
            )
        )

        audio_id = clean_id(
            player.get(
                "audio_id"
            )
        )

        pair = (
            player_story_id,
            audio_id
        )

        if (
            not player_story_id
            or not audio_id
            or pair in seen_pairs
        ):
            continue

        seen_pairs.add(
            pair
        )

        embed_players.append({
            "player_story_id":
                player_story_id,

            "audio_id":
                audio_id,
        })

    unresolved.append({
        "reference_date":
            item.get(
                "reference_date"
            ),

        "reference_title":
            item.get(
                "reference_title"
            ),

        "npr_story_id":
            clean_id(
                item.get(
                    "npr_story_id"
                )
            ),

        "npr_url":
            item.get(
                "npr_url"
            ),

        "embed_players":
            embed_players,
    })


#
# Find working records whose player IDs have
# a similar number of digits to the unresolved
# NPR audio IDs. These may be useful examples
# for manually comparing NPR URL formats.
#
unresolved_audio_ids = []

for item in unresolved:
    for player in item.get(
        "embed_players",
        []
    ):
        audio_id = player.get(
            "audio_id"
        )

        if audio_id:
            unresolved_audio_ids.append(
                audio_id
            )


comparison_examples = []

for target_id in unresolved_audio_ids:

    target_length = len(
        target_id
    )

    examples = []

    for item in working:

        matching_length_ids = [
            value
            for value
            in item.get(
                "player_ids",
                []
            )
            if len(value)
            == target_length
        ]

        if matching_length_ids:

            examples.append({
                "reference_date":
                    item.get(
                        "reference_date"
                    ),

                "reference_title":
                    item.get(
                        "reference_title"
                    ),

                "npr_story_id":
                    item.get(
                        "npr_story_id"
                    ),

                "player_ids":
                    matching_length_ids,

                "candidate_audio_url":
                    item.get(
                        "candidate_audio_url"
                    ),

                "validated_final_url":
                    item.get(
                        "validated_final_url"
                    ),
            })

        if len(examples) >= 10:
            break

    comparison_examples.append({
        "target_audio_id":
            target_id,

        "working_examples":
            examples,
    })


report = {
    "method":
        "compare-working-npr-player-ids-to-ondemand-audio",

    "summary": {
        "working_audio_count":
            len(working),

        "unresolved_episode_count":
            len(unresolved),

        "final_audio_domains":
            dict(
                domain_counts.most_common()
            ),

        "id_occurrence_counts":
            dict(
                audio_id_occurrence_counts
            ),

        "story_id_occurrence_counts":
            dict(
                player_id_occurrence_counts
            ),
    },

    "most_common_final_path_patterns": [
        {
            "pattern":
                pattern,

            "count":
                count,
        }
        for pattern, count
        in path_patterns.most_common(
            20
        )
    ],

    "unresolved":
        unresolved,

    "comparison_examples":
        comparison_examples,

    "working_examples_by_domain":
        dict(
            examples_by_domain
        ),
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
    "NPR audio mapping analysis complete"
)

print(
    "Working records:",
    len(working)
)

print(
    "Unresolved episodes:",
    len(unresolved)
)

print(
    "Final audio domains:",
    dict(
        domain_counts
    )
)

print(
    "Player-ID occurrences:",
    dict(
        audio_id_occurrence_counts
    )
)

print(
    "Story-ID occurrences:",
    dict(
        player_id_occurrence_counts
    )
)

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
