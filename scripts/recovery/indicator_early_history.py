#!/usr/bin/env python3
from pathlib import Path

import json
import re
import unicodedata
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup
REPO_ROOT = Path(__file__).resolve().parents[2]



HISTORY_FILE = str(REPO_ROOT / "indicator_history.json")
OUTPUT_FILE = str(REPO_ROOT / "indicator_early_audit.json")
REFERENCE_URLS = {
    2018: (
        "https://thetvdb.com/series/"
        "the-indicator-from-planet-money-podcast/"
        "seasons/official/2018"
    ),
    2019: (
        "https://thetvdb.com/series/"
        "the-indicator-from-planet-money-podcast/"
        "seasons/official/2019"
    ),
}

# We only need the period our NPR crawler failed to recover.
EARLY_START = date(2018, 3, 1)
EARLY_END = date(2019, 4, 30)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; theindicator-rss-audit/1.0)"
    )
}


def normalize_title(value):
    if not value:
        return ""

    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()

    # Normalize common punctuation differences.
    value = value.replace("&", " and ")
    value = value.replace("’", "'")
    value = value.replace("‘", "'")

    value = re.sub(r"\(rebroadcast\)", "", value)
    value = re.sub(r"\bupdated\b", "", value)

    # Keep only letters/numbers.
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


def parse_history_date(value):
    if not value:
        return None

    try:
        # Handles values such as:
        # 2019-04-30T18:42:29-04:00
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).date()

    except Exception:
        return None


