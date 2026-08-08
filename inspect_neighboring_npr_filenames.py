#!/usr/bin/env python3

import json
from datetime import datetime


INPUT_FILE = "indicator_npr_audio_validation.json"
OUTPUT_FILE = "indicator_npr_filename_neighbors.json"

TARGET_DATES = [
    "2018-10-31",
    "2019-04-22",
]

WINDOW = 7


def parse_date(value):
    return datetime.strptime(
        value,
        "%Y-%m-%d"
    )


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    data = json.load(file)


validated = data.get(
    "validated_audio",
    []
)


records = []

for item in validated:
    date = item.get(
        "reference_date"
    )

    if not date:
        continue

    records.append({
        "reference_date":
            date,

        "reference_title":
            item.get(
                "reference_title"
            ),

        "npr_story_id":
            item.get(
                "npr_story_id"
            ),

        "audio_url":
            item.get(
                "audio_url"
            ),

        "final_url":
            item.get(
                "final_url"
            ),
    })


records.sort(
    key=lambda item:
        parse_date(
            item["reference_date"]
        )
)


reports = []


for target_date in TARGET_DATES:

    target_dt = parse_date(
        target_date
    )

    nearby = []

    for item in records:

        item_dt = parse_date(
            item["reference_date"]
        )

        distance = abs(
            (
                item_dt
                - target_dt
            ).days
        )

        if distance <= WINDOW:

            final_url = item.get(
                "final_url"
            ) or ""

            filename = (
                final_url.rstrip("/")
                .split("/")[-1]
                if final_url
                else None
            )

            nearby.append({
                "date":
                    item.get(
                        "reference_date"
                    ),

                "day_distance":
                    distance,

                "title":
                    item.get(
                        "reference_title"
                    ),

                "npr_story_id":
                    item.get(
                        "npr_story_id"
                    ),

                "filename":
                    filename,

                "final_url":
                    final_url,
            })

    nearby.sort(
        key=lambda item: (
            item["day_distance"],
            item["date"]
        )
    )

    reports.append({
        "target_date":
            target_date,

        "window_days":
            WINDOW,

        "neighbor_count":
            len(nearby),

        "neighbors":
            nearby,
    })


report = {
    "method":
        "inspect-neighboring-npr-indicator-filenames",

    "targets":
        reports,
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
    "Neighbor filename inspection complete"
)

for item in reports:
    print()
    print(
        "Target:",
        item["target_date"]
    )

    print(
        "Neighbors:",
        item["neighbor_count"]
    )

    for neighbor in item["neighbors"]:
        print(
            neighbor["date"],
            "-",
            neighbor["filename"]
        )

print()
print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
