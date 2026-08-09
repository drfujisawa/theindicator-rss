#!/usr/bin/env python3

import json
import re
import time
from urllib.parse import quote
from urllib.request import Request, urlopen


OUTPUT_FILE = "indicator_traffic_tariff_wayback_mp3.json"

TARGET_DATE = "2019-04-22"
TARGET_TITLE = "The Traffic Tariff"

WAYBACK_PATTERN = (
    "https://ondemand.npr.org/"
    "anon.npr-mp3/npr/indicator/"
    "2019/04/20190422_indicator_*"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorTrafficTariffProbe/1.0)"
    )
}

TIMEOUT = 45
RETRIES = 4


def fetch(url):
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            request = Request(
                url,
                headers=HEADERS
            )

            with urlopen(
                request,
                timeout=TIMEOUT
            ) as response:
                return {
                    "final_url": response.geturl(),
                    "status_code": getattr(
                        response,
                        "status",
                        None
                    ),
                    "content_type": response.headers.get(
                        "Content-Type",
                        ""
                    ),
                    "text": response.read().decode(
                        "utf-8",
                        errors="replace"
                    ),
                }

        except Exception as exc:
            last_error = exc

            if attempt < RETRIES:
                time.sleep(
                    attempt * 4
                )

    raise last_error


def cdx_url():
    return (
        "https://web.archive.org/cdx/search/cdx"
        "?url="
        + quote(
            WAYBACK_PATTERN,
            safe=""
        )
        + "&output=json"
        + "&filter=statuscode:200"
        + "&collapse=urlkey"
        + "&fl=timestamp,original,statuscode,mimetype,digest"
        + "&limit=200"
    )


def parse_cdx(text):
    data = json.loads(text)

    if (
        not isinstance(data, list)
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
        for row in data[1:]
    ]


def normalize_filename(url):
    if not url:
        return ""

    filename = (
        url.split("?")[0]
        .rstrip("/")
        .split("/")[-1]
    )

    return filename.lower()


def score_candidate(url):
    filename = normalize_filename(
        url
    )

    score = 0
    reasons = []

    if "20190422_indicator_" in filename:
        score += 2
        reasons.append(
            "correct_date_prefix"
        )

    if "traffic" in filename:
        score += 3
        reasons.append(
            "contains_traffic"
        )

    if "tariff" in filename:
        score += 3
        reasons.append(
            "contains_tariff"
        )

    if "trade" in filename:
        score += 1
        reasons.append(
            "contains_trade"
        )

    if "tax" in filename:
        score += 1
        reasons.append(
            "contains_tax"
        )

    return {
        "score": score,
        "reasons": reasons,
        "filename": filename,
    }


report = {
    "method":
        "wayback-indicator-date-wildcard-mp3-search",

    "target_date":
        TARGET_DATE,

    "target_title":
        TARGET_TITLE,

    "wayback_pattern":
        WAYBACK_PATTERN,

    "cdx_status":
        None,

    "candidate_count":
        0,

    "candidates":
        [],
}


print(
    "Searching Wayback for:"
)
print(
    WAYBACK_PATTERN
)


try:
    response = fetch(
        cdx_url()
    )

    rows = parse_cdx(
        response["text"]
    )

    report[
        "cdx_status"
    ] = "fetched"

except Exception as exc:
    report[
        "cdx_status"
    ] = "error"

    report[
        "cdx_error"
    ] = str(exc)

    rows = []


for row in rows:
    original = row.get(
        "original"
    )

    if not original:
        continue

    scored = score_candidate(
        original
    )

    report[
        "candidates"
    ].append({
        "timestamp":
            row.get(
                "timestamp"
            ),

        "original":
            original,

        "statuscode":
            row.get(
                "statuscode"
            ),

        "mimetype":
            row.get(
                "mimetype"
            ),

        "score":
            scored[
                "score"
            ],

        "reasons":
            scored[
                "reasons"
            ],

        "filename":
            scored[
                "filename"
            ],
    })


report[
    "candidates"
].sort(
    key=lambda item: (
        item[
            "score"
        ],
        item.get(
            "timestamp"
        )
        or ""
    ),
    reverse=True
)


report[
    "candidate_count"
] = len(
    report[
        "candidates"
    ]
)


#
# Probe the top few candidate URLs directly.
#
for candidate in report[
    "candidates"
][:10]:

    url = candidate[
        "original"
    ]

    try:
        request = Request(
            url,
            headers={
                **HEADERS,
                "Range":
                    "bytes=0-4095",
            }
        )

        with urlopen(
            request,
            timeout=TIMEOUT
        ) as response:

            candidate[
                "live_status_code"
            ] = getattr(
                response,
                "status",
                None
            )

            candidate[
                "live_final_url"
            ] = response.geturl()

            candidate[
                "live_content_type"
            ] = response.headers.get(
                "Content-Type",
                ""
            )

            candidate[
                "sample_size"
            ] = len(
                response.read(
                    4096
                )
            )

    except Exception as exc:
        candidate[
            "probe_error"
        ] = str(exc)


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
    "Traffic Tariff Wayback MP3 search complete"
)

print(
    "CDX status:",
    report[
        "cdx_status"
    ]
)

print(
    "Candidates:",
    report[
        "candidate_count"
    ]
)

for candidate in report[
    "candidates"
][:10]:

    print()
    print(
        candidate[
            "filename"
        ]
    )

    print(
        "Score:",
        candidate[
            "score"
        ]
    )

    print(
        "Content type:",
        candidate.get(
            "live_content_type"
        )
    )

print()
print(
    "Saved:",
    OUTPUT_FILE
)

print(
    "================================"
)
