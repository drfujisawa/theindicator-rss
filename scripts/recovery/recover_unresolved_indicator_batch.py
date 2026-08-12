#!/usr/bin/env python3
from pathlib import Path

import html
import json
import os
import re
import time
from collections import defaultdict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]



OUTPUT_FILE = str(REPO_ROOT / "indicator_unresolved_batch_recovery.json")
AUDIT_FILE = str(REPO_ROOT / "indicator_completeness_audit.json")
TIMEOUT = 25
RETRIES = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorCompletenessRecovery/1.0)"
    )
}


# --------------------------------------------------
# Basic helpers
# --------------------------------------------------

def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def normalize_title(value):
    if not value:
        return ""

    value = str(value).lower()

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

    return " ".join(
        value.split()
    )


def normalize_date(value):
    if not value:
        return None

    match = re.search(
        r"(\d{4})-(\d{2})-(\d{2})",
        str(value)
    )

    if not match:
        return None

    return "-".join(
        match.groups()
    )


def clean_url(value):
    if not value:
        return None

    value = html.unescape(
        str(value)
    )

    value = value.replace(
        "\\/",
        "/"
    )

    value = value.replace(
        "\\u0026",
        "&"
    )

    value = value.strip(
        "\"' "
    )

    if not value.startswith(
        ("http://", "https://")
    ):
        return None

    return value


def unique(values):
    output = []

    for value in values:
        if value and value not in output:
            output.append(value)

    return output


# --------------------------------------------------
# Network helpers
# --------------------------------------------------

def fetch_bytes(url, range_request=False):
    last_error = None

    headers = dict(HEADERS)

    if range_request:
        headers["Range"] = "bytes=0-4095"

    for attempt in range(
        1,
        RETRIES + 1
    ):
        try:
            request = Request(
                url,
                headers=headers
            )

            with urlopen(
                request,
                timeout=TIMEOUT
            ) as response:

                data = response.read(
                    4096
                    if range_request
                    else 3000000
                )

                return {
                    "status_code":
                        getattr(
                            response,
                            "status",
                            None
                        ),

                    "final_url":
                        response.geturl(),

                    "content_type":
                        response.headers.get(
                            "Content-Type",
                            ""
                        ),

                    "data":
                        data,
                }

        except Exception as exc:
            last_error = exc

            if attempt < RETRIES:
                time.sleep(
                    attempt * 2
                )

    raise last_error


def fetch_text(url):
    response = fetch_bytes(
        url,
        range_request=False
    )

    return {
        **response,

        "text":
            response["data"].decode(
                "utf-8",
                errors="replace"
            )
    }


def is_audio_type(value):
    if not value:
        return False

    value = value.lower()

    return (
        value.startswith("audio/")
        or "audio/mpeg" in value
        or "audio/mp3" in value
        or "audio/aac" in value
        or "audio/mp4" in value
    )


def validate_audio(url):
    try:
        response = fetch_bytes(
            url,
            range_request=True
        )

        content_type = (
            response[
                "content_type"
            ]
            or ""
        )

        return {
            "candidate_url":
                url,

            "status_code":
                response[
                    "status_code"
                ],

            "final_url":
                response[
                    "final_url"
                ],

            "content_type":
                content_type,

            "sample_size":
                len(
                    response["data"]
                ),

            "is_audio":
                is_audio_type(
                    content_type
                ),
        }

    except Exception as exc:
        return {
            "candidate_url":
                url,

            "is_audio":
                False,

            "error":
                str(exc),
        }


# --------------------------------------------------
# HTML audio extraction
# --------------------------------------------------

def extract_urls_from_text(text):
    if not text:
        return []

    text = html.unescape(
        text
    )

    text = text.replace(
        "\\/",
        "/"
    )

    text = text.replace(
        "\\u0026",
        "&"
    )

    patterns = [
        r'https?://[^"\'<>\s\\]+\.mp3'
        r'(?:\?[^"\'<>\s\\]*)?',

        r'https?://ondemand\.npr\.org/'
        r'[^"\'<>\s\\]+',

        r'https?://prfx\.byspotify\.com/'
        r'[^"\'<>\s\\]+',

        r'https?://play\.podtrac\.com/'
        r'[^"\'<>\s\\]+',

        r'https?://[^"\'<>\s\\]+'
        r'(?:audio|download|enclosure)'
        r'[^"\'<>\s\\]*',
    ]

    found = []

    for pattern in patterns:
        found.extend(
            re.findall(
                pattern,
                text,
                re.I
            )
        )

    return unique(
        clean_url(value)
        for value in found
    )


