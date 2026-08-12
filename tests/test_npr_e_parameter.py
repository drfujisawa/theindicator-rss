#!/usr/bin/env python3
from pathlib import Path

import json
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[1]



VALIDATION_FILE = str(REPO_ROOT / "indicator_npr_audio_validation.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_npr_e_parameter_test.json")
TARGETS = [
    {
        "title": "Paranormal Profits",
        "audio_id": "662707862",
    },
    {
        "title": "The Traffic Tariff",
        "audio_id": "730102905",
    },
]

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorNPRParameterTest/1.0)"
    ),
    "Range": "bytes=0-4095",
}


def fetch(url):
    request = Request(
        url,
        headers=HEADERS,
    )

    with urlopen(
        request,
        timeout=TIMEOUT
    ) as response:

        sample = response.read(4096)

        return {
            "status_code": getattr(
                response,
                "status",
                None
            ),
            "final_url": response.geturl(),
            "content_type": response.headers.get(
                "Content-Type",
                ""
            ),
            "sample_size": len(sample),
        }


def replace_e(url, new_e):
    parsed = urlparse(url)

    query = parse_qs(
        parsed.query,
        keep_blank_values=True
    )

    query["e"] = [new_e]

    new_query = urlencode(
        query,
        doseq=True
    )

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


with open(
    VALIDATION_FILE,
    "r",
    encoding="utf-8"
) as file:
    data = json.load(file)


validated = data.get(
    "validated_audio",
    []
)

if not validated:
    raise RuntimeError(
        "No validated audio records found."
    )


#
# Use one known-good Indicator redirect URL
# as our control.
#
control = validated[0]

control_url = control.get(
    "audio_url"
)

if not control_url:
    raise RuntimeError(
        "Control record has no audio URL."
    )


print(
    "Control episode:",
    control.get("reference_title")
)

print(
    "Control URL:",
    control_url
)


control_response = fetch(
    control_url
)


tests = []


for target in TARGETS:

    modified_url = replace_e(
        control_url,
        target["audio_id"]
    )

    print()
    print(
        "Testing:",
        target["title"]
    )

    print(
        "e=",
        target["audio_id"]
    )

    response = fetch(
        modified_url
    )

    same_destination = (
        response.get("final_url")
        == control_response.get(
            "final_url"
        )
    )

    tests.append({
        "target_title":
            target["title"],

        "target_audio_id":
            target["audio_id"],

        "modified_url":
            modified_url,

        "response":
            response,

        "same_final_url_as_control":
            same_destination,
    })

    print(
        "Final URL:",
        response.get("final_url")
    )

    print(
        "Same as control:",
        same_destination
    )


report = {
    "method":
        "test-whether-npr-e-parameter-selects-audio",

    "control": {
        "title":
            control.get(
                "reference_title"
            ),

        "audio_url":
            control_url,

        "response":
            control_response,
    },

    "tests":
        tests,

    "interpretation": {
        "if_same_final_url_is_true": (
            "The e parameter is tracking metadata "
            "and does not select the MP3."
        ),

        "if_same_final_url_is_false": (
            "The e parameter may participate in "
            "selecting the episode audio."
        ),
    },
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
print("NPR e-parameter test complete")
print("Saved:", OUTPUT_FILE)
print("==============================")
