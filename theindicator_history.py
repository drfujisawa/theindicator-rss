#!/usr/bin/env python3

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup


OUTPUT_FILE = "indicator_history.json"

# Stop once we reach the beginning of The Indicator.
STOP_DATE = datetime(2017, 12, 1, tzinfo=timezone.utc)

# Start just before the oldest episode already in our live feed.
START_DATE = datetime(2025, 6, 9, tzinfo=timezone.utc)

# Safety limit for one GitHub Actions run.
MAX_ARCHIVE_PAGES = 25

# Pause between NPR requests.
REQUEST_DELAY = 1.0

ARCHIVE_STEM = "https://www.npr.org/sections/business/archive"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; theindicator-rss-history/1.0)"
    )
}


def clean_text(text):
    if not text:
        return None

    return " ".join(unescape(text).split())


def download(url):
    print(f"Downloading: {url}")

    request = urllib.request.Request(
        url,
        headers=HEADERS,
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:
        data = response.read()

    print(f"Downloaded {len(data):,} bytes")

    time.sleep(REQUEST_DELAY)

    return data


def normalize_url(url):
    if not url:
        return None

    return urljoin(
        "https://www.npr.org/",
        url,
    )


def parse_datetime(value):
    if not value:
        return None

    try:
        value = value.replace(
            "Z",
            "+00:00"
        )

        result = datetime.fromisoformat(
            value
        )

        if result.tzinfo is None:
            result = result.replace(
                tzinfo=timezone.utc
            )

        return result

    except Exception:
        return None


def load_history():
    if not os.path.exists(OUTPUT_FILE):
        return {
            "next_date": START_DATE.isoformat(),
            "episodes": [],
        }

    with open(
        OUTPUT_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        data = json.load(file)

    return data


def save_history(data):
    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )


def build_archive_url(date):
    return (
        ARCHIVE_STEM
        + date.strftime(
            "?date=%m-%d-%Y"
        )
    )


def find_indicator_links(html):
    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    results = []
    seen = set()

    for article in soup.find_all("article"):

        text = clean_text(
            article.get_text(
                " ",
                strip=True
            )
        )

        if not text:
            continue

        if (
            "The Indicator from Planet Money"
            not in text
            and "The Indicator"
            not in text
        ):
            continue

        candidates = []

        for heading_name in [
            "h1",
            "h2",
            "h3",
            "h4",
        ]:
            heading = article.find(
                heading_name
            )

            if heading:
                link = heading.find(
                    "a",
                    href=True
                )

                if link:
                    candidates.append(
                        link["href"]
                    )

        if not candidates:
            for link in article.find_all(
                "a",
                href=True
            ):
                candidates.append(
                    link["href"]
                )

        for candidate in candidates:

            url = normalize_url(candidate)

            if not url:
                continue

            if "npr.org/" not in url:
                continue

            if "/player/" in url:
                continue

            if "/podcasts/" in url:
                continue

            if "/sections/business" in url:
                continue

            if url in seen:
                continue

            seen.add(url)
            results.append(url)

            break

    return results


def find_player(soup):
    html = str(soup)

    match = re.search(
        r"(?:https?:)?//www\.npr\.org/"
        r"player/embed/"
        r"([^/\"'<>\s]+)/"
        r"([^/?\"'<>\s]+)",
        html,
    )

    if not match:
        match = re.search(
            r"/player/embed/"
            r"([^/\"'<>\s]+)/"
            r"([^/?\"'<>\s]+)",
            html,
        )

    if not match:
        return None

    story_id = match.group(1)
    audio_id = match.group(2)

    return {
        "story_id": story_id,
        "audio_id": audio_id,
        "player_url": (
            "https://www.npr.org/player/embed/"
            f"{story_id}/{audio_id}"
        ),
    }


def find_title(soup):
    heading = soup.find("h1")

    if heading:
        return clean_text(
            heading.get_text(
                " ",
                strip=True
            )
        )

    return None


def find_description(soup):
    meta = soup.find(
        "meta",
        attrs={"name": "description"}
    )

    if meta:
        description = meta.get(
            "content"
        )

        if description:
            return clean_text(
                description
            )

    return None


def find_date(soup):
    time_element = soup.find(
        "time",
        attrs={"datetime": True}
    )

    if time_element:
        value = time_element.get(
            "datetime"
        )

        if value:
            return value

    text = clean_text(
        soup.get_text(
            " ",
            strip=True
        )
    )

    pattern = re.compile(
        r"(January|February|March|April|May|"
        r"June|July|August|September|October|"
        r"November|December)"
        r"\s+\d{1,2},\s+\d{4}"
    )

    match = pattern.search(
        text or ""
    )

    if match:
        try:
            date = datetime.strptime(
                match.group(0),
                "%B %d, %Y"
            )

            return date.replace(
                tzinfo=timezone.utc
            ).isoformat()

        except Exception:
            pass

    return None


def parse_episode_page(url):
    html = download(url)

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    player = find_player(soup)

    if not player:
        print(
            "WARNING: No audio player found."
        )

        return None

    return {
        "title": find_title(soup),
        "date": find_date(soup),
        "npr_url": url,
        "story_id": player["story_id"],
        "audio_id": player["audio_id"],
        "player_url": player["player_url"],
        "description": find_description(
            soup
        ),
    }


def episode_key(episode):
    return (
        episode.get("story_id"),
        episode.get("audio_id"),
    )


def main():

    history = load_history()

    episodes = history.get(
        "episodes",
        []
    )

    known = {
        episode_key(e)
        for e in episodes
    }

    current_date = parse_datetime(
        history.get("next_date")
    )

    if current_date is None:
        current_date = START_DATE

    print()
    print(
        f"Starting history crawl at "
        f"{current_date.date()}"
    )

    print(
        f"Already have "
        f"{len(episodes)} historical episodes."
    )
    print()

    pages_processed = 0

    while (
        current_date > STOP_DATE
        and pages_processed
        < MAX_ARCHIVE_PAGES
    ):

        pages_processed += 1

        print()
        print(
            "================================"
        )
        print(
            f"Archive page "
            f"{pages_processed}/"
            f"{MAX_ARCHIVE_PAGES}"
        )
        print(
            f"Date: {current_date.date()}"
        )
        print(
            "================================"
        )

        archive_url = build_archive_url(
            current_date
        )

        archive_html = download(
            archive_url
        )

        links = find_indicator_links(
            archive_html
        )

        print(
            f"Found {len(links)} "
            "Indicator link(s)."
        )

        oldest_date = None

        for url in links:

            try:
                episode = parse_episode_page(
                    url
                )

            except Exception as error:
                print(
                    f"ERROR reading {url}: "
                    f"{error}"
                )

                continue

            if not episode:
                continue

            key = episode_key(
                episode
            )

            episode_date = parse_datetime(
                episode.get("date")
            )

            if episode_date:
                if (
                    oldest_date is None
                    or episode_date
                    < oldest_date
                ):
                    oldest_date = episode_date

            if key in known:
                print(
                    f"Already saved: "
                    f"{episode['title']}"
                )

                continue

            known.add(key)

            episodes.append(
                episode
            )

            print(
                f"Saved: "
                f"{episode['title']}"
            )

        #
        # Decide which archive date to request next.
        #
        if oldest_date is not None:

            next_date = (
                oldest_date
                - timedelta(days=1)
            )

        else:
            #
            # If a page somehow has no Indicator
            # episodes, jump backward 30 days.
            #
            next_date = (
                current_date
                - timedelta(days=30)
            )

        #
        # Guard against getting stuck on
        # the same archive page forever.
        #
        if next_date >= current_date:
            next_date = (
                current_date
                - timedelta(days=1)
            )

        current_date = next_date

        #
        # Sort newest first.
        #
        episodes.sort(
            key=lambda e: (
                parse_datetime(
                    e.get("date")
                )
                or STOP_DATE
            ),
            reverse=True,
        )

        history = {
            "next_date":
                current_date.isoformat(),
            "episode_count":
                len(episodes),
            "episodes":
                episodes,
        }

        #
        # Save after EVERY archive page.
        #
        save_history(
            history
        )

        print()
        print(
            f"Progress saved."
        )
        print(
            f"Historical episodes: "
            f"{len(episodes)}"
        )
        print(
            f"Next archive date: "
            f"{current_date.date()}"
        )

    print()
    print(
        "================================"
    )
    print(
        "Historical crawl finished "
        "for this run."
    )
    print(
        f"Processed "
        f"{pages_processed} "
        "archive page(s)."
    )
    print(
        f"Saved "
        f"{len(episodes)} "
        "historical episodes."
    )
    print(
        f"Next date: "
        f"{current_date.date()}"
    )
    print(
        "================================"
    )


if __name__ == "__main__":
    main()