# --------------------------------------------------
# Recursively mine our existing JSON reports
# --------------------------------------------------

USEFUL_URL_KEYS = {
    "npr_url",
    "source_url",
    "affiliate_url",
    "audio_url",
    "final_url",
    "final_audio_url",
    "candidate_url",
    "enclosure_url",
    "player_url",
    "embed_url",
    "final_npr_url",
}


def object_matches_target(
    obj,
    target_date,
    target_title
):
    if not isinstance(
        obj,
        dict
    ):
        return False

    dates = []

    titles = []

    for key, value in obj.items():

        key_lower = str(
            key
        ).lower()

        if (
            "date" in key_lower
            and isinstance(
                value,
                str
            )
        ):
            date = normalize_date(
                value
            )

            if date:
                dates.append(
                    date
                )

        if (
            "title" in key_lower
            and isinstance(
                value,
                str
            )
        ):
            titles.append(
                normalize_title(
                    value
                )
            )

    target_title_norm = (
        normalize_title(
            target_title
        )
    )

    date_match = (
        target_date in dates
        if dates
        else False
    )

    title_match = (
        target_title_norm
        in titles
        if titles
        else False
    )

    # Require title, or date+some title.
    return (
        title_match
        or (
            date_match
            and target_title_norm
            in " ".join(titles)
        )
    )


def collect_matching_objects(
    value,
    target_date,
    target_title,
    path="root"
):
    matches = []

    if isinstance(value, dict):

        if object_matches_target(
            value,
            target_date,
            target_title
        ):
            matches.append({
                "path": path,
                "object": value,
            })

        for key, child in value.items():
            matches.extend(
                collect_matching_objects(
                    child,
                    target_date,
                    target_title,
                    f"{path}.{key}"
                )
            )

    elif isinstance(value, list):

        for index, child in enumerate(
            value
        ):
            matches.extend(
                collect_matching_objects(
                    child,
                    target_date,
                    target_title,
                    f"{path}[{index}]"
                )
            )

    return matches


def extract_evidence(
    filename,
    obj,
    path
):
    urls = []

    ids = {}

    statuses = {}

    for key, value in obj.items():

        lower = str(
            key
        ).lower()

        if isinstance(value, str):

            possible_url = (
                clean_url(value)
            )

            if (
                possible_url
                and (
                    lower in USEFUL_URL_KEYS
                    or "url" in lower
                )
            ):
                urls.append(
                    possible_url
                )

            if (
                lower.endswith("_id")
                or lower in {
                    "story_id",
                    "audio_id",
                    "content_id",
                    "npr_story_id",
                    "player_story_id",
                }
            ):
                ids[key] = value

            if (
                "status" in lower
                or "reason" in lower
            ):
                statuses[key] = value

        elif isinstance(
            value,
            (int, float)
        ):
            if lower.endswith(
                "_id"
            ):
                ids[key] = value

    return {
        "file":
            filename,

        "path":
            path,

        "urls":
            unique(urls),

        "ids":
            ids,

        "statuses":
            statuses,
    }


# --------------------------------------------------
# Wayback helper
# --------------------------------------------------

def wayback_cdx(original_url):
    query = (
        "https://web.archive.org/"
        "cdx/search/cdx"
        "?url="
        + quote(
            original_url,
            safe=""
        )
        + "&output=json"
        + "&filter=statuscode:200"
        + "&collapse=digest"
        + "&fl=timestamp,original"
        + "&limit=10"
    )

    try:
        response = fetch_text(
            query
        )

        data = json.loads(
            response["text"]
        )

        if (
            not isinstance(
                data,
                list
            )
            or len(data) < 2
        ):
            return []

        header = data[0]

        return [
            dict(
                zip(
                    header,
                    row
                )
            )
            for row
            in data[1:]
        ]

    except Exception:
        return []


