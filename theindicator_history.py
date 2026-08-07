#!/usr/bin/env python3

import json
import re
import urllib.request
from html import unescape

from bs4 import BeautifulSoup


# Start safely with a date BEFORE our existing RSS history.
TEST_DATE = "05-31-2025"

OUTPUT_FILE = "history_test.json"
MAX_EPISODES = 20

ARCHIVE_URLS = [
    f"https://www.npr.org/sections/business/archive?date={TEST_DATE}",
    f"https://partners.npr.org/sections/business/archive?date={TEST_DATE}",
]


def clean_text(text):
    if not text:
        return None

    return " ".join(unescape(text).split())


def download_archive():
    """
    Try NPR's normal site first, then the partners site.
    """

    last_error = None

    for url in ARCHIVE_URLS:
        print(f"Trying: {url}")

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(compatible; theindicator-rss-history/1.0)"
                )
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=30
            ) as response:
                html = response.read()

            print(
                f"Downloaded {len(html):,} bytes "
                f"from {url}"
            )

            return html, url

        except Exception as error:
            print(f"Failed: {error}")
            last_error = error

    raise RuntimeError(
        f"Could not download NPR archive: {last_error}"
    )


def find_date(text):
    if not text:
        return None

    pattern = re.compile(
        r"(January|February|March|April|May|June|July|"
        r"August|September|October|November|December)"
        r"\s+\d{1,2},\s+\d{4}"
    )

    match = pattern.search(text)

    if match:
        return match.group(0)

    return None


def normalize_url(url):
    if not url:
        return None

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/"):
        return "https://www.npr.org" + url

    return url


def get_player_ids(element):
    """
    Find NPR player IDs inside an episode/story block.

    Example:
    /player/embed/1250902337/1269298862
    """

    html = str(element)

    match = re.search(
        r"(?:https?:)?//www\.npr\.org/player/embed/"
        r"([^/\"'<>\s]+)/([^/?\"'<>\s]+)",
        html,
    )

    if not match:
        match = re.search(
            r"/player/embed/"
            r"([^/\"'<>\s]+)/([^/?\"'<>\s]+)",
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


def looks_like_indicator(element):
    """
    Make sure this block belongs to The Indicator,
    not Planet Money or another NPR show.
    """

    text = clean_text(
        element.get_text(" ", strip=True)
    )

    if not text:
        return False

    return (
        "The Indicator from Planet Money" in text
        or "The Indicator" in text
    )


def find_title(element):
    """
    Prefer NPR's story heading.
    """

    for tag_name in ["h2", "h3", "h4"]:
        heading = element.find(tag_name)

        if heading:
            title = clean_text(
                heading.get_text(" ", strip=True)
            )

            if (
                title
                and title != "The Indicator"
                and title != "The Indicator from Planet Money"
            ):
                return title

    return None


def find_story_url(element):
    """
    Find the most likely NPR article URL.
    """

    for link in element.find_all("a", href=True):
        href = normalize_url(link.get("href"))

        if not href:
            continue

        if "npr.org/" not in href:
            continue

        if "/player/" in href:
            continue

        if "/podcasts/" in href:
            continue

        if "/sections/business" in href:
            continue

        return href

    return None


def find_description(element, title):
    """
    Find a reasonable episode description.
    """

    for paragraph in element.find_all("p"):
        text = clean_text(
            paragraph.get_text(" ", strip=True)
        )

        if not text:
            continue

        if title and text == title:
            continue

        if len(text) >= 40:
            return text

    return None


def parse_archive(html):
    soup = BeautifulSoup(html, "html.parser")

    episodes = []
    seen_players = set()

    #
    # NPR has changed its markup over time, so instead
    # of depending on one specific CSS class, find every
    # embedded player and inspect its surrounding story block.
    #
    players = soup.find_all(
        src=re.compile(r"/player/embed/")
    )

    print(f"Found {len(players)} NPR player(s) on page.")

    for player in players:

        block = player

        #
        # Walk upward looking for a container that includes
        # enough text to identify the show and story.
        #
        for _ in range(8):
            if block.parent is None:
                break

            block = block.parent

            if looks_like_indicator(block):
                break

        if not looks_like_indicator(block):
            continue

        player_info = get_player_ids(block)

        if not player_info:
            continue

        key = (
            player_info["story_id"],
            player_info["audio_id"],
        )

        if key in seen_players:
            continue

        seen_players.add(key)

        block_text = clean_text(
            block.get_text(" ", strip=True)
        )

        title = find_title(block)

        episode = {
            "title": title,
            "date": find_date(block_text),
            "npr_url": find_story_url(block),
            "story_id": player_info["story_id"],
            "audio_id": player_info["audio_id"],
            "player_url": player_info["player_url"],
            "description": find_description(
                block,
                title
            ),
        }

        episodes.append(episode)

        if len(episodes) >= MAX_EPISODES:
            break

    return episodes


def main():

    html, source_url = download_archive()

    episodes = parse_archive(html)

    result = {
        "test_date": TEST_DATE,
        "source_url": source_url,
        "episode_count": len(episodes),
        "episodes": episodes,
    }

    print()
    print(
        f"Found {len(episodes)} "
        "Indicator episode(s)."
    )
    print()

    for number, episode in enumerate(
        episodes,
        start=1
    ):
        print(f"{number}. {episode['title']}")
        print(f"   Date: {episode['date']}")
        print(
            f"   Story ID: "
            f"{episode['story_id']}"
        )
        print(
            f"   Audio ID: "
            f"{episode['audio_id']}"
        )
        print(
            f"   URL: "
            f"{episode['npr_url']}"
        )
        print()

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(
        f"Saved results to {OUTPUT_FILE}"
    )

    if not episodes:
        raise RuntimeError(
            "Archive page downloaded, but no "
            "Indicator episodes were discovered."
        )


if __name__ == "__main__":
    main()
