#!/usr/bin/env python3
from pathlib import Path

import json
import re
from difflib import SequenceMatcher
REPO_ROOT = Path(__file__).resolve().parents[2]



INPUT_FILE = str(REPO_ROOT / "indicator_npr_filename_neighbors.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_filename_slug_analysis.json")
def normalize(value):
    if not value:
        return ""

    value = value.lower()
    value = value.replace("&", " and ")
    value = value.replace("’", "")
    value = value.replace("'", "")

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return " ".join(
        value.split()
    )


def filename_slug(filename):
    if not filename:
        return ""

    value = filename

    if value.endswith(".mp3"):
        value = value[:-4]

    # Remove YYYYMMDD_indicator_
    value = re.sub(
        r"^\d{8}_indicator_",
        "",
        value
    )

    # Remove trailing _final
    value = re.sub(
        r"_final$",
        "",
        value
    )

    return value


def slug_words(value):
    return [
        word
        for word in value.split("_")
        if word
    ]


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    data = json.load(file)


results = []


for target in data.get(
    "targets",
    []
):
    analyzed = []

    for neighbor in target.get(
        "neighbors",
        []
    ):

        title = neighbor.get(
            "title",
            ""
        )

        filename = neighbor.get(
            "filename",
            ""
        )

        slug = filename_slug(
            filename
        )

        title_normalized = normalize(
            title
        )

        slug_normalized = normalize(
            slug.replace("_", " ")
        )

        title_words = set(
            title_normalized.split()
        )

        slug_word_list = slug_words(
            slug
        )

        slug_word_set = set(
            normalize(
                " ".join(
                    slug_word_list
                )
            ).split()
        )

        shared_words = sorted(
            title_words
            & slug_word_set
        )

        extra_slug_words = sorted(
            slug_word_set
            - title_words
        )

        missing_title_words = sorted(
            title_words
            - slug_word_set
        )

        similarity = (
            SequenceMatcher(
                None,
                title_normalized,
                slug_normalized
            ).ratio()
            if (
                title_normalized
                and slug_normalized
            )
            else 0.0
        )

        analyzed.append({
            "date":
                neighbor.get("date"),

            "title":
                title,

            "filename":
                filename,

            "filename_slug":
                slug,

            "title_normalized":
                title_normalized,

            "slug_normalized":
                slug_normalized,

            "similarity":
                round(
                    similarity,
                    3
                ),

            "shared_words":
                shared_words,

            "extra_slug_words":
                extra_slug_words,

            "missing_title_words":
                missing_title_words,
        })

    results.append({
        "target_date":
            target.get(
                "target_date"
            ),

        "neighbors":
            analyzed,
    })


report = {
    "method":
        "compare-indicator-public-titles-to-npr-audio-filenames",

    "targets":
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
    "NPR filename slug analysis complete"
)

for target in results:

    print()
    print(
        "Target:",
        target["target_date"]
    )

    for item in target[
        "neighbors"
    ]:

        print(
            item["date"],
            "|",
            item["title"],
            "=>",
            item["filename_slug"],
            "| score:",
            item["similarity"]
        )

print()
print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
