#!/usr/bin/env python3
from pathlib import Path

import json
import time
from collections import Counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]



INPUT_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_recovery.json")
OUTPUT_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_validation.json")
TIMEOUT = 30
REQUEST_DELAY = 0.15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRAudioValidator/1.0)"
    )
}


def request_audio(url):
    """
    Request only a tiny byte range when possible.
    We do not need to download the whole episode.
    """

    headers = dict(HEADERS)
    headers["Range"] = "bytes=0-4095"

    request = Request(
        url,
        headers=headers
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        final_url = response.geturl()

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        content_length = response.headers.get(
            "Content-Length"
        )

        status = getattr(
            response,
            "status",
            None
        )

        sample = response.read(4096)

    return {
        "status_code": status,
        "final_url": final_url,
        "content_type": content_type,
        "content_length": content_length,
        "sample_size": len(sample),
    }


def looks_like_audio_content_type(value):
    if not value:
        return False

    value = value.lower()

    return (
        value.startswith("audio/")
        or "mpeg" in value
        or "mp3" in value
        or "aac" in value
        or "m4a" in value
        or "octet-stream" in value
    )


def looks_like_station_stream(url):
    if not url:
        return False

    u = url.lower()

    return (
        "livestream-redirect" in u
        or "streamtheworld.com" in u
    )


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    recovery = json.load(file)


source_results = recovery.get(
    "results",
    []
)


targets = [
    item
    for item in source_results
    if item.get("best_audio")
]


validated = []
needs_review = []
failed = []

final_url_counts = Counter()


for number, item in enumerate(
    targets,
    start=1
):

    audio = item.get(
        "best_audio",
        {}
    )

    audio_url = audio.get("url")

    print()
    print(
        f"[{number}/{len(targets)}] "
        f"{item.get('reference_date')} - "
        f"{item.get('reference_title')}"
    )

    result = {
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
            audio_url,

        "audio_type":
            audio.get("type"),

        "validation_status":
            None,
    }

    if not audio_url:

        result[
            "validation_status"
        ] = "missing_audio_url"

        failed.append(result)
        continue

    try:

        response = request_audio(
            audio_url
        )

    except HTTPError as exc:

        result[
            "validation_status"
        ] = f"http_{exc.code}"

        result["error"] = str(exc)

        failed.append(result)
        continue

    except (
        URLError,
        TimeoutError
    ) as exc:

        result[
            "validation_status"
        ] = "request_error"

        result["error"] = str(exc)

        failed.append(result)
        continue

    except Exception as exc:

        result[
            "validation_status"
        ] = "error"

        result["error"] = str(exc)

        failed.append(result)
        continue

    result.update(response)

    final_url = response.get(
        "final_url"
    )

    if final_url:
        final_url_counts[
            final_url
        ] += 1

    is_audio = (
        looks_like_audio_content_type(
            response.get(
                "content_type"
            )
        )
    )

    station_stream = (
        looks_like_station_stream(
            final_url
        )
        or looks_like_station_stream(
            audio_url
        )
    )

    successful_status = (
        response.get(
            "status_code"
        )
        in {
            200,
            206,
            None,
        }
    )

    if (
        successful_status
        and is_audio
        and not station_stream
    ):

        result[
            "validation_status"
        ] = "validated_audio"

        validated.append(result)

        print(
            "  VALID:",
            response.get(
                "content_type"
            )
        )

    elif (
        successful_status
        and not station_stream
    ):

        result[
            "validation_status"
        ] = "needs_review"

        result[
            "review_reason"
        ] = "non_audio_content_type"

        needs_review.append(
            result
        )

        print(
            "  REVIEW:",
            response.get(
                "content_type"
            )
        )

    else:

        result[
            "validation_status"
        ] = "failed"

        if station_stream:
            result[
                "failure_reason"
            ] = "station_stream"

        else:
            result[
                "failure_reason"
            ] = "bad_response"

        failed.append(result)

    time.sleep(
        REQUEST_DELAY
    )


duplicate_final_urls = {
    url: count
    for url, count
    in final_url_counts.items()
    if count > 1
}


# Add duplicate-final-URL flags after all requests are known.
for collection in [
    validated,
    needs_review,
    failed,
]:

    for result in collection:

        final_url = result.get(
            "final_url"
        )

        result[
            "duplicate_final_url"
        ] = (
            bool(final_url)
            and final_url_counts[
                final_url
            ] > 1
        )


summary = {
    "input_audio_candidate_count":
        len(targets),

    "validated_audio_count":
        len(validated),

    "needs_review_count":
        len(needs_review),

    "failed_count":
        len(failed),

    "unique_final_audio_url_count":
        len(final_url_counts),

    "duplicate_final_audio_url_count":
        len(duplicate_final_urls),
}


report = {
    "method":
        "npr-audio-http-validation",

    "validation_policy": {
        "validated_requires": [
            "audio URL responds successfully",
            "response Content-Type is audio-like",
            "URL is not a known station livestream"
        ],
        "note": (
            "Only a small byte range is requested. "
            "The complete episode is not downloaded."
        ),
    },

    "summary":
        summary,

    "duplicate_final_audio_urls":
        duplicate_final_urls,

    "validated_audio":
        validated,

    "needs_review":
        needs_review,

    "failed":
        failed,
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
    "NPR audio validation complete"
)

for key, value in summary.items():
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