def probe_wayback_page(url):
    captures = wayback_cdx(
        url
    )

    attempts = []

    for capture in captures[:3]:

        timestamp = capture.get(
            "timestamp"
        )

        original = capture.get(
            "original"
        )

        if not timestamp or not original:
            continue

        archive_url = (
            "https://web.archive.org/web/"
            f"{timestamp}id_/"
            f"{original}"
        )

        try:
            page = fetch_text(
                archive_url
            )

            audio_urls = (
                extract_urls_from_text(
                    page["text"]
                )
            )

            attempts.append({
                "timestamp":
                    timestamp,

                "archive_url":
                    archive_url,

                "audio_candidates":
                    audio_urls,
            })

            if audio_urls:
                break

        except Exception as exc:

            attempts.append({
                "timestamp":
                    timestamp,

                "archive_url":
                    archive_url,

                "error":
                    str(exc),
            })

    return attempts


# --------------------------------------------------
# Load everything
# --------------------------------------------------

audit = load_json(
    AUDIT_FILE
)

if not audit:
    raise RuntimeError(
        "indicator_completeness_audit.json "
        "was not found or could not be read."
    )


unresolved = audit.get(
    "unresolved_reference_episodes",
    []
)


json_files = sorted(
    filename
    for filename
    in os.listdir(".")
    if (
        filename.endswith(
            ".json"
        )
        and filename
        not in {
            OUTPUT_FILE,
        }
    )
)


loaded_files = {}

for filename in json_files:
    data = load_json(
        filename
    )

    if data is not None:
        loaded_files[
            filename
        ] = data


# --------------------------------------------------
# Reference-title occurrence table
# --------------------------------------------------

reference_title_dates = defaultdict(
    list
)

for item in audit.get(
    "reference_classification",
    []
):

    title = normalize_title(
        item.get(
            "title"
        )
    )

    date = normalize_date(
        item.get(
            "date"
        )
    )

    if title and date:
        if date not in (
            reference_title_dates[
                title
            ]
        ):
            reference_title_dates[
                title
            ].append(
                date
            )


# --------------------------------------------------
# Main batch
# --------------------------------------------------

results = []


