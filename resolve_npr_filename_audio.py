#!/usr/bin/env python3

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


INPUT_FILE = "indicator_npr_player_resolution.json"
OUTPUT_FILE = "indicator_npr_filename_resolution.json"

TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRFilenameResolver/1.0)"
    )
}


def clean_id(value):
    if value is None:
        return None

    match = re.search(r"\d{5,}", str(value))

    return match.group(0) if match else None


def normalize_slug(title):
    if not title:
        return ""

    value = title.lower()

    value = value.replace("&", " and ")
    value = value.replace("’", "")
    value = value.replace("'", "")

    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)

    return value.strip("_")


def slug_variants(title):
    base = normalize_slug(title)

    variants = [
        base,
        base.replace("the_", "", 1)
        if base.startswith("the_")
        else base,
    ]

    words = base.split("_")

    if len(words) > 1:
        variants.append(
            "_".join(words[:2])
        )

        variants.append(
            "_".join(words[-2:])
        )

    # A few common NPR filename simplifications.
    substitutions = {
        "proficiency": "profits",
        "paranormal_proficiency": "paranormal_profits",
        "the_traffic_tariff": "traffic_tariff",
    }

    if base in substitutions:
        variants.append(
            substitutions[base]
        )

    output = []

    for value in variants:
        value = value.strip("_")

        if value and value not in output:
            output.append(value)

    return output


def probe(url):
    request = Request(
        url,
        headers={
            **HEADERS,
            "Range": "bytes=0-4095",
        },
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        status = getattr(
            response,
            "status",
            None
        )

        final_url = response.geturl()

        content_type = response.headers.get(
            "Content-Type",
            ""
        )

        sample = response.read(4096)

    return {
        "status_code": status,
        "final_url": final_url,
        "content_type": content_type,
        "sample_size": len(sample),
    }


def is_audio(content_type):
    if not content_type:
        return False

    value = content_type.lower()

    return (
        value.startswith("audio/")
        or "mpeg" in value
        or "mp3" in value
        or "aac" in value
        or "m4a" in value
    )


with open(
    INPUT_FILE,
    "r",
    encoding="utf-8"
) as file:
    data = json.load(file)


results = []


for item in data.get("results", []):
    title = item.get("reference_title")
    date = item.get("reference_date")

    if not title or not date:
        continue

    year, month, day = date.split("-")
    mmdd = f"{month}{day}"

    embed_players = item.get(
        "embed_players",
        []
    )

    audio_ids = []

    for player in embed_players:
        audio_id = clean_id(
            player.get("audio_id")
        )

        if (
            audio_id
            and audio_id not in audio_ids
        ):
            audio_ids.append(audio_id)

    result = {
        "reference_date": date,
        "reference_title": title,
        "npr_story_id":
            item.get("npr_story_id"),
        "audio_ids": audio_ids,
        "slug_variants":
            slug_variants(title),
        "status": "not_resolved",
        "attempts": [],
    }

    resolved = None

    for audio_id in audio_ids:
        for slug in slug_variants(title):

            candidate = (
                "https://ondemand.npr.org/"
                "anon.npr-mp3/npr/indicator/"
                f"{year}/{month}/"
                f"{audio_id}_indicator_"
                f"{mmdd}_{slug}_final.mp3"
            )

            attempt = {
                "candidate_url": candidate,
            }

            try:
                response = probe(candidate)

                attempt.update(response)

                attempt["is_audio"] = (
                    is_audio(
                        response.get(
                            "content_type"
                        )
                    )
                )

                if attempt["is_audio"]:
                    resolved = attempt
                    result["status"] = "resolved"
                    result["best_audio"] = attempt

                    result[
                        "resolved_slug"
                    ] = slug

                    break

            except HTTPError as exc:
                attempt["status"] = (
                    f"http_{exc.code}"
                )

            except (
                URLError,
                TimeoutError,
            ) as exc:
                attempt["status"] = (
                    "request_error"
                )
                attempt["error"] = str(exc)

            except Exception as exc:
                attempt["status"] = "error"
                attempt["error"] = str(exc)

            result["attempts"].append(
                attempt
            )

        if resolved:
            break

    results.append(result)


report = {
    "method":
        "targeted-npr-indicator-filename-resolution",

    "input_count":
        len(results),

    "resolved_count":
        sum(
            1
            for item in results
            if item.get("status")
            == "resolved"
        ),

    "unresolved_count":
        sum(
            1
            for item in results
            if item.get("status")
            != "resolved"
        ),

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
print("==============================")
print("NPR filename resolution complete")
print("Input:", report["input_count"])
print("Resolved:", report["resolved_count"])
print("Unresolved:", report["unresolved_count"])
print("Saved:", OUTPUT_FILE)
print("==============================")
