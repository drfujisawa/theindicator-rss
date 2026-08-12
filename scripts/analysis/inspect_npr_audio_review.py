#!/usr/bin/env python3
from pathlib import Path

import json
REPO_ROOT = Path(__file__).resolve().parents[2]


INPUT_FILE = str(REPO_ROOT / "indicator_npr_audio_validation.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_audio_review.json")
with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    data = json.load(file)


needs_review = data.get(
    "needs_review",
    []
)


report = {
    "review_count": len(needs_review),
    "records": []
}


for item in needs_review:
    report["records"].append({
        "reference_date":
            item.get("reference_date"),

        "reference_title":
            item.get("reference_title"),

        "identity_status":
            item.get("identity_status"),

        "npr_story_id":
            item.get("npr_story_id"),

        "npr_url":
            item.get("npr_url"),

        "audio_url":
            item.get("audio_url"),

        "audio_type":
            item.get("audio_type"),

        "status_code":
            item.get("status_code"),

        "final_url":
            item.get("final_url"),

        "content_type":
            item.get("content_type"),

        "content_length":
            item.get("content_length"),

        "sample_size":
            item.get("sample_size"),

        "review_reason":
            item.get("review_reason"),

        "duplicate_final_url":
            item.get("duplicate_final_url"),
    })


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


print("Audio review extraction complete")
print("Records needing review:", len(needs_review))
print("Saved:", OUTPUT_FILE)