for index, target in enumerate(
    unresolved,
    start=1
):

    date = normalize_date(
        target.get(
            "date"
        )
    )

    title = target.get(
        "title"
    )

    normalized = normalize_title(
        title
    )

    print()
    print(
        f"[{index}/{len(unresolved)}]",
        date,
        "-",
        title
    )

    result = {
        "date":
            date,

        "title":
            title,

        "reference_year":
            target.get(
                "reference_year"
            ),

        "reference_episode":
            target.get(
                "reference_episode"
            ),

        "status":
            "still_unresolved",

        "duplicate_reference_dates":
            [],

        "prior_evidence":
            [],

        "page_probes":
            [],

        "wayback_probes":
            [],

        "validated_audio":
            [],
    }


    # ----------------------------------------------
    # Duplicate/reference anomaly check
    # ----------------------------------------------

    same_title_dates = sorted(
        reference_title_dates.get(
            normalized,
            []
        )
    )

    other_dates = [
        value
        for value
        in same_title_dates
        if value != date
    ]

    result[
        "duplicate_reference_dates"
    ] = other_dates


    # ----------------------------------------------
    # Mine every prior JSON artifact
    # ----------------------------------------------

    discovered_urls = []

    for filename, data in (
        loaded_files.items()
    ):

        if filename == AUDIT_FILE:
            continue

        matches = (
            collect_matching_objects(
                data,
                date,
                title
            )
        )

        for match in matches:

            evidence = (
                extract_evidence(
                    filename,
                    match["object"],
                    match["path"]
                )
            )

            if (
                evidence["urls"]
                or evidence["ids"]
                or evidence["statuses"]
            ):

                result[
                    "prior_evidence"
                ].append(
                    evidence
                )

                discovered_urls.extend(
                    evidence["urls"]
                )


    discovered_urls = unique(
        discovered_urls
    )


    # ----------------------------------------------
    # First validate any known direct audio URLs
    # ----------------------------------------------

    for url in discovered_urls:

        if any(
            marker in url.lower()
            for marker in [
                ".mp3",
                "ondemand.npr.org",
                "prfx.byspotify.com",
                "play.podtrac.com",
            ]
        ):

            validation = (
                validate_audio(
                    url
                )
            )

            if validation.get(
                "is_audio"
            ):
                result[
                    "validated_audio"
                ].append(
                    validation
                )


    # ----------------------------------------------
    # Fetch known NPR/affiliate/player pages
    # ----------------------------------------------

    page_urls = [
        url
        for url in discovered_urls
        if not any(
            marker in url.lower()
            for marker in [
                ".mp3",
                "ondemand.npr.org",
                "prfx.byspotify.com",
                "play.podtrac.com",
            ]
        )
    ]


    for url in page_urls[:12]:

        probe = {
            "url":
                url,

            "audio_candidates":
                [],
        }

        try:
            page = fetch_text(
                url
            )

            probe[
                "status_code"
            ] = page[
                "status_code"
            ]

            probe[
                "final_url"
            ] = page[
                "final_url"
            ]

            candidates = (
                extract_urls_from_text(
                    page["text"]
                )
            )

            probe[
                "audio_candidates"
            ] = candidates

            for candidate in candidates[:10]:

                validation = (
                    validate_audio(
                        candidate
                    )
                )

                if validation.get(
                    "is_audio"
                ):
                    result[
                        "validated_audio"
                    ].append(
                        validation
                    )

        except Exception as exc:

            probe[
                "error"
            ] = str(exc)

        result[
            "page_probes"
        ].append(
            probe
        )


        # Only Wayback-probe relevant page URLs.
        if any(
            domain in url.lower()
            for domain in [
                "npr.org",
                "wbur.org",
                "wypr.org",
                "wamu.org",
                "kqed.org",
                "kuow.org",
                "mprnews.org",
            ]
        ):

            archive_attempts = (
                probe_wayback_page(
                    url
                )
            )

            if archive_attempts:

                result[
                    "wayback_probes"
                ].append({
                    "original_url":
                        url,

                    "captures":
                        archive_attempts,
                })

                for capture in (
                    archive_attempts
                ):

                    for candidate in (
                        capture.get(
                            "audio_candidates",
                            []
                        )
                    )[:10]:

                        validation = (
                            validate_audio(
                                candidate
                            )
                        )

                        if validation.get(
                            "is_audio"
                        ):
                            result[
                                "validated_audio"
                            ].append(
                                validation
                            )


    # ----------------------------------------------
    # Deduplicate successful audio
    # ----------------------------------------------

    successful = []

    seen_audio = set()

    for item in result[
        "validated_audio"
    ]:

        final_url = (
            item.get(
                "final_url"
            )
            or item.get(
                "candidate_url"
            )
        )

        if (
            final_url
            and final_url
            not in seen_audio
        ):
            seen_audio.add(
                final_url
            )

            successful.append(
                item
            )

    result[
        "validated_audio"
    ] = successful


    # ----------------------------------------------
    # Final classification
    # ----------------------------------------------

    if successful:

        result[
            "status"
        ] = "recovered_and_validated"

        result[
            "best_audio"
        ] = successful[0]

    elif other_dates:

        result[
            "status"
        ] = (
            "possible_duplicate_or_rebroadcast"
        )

    elif result[
        "prior_evidence"
    ]:

        result[
            "status"
        ] = (
            "unresolved_with_prior_evidence"
        )

    else:

        result[
            "status"
        ] = (
            "unresolved_no_prior_evidence"
        )


    print(
        "  ->",
        result["status"]
    )

    results.append(
        result
    )


# --------------------------------------------------
# Summary
# --------------------------------------------------

status_counts = defaultdict(int)

for item in results:
    status_counts[
        item["status"]
    ] += 1


report = {
    "method":
        "batch-recovery-and-triage-of-unresolved-reference-episodes",

    "input_unresolved_count":
        len(unresolved),

    "summary": {
        "recovered_and_validated":
            status_counts[
                "recovered_and_validated"
            ],

        "possible_duplicate_or_rebroadcast":
            status_counts[
                "possible_duplicate_or_rebroadcast"
            ],

        "unresolved_with_prior_evidence":
            status_counts[
                "unresolved_with_prior_evidence"
            ],

        "unresolved_no_prior_evidence":
            status_counts[
                "unresolved_no_prior_evidence"
            ],
    },

    "remaining_not_recovered_count":
        sum(
            1
            for item in results
            if item["status"]
            != "recovered_and_validated"
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
print(
    "================================"
)

print(
    "UNRESOLVED BATCH RECOVERY COMPLETE"
)

print(
    "Input:",
    report[
        "input_unresolved_count"
    ]
)

for key, value in report[
    "summary"
].items():

    print(
        key + ":",
        value
    )

print(
    "Remaining not recovered:",
    report[
        "remaining_not_recovered_count"
    ]
)

print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
