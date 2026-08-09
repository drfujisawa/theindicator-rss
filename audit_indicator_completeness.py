#!/usr/bin/env python3

import json
import re
from collections import defaultdict
from datetime import datetime


OUTPUT_FILE = "indicator_completeness_audit.json"

HISTORY_FILE = "indicator_history.json"
EARLY_AUDIT_FILE = "indicator_early_audit.json"
NPR_VALIDATION_FILE = "indicator_npr_audio_validation.json"
MULTI_ARCHIVE_FILE = "indicator_multi_archive_player_probe.json"
WBUR_FILE = "indicator_wbur_traffic_tariff_probe.json"


def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print("Could not read", filename, exc)
        return {}


def normalize_title(value):
    if not value:
        return ""

    value = value.lower()
    value = value.replace("&", " and ")
    value = value.replace("’", "'")

    value = re.sub(
        r"\b(rebroadcast|rerun|re-air|reair)\b",
        "",
        value
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return " ".join(value.split())


def normalize_date(value):
    if not value:
        return None

    value = str(value)

    match = re.search(
        r"(\d{4})-(\d{2})-(\d{2})",
        value
    )

    if not match:
        return None

    return "-".join(
        match.groups()
    )


def extract_episode_list(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in [
        "episodes",
        "results",
        "items",
        "validated_audio",
        "recovered",
    ]:
        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


def episode_title(item):
    if not isinstance(item, dict):
        return None

    for key in [
        "reference_title",
        "title",
        "npr_title",
        "source_title",
    ]:
        value = item.get(key)

        if value:
            return str(value)

    return None


def episode_date(item):
    if not isinstance(item, dict):
        return None

    for key in [
        "reference_date",
        "date",
        "pub_date",
        "published",
    ]:
        value = item.get(key)

        normalized = normalize_date(
            value
        )

        if normalized:
            return normalized

    return None


def key_for(date, title):
    return (
        normalize_date(date),
        normalize_title(title)
    )


history = load_json(
    HISTORY_FILE
)

early_audit = load_json(
    EARLY_AUDIT_FILE
)

validation = load_json(
    NPR_VALIDATION_FILE
)

multi_archive = load_json(
    MULTI_ARCHIVE_FILE
)

wbur = load_json(
    WBUR_FILE
)


# --------------------------------------------------
# Existing archive
# --------------------------------------------------

history_episodes = extract_episode_list(
    history
)

history_keys = set()
history_records = []

for item in history_episodes:
    date = episode_date(item)
    title = episode_title(item)

    if not date or not title:
        continue

    history_keys.add(
        key_for(date, title)
    )

    history_records.append({
        "date": date,
        "title": title,
        "source": "indicator_history.json",
    })


# --------------------------------------------------
# External/reference early-history list
# --------------------------------------------------

reference_records = []

if isinstance(early_audit, dict):

    # The original audit normally stores these
    # separately.
    for section in [
        "possible_missing",
        "matched",
        "reference_episodes",
        "episodes",
    ]:

        values = early_audit.get(
            section
        )

        if not isinstance(values, list):
            continue

        for item in values:
            date = episode_date(item)
            title = episode_title(item)

            if not date or not title:
                continue

            record = {
                "date": date,
                "title": title,
                "reference_year":
                    item.get(
                        "reference_year"
                    ),

                "reference_episode":
                    item.get(
                        "reference_episode"
                    ),

                "reference_section":
                    section,
            }

            identity = key_for(
                date,
                title
            )

            if not any(
                key_for(
                    existing["date"],
                    existing["title"]
                ) == identity
                for existing
                in reference_records
            ):
                reference_records.append(
                    record
                )


# --------------------------------------------------
# 232 validated NPR recoveries
# --------------------------------------------------

validated_records = []

for item in validation.get(
    "validated_audio",
    []
):
    date = episode_date(item)
    title = episode_title(item)

    if not date or not title:
        continue

    validated_records.append({
        "date": date,
        "title": title,
        "npr_story_id":
            item.get(
                "npr_story_id"
            ),
        "audio_url":
            item.get(
                "final_url"
            )
            or item.get(
                "audio_url"
            ),
        "source":
            "indicator_npr_audio_validation.json",
    })


# --------------------------------------------------
# Paranormal Profits recovered from archived NPR player
# --------------------------------------------------

special_recoveries = []

for target in multi_archive.get(
    "targets",
    []
):
    if (
        normalize_title(
            target.get("title")
        )
        != normalize_title(
            "Paranormal Profits"
        )
    ):
        continue

    found_audio = None

    wayback = target.get(
        "wayback",
        {}
    )

    for capture in wayback.get(
        "fetched_captures",
        []
    ):
        clues = capture.get(
            "audio_clues",
            {}
        )

        mp3s = clues.get(
            "mp3_urls",
            []
        )

        if mp3s:
            found_audio = mp3s[0]
            break

    if found_audio:
        special_recoveries.append({
            "date": "2018-10-31",
            "title": "Paranormal Profits",
            "audio_url": found_audio,
            "source":
                "archived NPR player via Wayback",
        })


# --------------------------------------------------
# The Traffic Tariff recovered through WBUR -> NPR
# --------------------------------------------------

for audio in wbur.get(
    "validated_audio",
    []
):
    if not audio.get(
        "is_audio"
    ):
        continue

    special_recoveries.append({
        "date": "2019-04-22",
        "title": "The Traffic Tariff",
        "audio_url":
            audio.get(
                "final_url"
            )
            or audio.get(
                "candidate_url"
            ),
        "source":
            "WBUR page -> NPR-hosted audio",
    })

    break


# --------------------------------------------------
# Build recovered index
# --------------------------------------------------

recovered_records = (
    validated_records
    + special_recoveries
)

recovered_keys = {
    key_for(
        item["date"],
        item["title"]
    )
    for item in recovered_records
}


# --------------------------------------------------
# Classify reference entries
# --------------------------------------------------

classified = []

counts = defaultdict(int)

for ref in reference_records:

    identity = key_for(
        ref["date"],
        ref["title"]
    )

    if identity in history_keys:
        status = (
            "already_in_history"
        )

    elif identity in recovered_keys:
        status = (
            "recovered_and_validated"
        )

    else:
        status = (
            "still_unresolved"
        )

    counts[status] += 1

    classified.append({
        **ref,
        "status": status,
    })


unresolved = [
    item
    for item in classified
    if item["status"]
    == "still_unresolved"
]


# --------------------------------------------------
# Combined known archive
# --------------------------------------------------

combined = []
seen = set()

for item in (
    history_records
    + recovered_records
):

    identity = key_for(
        item["date"],
        item["title"]
    )

    if identity in seen:
        continue

    seen.add(identity)

    combined.append(
        item
    )


combined.sort(
    key=lambda item:
        (
            item["date"],
            normalize_title(
                item["title"]
            )
        )
)


# --------------------------------------------------
# Duplicate checks
# --------------------------------------------------

by_date = defaultdict(list)
by_title = defaultdict(list)

for item in combined:

    by_date[
        item["date"]
    ].append(
        item["title"]
    )

    by_title[
        normalize_title(
            item["title"]
        )
    ].append(
        item["date"]
    )


same_date_multiple = []

for date, titles in by_date.items():

    unique_titles = sorted(
        set(titles)
    )

    if len(unique_titles) > 1:
        same_date_multiple.append({
            "date": date,
            "titles": unique_titles,
        })


repeated_titles = []

for title, dates in by_title.items():

    unique_dates = sorted(
        set(dates)
    )

    if title and len(
        unique_dates
    ) > 1:

        repeated_titles.append({
            "normalized_title":
                title,

            "dates":
                unique_dates,
        })


# --------------------------------------------------
# Chronological gap audit
#
# These are only FLAGS.
# A gap does not prove an episode is missing.
# --------------------------------------------------

unique_dates = sorted(
    set(
        item["date"]
        for item in combined
    )
)

large_gaps = []

for previous, current in zip(
    unique_dates,
    unique_dates[1:]
):

    previous_dt = datetime.strptime(
        previous,
        "%Y-%m-%d"
    )

    current_dt = datetime.strptime(
        current,
        "%Y-%m-%d"
    )

    difference = (
        current_dt
        - previous_dt
    ).days

    if difference >= 5:
        large_gaps.append({
            "after": previous,
            "before": current,
            "calendar_days":
                difference,
        })


# --------------------------------------------------
# Final report
# --------------------------------------------------

report = {
    "audit_version": 1,

    "purpose":
        "Non-destructive completeness audit before rebuilding Indicator history",

    "source_counts": {
        "history_episode_count":
            len(history_episodes),

        "reference_episode_count":
            len(reference_records),

        "standard_validated_recovery_count":
            len(validated_records),

        "special_recovery_count":
            len(special_recoveries),

        "total_validated_recovery_count":
            len(recovered_records),

        "combined_unique_episode_count":
            len(combined),
    },

    "reference_accounting": {
        "already_in_history":
            counts[
                "already_in_history"
            ],

        "recovered_and_validated":
            counts[
                "recovered_and_validated"
            ],

        "still_unresolved":
            counts[
                "still_unresolved"
            ],

        "accounted_for_total":
            (
                counts[
                    "already_in_history"
                ]
                +
                counts[
                    "recovered_and_validated"
                ]
            ),
    },

    "special_recoveries":
        special_recoveries,

    "unresolved_reference_count":
        len(unresolved),

    "unresolved_reference_episodes":
        unresolved,

    "duplicate_and_gap_checks": {
        "dates_with_multiple_titles_count":
            len(
                same_date_multiple
            ),

        "repeated_normalized_titles_count":
            len(
                repeated_titles
            ),

        "large_gap_count":
            len(
                large_gaps
            ),

        "dates_with_multiple_titles":
            same_date_multiple,

        "repeated_normalized_titles":
            repeated_titles,

        "large_gaps":
            large_gaps,
    },

    "reference_classification":
        classified,
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
    "INDICATOR COMPLETENESS AUDIT"
)

print(
    "================================"
)

print(
    "Existing history:",
    report[
        "source_counts"
    ][
        "history_episode_count"
    ]
)

print(
    "Reference episodes:",
    report[
        "source_counts"
    ][
        "reference_episode_count"
    ]
)

print(
    "Validated recoveries:",
    report[
        "source_counts"
    ][
        "total_validated_recovery_count"
    ]
)

print()
print(
    "Already in history:",
    report[
        "reference_accounting"
    ][
        "already_in_history"
    ]
)

print(
    "Recovered:",
    report[
        "reference_accounting"
    ][
        "recovered_and_validated"
    ]
)

print(
    "STILL UNRESOLVED:",
    report[
        "reference_accounting"
    ][
        "still_unresolved"
    ]
)

print()
print(
    "Combined unique episodes:",
    report[
        "source_counts"
    ][
        "combined_unique_episode_count"
    ]
)

print(
    "Large chronological gaps:",
    report[
        "duplicate_and_gap_checks"
    ][
        "large_gap_count"
    ]
)

print()
print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
