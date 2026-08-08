import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

RECOVERED_FILE = Path("indicator_recovered_episodes.json")
HISTORY_FILE = Path("indicator_history.json")
OUTPUT_FILE = Path("indicator_recovery_validation.json")


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_title(title):
    if not title:
        return ""

    return " ".join(
        "".join(
            c.lower() if c.isalnum() else " "
            for c in title
        ).split()
    )


def classify_media_url(url):
    if not url:
        return "unknown"

    u = url.lower()

    if any(
        bad in u
        for bad in [
            "/assets/img/",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".svg",
        ]
    ):
        return "image"

    if any(
        good in u
        for good in [
            ".mp3",
            ".m4a",
            ".aac",
        ]
    ):
        return "direct_audio"

    if "playerservices.streamtheworld.com" in u:
        return "shared_player_service"

    if any(
        word in u
        for word in [
            "streamtheworld",
            "triton",
            "playerservices",
            "/stream/",
        ]
    ):
        return "stream_service"

    if "audio" in u:
        return "possible_audio"

    return "other"


def get_episode_specific_audio(urls):
    results = []

    for url in urls:
        kind = classify_media_url(url)

        if kind in {
            "direct_audio",
            "possible_audio",
        }:
            results.append(url)

    return results


recovered_data = load_json(RECOVERED_FILE)
history_data = load_json(HISTORY_FILE)

recovered = recovered_data.get("recovered", [])
failed = recovered_data.get("failed", [])
history = history_data.get("episodes", [])


history_urls = {
    ep.get("npr_url")
    for ep in history
    if ep.get("npr_url")
}

history_story_ids = {
    str(ep.get("story_id"))
    for ep in history
    if ep.get("story_id")
}

history_audio_ids = {
    str(ep.get("audio_id"))
    for ep in history
    if ep.get("audio_id")
}


source_counts = Counter(
    ep.get("source_url")
    for ep in recovered
    if ep.get("source_url")
)


episode_audio_counter = Counter()

episode_specific_audio = {}

for index, ep in enumerate(recovered):
    urls = ep.get("all_audio_urls", [])

    specific = get_episode_specific_audio(urls)

    episode_specific_audio[index] = specific

    for url in specific:
        episode_audio_counter[url] += 1


clean_candidates = []
needs_review = []

issue_type_counts = Counter()


for index, ep in enumerate(recovered):
    issues = []

    reference_title = ep.get("reference_title", "")
    source_title = ep.get("source_title", "")
    title_score = ep.get("title_score")

    ref_norm = normalize_title(reference_title)
    source_norm = normalize_title(source_title)

    source_url = ep.get("source_url")

    all_media = ep.get("all_audio_urls", [])

    classified_media = [
        {
            "url": url,
            "type": classify_media_url(url),
        }
        for url in all_media
    ]

    specific_audio = episode_specific_audio.get(
        index,
        []
    )

    shared_services = [
        item["url"]
        for item in classified_media
        if item["type"] in {
            "shared_player_service",
            "stream_service",
        }
    ]

    images = [
        item["url"]
        for item in classified_media
        if item["type"] == "image"
    ]

    possible_ids = [
        str(x)
        for x in ep.get("possible_ids", [])
    ]

    if title_score is None:
        issues.append("missing_title_score")

    elif title_score < 0.85:
        issues.append(
            f"weak_title_score:{title_score}"
        )

    if ref_norm and source_norm:
        if (
            ref_norm != source_norm
            and (
                title_score is None
                or title_score < 0.95
            )
        ):
            issues.append("title_mismatch")

    if source_url and source_counts[source_url] > 1:
        issues.append("duplicate_source_url")

    if source_url in history_urls:
        issues.append("source_already_in_history")

    duplicate_specific_audio = [
        url
        for url in specific_audio
        if episode_audio_counter[url] > 1
    ]

    if duplicate_specific_audio:
        issues.append(
            "duplicate_episode_specific_audio"
        )

    story_id_collisions = [
        value
        for value in possible_ids
        if value in history_story_ids
    ]

    audio_id_collisions = [
        value
        for value in possible_ids
        if value in history_audio_ids
    ]

    if story_id_collisions:
        issues.append(
            "possible_story_id_collision"
        )

    if audio_id_collisions:
        issues.append(
            "possible_audio_id_collision"
        )

    if not specific_audio:
        if shared_services:
            issues.append(
                "shared_player_only"
            )
        else:
            issues.append(
                "no_episode_specific_audio"
            )

    result = {
        "reference_date":
            ep.get("reference_date"),

        "reference_title":
            reference_title,

        "reference_year":
            ep.get("reference_year"),

        "reference_episode":
            ep.get("reference_episode"),

        "source_url":
            source_url,

        "source_domain":
            ep.get("source_domain"),

        "source_title":
            source_title,

        "title_score":
            title_score,

        "episode_specific_audio_urls":
            specific_audio,

        "shared_player_urls":
            shared_services,

        "image_urls":
            images,

        "possible_ids":
            possible_ids,

        "issues":
            issues,
    }

    if issues:
        needs_review.append(result)

        for issue in issues:
            issue_type_counts[
                issue.split(":")[0]
            ] += 1

    else:
        clean_candidates.append(result)


failure_status_counts = Counter()

for ep in failed:
    status = (
        ep.get("status")
        or ep.get("reason")
        or "unknown"
    )

    failure_status_counts[
        str(status)
    ] += 1


report = {
    "validation_version": 2,

    "source_file":
        str(RECOVERED_FILE),

    "history_file":
        str(HISTORY_FILE),

    "summary": {
        "recovered_input_count":
            len(recovered),

        "failed_input_count":
            len(failed),

        "history_episode_count":
            len(history),

        "clean_candidate_count":
            len(clean_candidates),

        "needs_review_count":
            len(needs_review),

        "duplicate_source_url_count":
            sum(
                1
                for count
                in source_counts.values()
                if count > 1
            ),

        "duplicate_episode_specific_audio_count":
            sum(
                1
                for count
                in episode_audio_counter.values()
                if count > 1
            ),
    },

    "issue_type_counts":
        dict(
            sorted(
                issue_type_counts.items()
            )
        ),

    "failure_status_counts":
        dict(
            sorted(
                failure_status_counts.items()
            )
        ),

    "clean_candidates":
        clean_candidates,

    "needs_review":
        needs_review,

    "failed_recoveries":
        failed,
}


with OUTPUT_FILE.open(
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        report,
        f,
        indent=2,
        ensure_ascii=False,
    )


print("Validation complete")
print("-------------------")

print(
    "Recovered input:",
    len(recovered)
)

print(
    "Clean candidates:",
    len(clean_candidates)
)

print(
    "Needs review:",
    len(needs_review)
)

print(
    "Failed recoveries:",
    len(failed)
)

print(
    "Report:",
    OUTPUT_FILE
)
