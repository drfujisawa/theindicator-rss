#!/usr/bin/env python3

import json
import re
import urllib.request
from html import unescape

from bs4 import BeautifulSoup


NPR_PAGE = "https://www.npr.org/podcasts/510325/the-indicator-from-planet-money"
OUTPUT_FILE = "history_test.json"
MAX_EPISODES = 10


def download_page():
    print("Downloading NPR Indicator page...")

    request = urllib.request.Request(
        NPR_PAGE,
        headers={
            "User-Agent": "Mozilla/5.0 theindicator-rss-history-test"
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def clean_text(text):
    if not text:
        return None

    return " ".join(unescape(text).split())


def find_description(heading):
    """
    NPR normally puts the episode description near the episode heading.
    Search nearby elements for useful paragraph text.
    """

    for element in heading.find_all_next(limit=15):
        if element.name == "p":
            text = clean_text(element.get_text(" ", strip=True))

            if text and len(text) > 30:
                return text

        # Stop if we've reached the next episode heading.
        if element is not heading and element.name == "h2":
            break

    return None


def find_date(heading):
    """
    Look backwards from the episode title for NPR's date heading.
    """

    date_pattern = re.compile(
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+"
        r"\d{1,2},\s+\d{4}"
    )

    for element in heading.find_all_previous(limit=12):
        text = clean_text(element.get_text(" ", strip=True))

        if text:
            match = date_pattern.search(text)

            if match:
                return match.group(0)

    return None


def find_player_ids(heading):
    """
    Find the NPR embedded-player URL belonging to this episode.

    Example:
    https://www.npr.org/player/embed/676897742/676898271
    """

    for element in heading.find_all_next(limit=25):
        if element is not heading and element.name == "h2":
            break

        # iframe src
        if element.name == "iframe":
            src = element.get("src", "")

            match = re.search(
                r"/player/embed/([^/\"']+)/([^/?\"']+)",
                src
            )

            if match:
                return {
                    "story_id": match.group(1),
                    "audio_id": match.group(2),
                    "player_url": src,
                }

        # Sometimes NPR HTML contains the player URL as plain text/data.
        html = str(element)

        match = re.search(
            r"https://www\.npr\.org/player/embed/"
            r"([^/\"']+)/([^/?\"']+)",
            html
        )

        if match:
            return {
                "story_id": match.group(1),
                "audio_id": match.group(2),
                "player_url": (
                    "https://www.npr.org/player/embed/"
                    f"{match.group(1)}/{match.group(2)}"
                ),
            }

    return None


def find_episode_url(heading):
    """
    Try to obtain NPR's actual story URL from the episode title.
    """

    link = heading.find("a", href=True)

    if link:
        href = link["href"]

        if href.startswith("https://www.npr.org/"):
            return href

        if href.startswith("/"):
            return "https://www.npr.org" + href

    return None


def find_episodes(html):
    soup = BeautifulSoup(html, "html.parser")

    episodes = []

    for heading in soup.find_all("h2"):
        title = clean_text(heading.get_text(" ", strip=True))

        if not title:
            continue

        player = find_player_ids(heading)

        # If there is no NPR audio player nearby,
        # this probably isn't an episode heading.
        if not player:
            continue

        episode = {
            "title": title,
            "date": find_date(heading),
            "npr_url": find_episode_url(heading),
            "story_id": player["story_id"],
            "audio_id": player["audio_id"],
            "player_url": player["player_url"],
            "description": find_description(heading),
        }

        # Avoid accidentally recording the same episode twice.
        if not any(
            existing["story_id"] == episode["story_id"]
            for existing in episodes
        ):
            episodes.append(episode)

        if len(episodes) >= MAX_EPISODES:
            break

    return episodes


def main():
    html = download_page()

    episodes = find_episodes(html)

    print()
    print(f"Found {len(episodes)} episode(s).")
    print()

    for number, episode in enumerate(episodes, start=1):
        print(f"{number}. {episode['title']}")
        print(f"   Date: {episode['date']}")
        print(f"   Story ID: {episode['story_id']}")
        print(f"   Audio ID: {episode['audio_id']}")
        print(f"   URL: {episode['npr_url']}")
        print()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            episodes,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(f"Saved test results to {OUTPUT_FILE}")

    if not episodes:
        raise RuntimeError(
            "No episodes were discovered. "
            "NPR's HTML structure may have changed."
        )


if __name__ == "__main__":
    main()