def load_npr_history():
    with open(
        HISTORY_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        data = json.load(file)

    episodes = data.get("episodes", [])

    results = []

    for episode in episodes:
        episode_date = parse_history_date(
            episode.get("date")
        )

        if not episode_date:
            continue

        results.append({
            "title": episode.get("title"),
            "normalized_title": normalize_title(
                episode.get("title")
            ),
            "date": episode_date,
            "npr_url": episode.get("npr_url"),
            "story_id": episode.get("story_id"),
            "audio_id": episode.get("audio_id"),
        })

    return results


def download_reference(year):
    url = REFERENCE_URLS[year]

    print(f"Downloading TheTVDB {year} list...")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    return response.text


def parse_reference_page(html, year):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    episodes = []

    # TheTVDB displays each episode in a table row.
    for row in soup.find_all("tr"):
        text = " ".join(
            row.get_text(" ", strip=True).split()
        )

        episode_match = re.search(
            rf"S{year}E(\d+)",
            text
        )

        if not episode_match:
            continue

        # Find a date such as "March 12, 2018".
        date_match = re.search(
            r"(January|February|March|April|May|June|"
            r"July|August|September|October|November|December)"
            r"\s+\d{1,2},\s+\d{4}",
            text,
        )

        if not date_match:
            continue

        try:
            aired = datetime.strptime(
                date_match.group(0),
                "%B %d, %Y"
            ).date()

        except ValueError:
            continue

        # The title is normally the episode link.
        title = None

        for link in row.find_all("a"):
            candidate = " ".join(
                link.get_text(
                    " ",
                    strip=True
                ).split()
            )

            if not candidate:
                continue

            if candidate.startswith(
                f"S{year}E"
            ):
                continue

            title = candidate
            break

        if not title:
            # Fallback: remove episode number/date/runtime.
            title = text
            title = re.sub(
                rf"S{year}E\d+",
                "",
                title
            )
            title = title.replace(
                date_match.group(0),
                ""
            )
            title = re.sub(
                r"\b10\b$",
                "",
                title
            )
            title = " ".join(
                title.split()
            )

        episodes.append({
            "reference_year": year,
            "reference_episode":
                int(episode_match.group(1)),
            "title": title,
            "normalized_title":
                normalize_title(title),
            "date": aired,
        })

    return episodes


def find_match(reference, npr_episodes):
    """
    Matching strategy:

    1. Exact normalized title + exact date
    2. Exact normalized title within 7 days
    3. Exact date with very similar title

    We intentionally avoid aggressive fuzzy matching.
    """

    ref_title = reference[
        "normalized_title"
    ]
    ref_date = reference["date"]

    # Exact title + date.
    for episode in npr_episodes:
        if (
            episode["normalized_title"]
            == ref_title
            and episode["date"]
            == ref_date
        ):
            return episode, "exact-title-date"

    # Same title, nearby date.
    for episode in npr_episodes:
        if (
            episode["normalized_title"]
            == ref_title
            and abs(
                (
                    episode["date"]
                    - ref_date
                ).days
            ) <= 7
        ):
            return episode, "title-near-date"

    # Same date and title words mostly overlap.
    ref_words = set(
        ref_title.split()
    )

    if ref_words:
        for episode in npr_episodes:

            if episode["date"] != ref_date:
                continue

            episode_words = set(
                episode[
                    "normalized_title"
                ].split()
            )

            overlap = (
                len(
                    ref_words
                    & episode_words
                )
                / max(
                    len(ref_words),
                    1
                )
            )

            if overlap >= 0.75:
                return (
                    episode,
                    "date-similar-title"
                )

    return None, None


def main():
    print(
        "Loading NPR historical archive..."
    )

    npr_episodes = load_npr_history()

    print(
        f"NPR archive contains "
        f"{len(npr_episodes)} dated entries."
    )

    reference = []

    for year in [2018, 2019]:
        html = download_reference(year)

        parsed = parse_reference_page(
            html,
            year
        )

        print(
            f"Parsed {len(parsed)} "
            f"TheTVDB entries for {year}."
        )

        reference.extend(parsed)

    # Restrict to the period we're investigating.
    reference = [
        episode
        for episode in reference
        if (
            EARLY_START
            <= episode["date"]
            <= EARLY_END
        )
    ]

    reference.sort(
        key=lambda e: (
            e["date"],
            e["reference_episode"],
        )
    )

    matched = []
    missing = []

    for episode in reference:
        npr_match, match_type = (
            find_match(
                episode,
                npr_episodes
            )
        )

        record = {
            "date":
                episode["date"].isoformat(),
            "title":
                episode["title"],
            "reference_year":
                episode[
                    "reference_year"
                ],
            "reference_episode":
                episode[
                    "reference_episode"
                ],
        }

        if npr_match:
            record["match_type"] = (
                match_type
            )
            record["npr_title"] = (
                npr_match["title"]
            )
            record["npr_url"] = (
                npr_match["npr_url"]
            )

            matched.append(record)

        else:
            missing.append(record)

    # Flag suspicious reference dates.
    anomalies = []

    for episode in reference:
        if (
            episode["reference_year"]
            != episode["date"].year
        ):
            anomalies.append({
                "title":
                    episode["title"],
                "reference_year":
                    episode[
                        "reference_year"
                    ],
                "date":
                    episode[
                        "date"
                    ].isoformat(),
                "reason":
                    "Reference season and "
                    "air-date year disagree",
            })

    output = {
        "audit_period": {
            "start":
                EARLY_START.isoformat(),
            "end":
                EARLY_END.isoformat(),
        },
        "npr_archive_total":
            len(npr_episodes),
        "reference_episode_count":
            len(reference),
        "matched_count":
            len(matched),
        "possible_missing_count":
            len(missing),
        "reference_anomaly_count":
            len(anomalies),
        "possible_missing":
            missing,
        "matched":
            matched,
        "reference_anomalies":
            anomalies,
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            output,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print(
        "================================"
    )
    print("Early-history audit complete")
    print(
        f"Reference episodes: "
        f"{len(reference)}"
    )
    print(
        f"Already in NPR archive: "
        f"{len(matched)}"
    )
    print(
        f"Possible missing: "
        f"{len(missing)}"
    )
    print(
        f"Reference anomalies: "
        f"{len(anomalies)}"
    )
    print(
        f"Saved to {OUTPUT_FILE}"
    )
    print(
        "================================"
    )


if __name__ == "__main__":
    main()
