#!/usr/bin/env python3

import json
import re
import time
import urllib.request
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup


TEST_DATE = "05-31-2025"
OUTPUT_FILE = "history_test.json"
MAX_EPISODES = 10

ARCHIVE_URL = (
    "https://www.npr.org/sections/business/archive"
    f"?date={TEST_DATE}"
)

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

    return data


def normalize_url(url):
    if not url:
        return None

    return urljoin(
        "https://www.npr.org/",
        url,
    )


def find_indicator_links(html):
    """
    The archive page does NOT necessarily contain
    audio players.

    Its job is only to give us links to Indicator stories.
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    links = []
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

        #
        # Look for the story headline link.
        #
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

        #
        # Fallback: inspect all links.
        #
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
            links.append(url)

            break

    return links


def find_player(soup):
    """
    Find NPR's embedded audio player.

    Example:
    /player/embed/1252898728/1269346031
    """

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
    #
    # NPR normally provides a useful meta description.
    #
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
    #
    # First try HTML <time datetime=...>
    #
    time_element = soup.find(
        "time",
        attrs={"datetime": True}
    )

    if time_element:
        return time_element.get(
            "datetime"
        )

    #
    # Fallback to visible date text.
    #
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
        return match.group(0)

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


def main():

    print()
    print(
        "STEP 1: Download archive page"
    )
    print()

    archive_html = download(
        ARCHIVE_URL
    )

    links = find_indicator_links(
        archive_html
    )

    print()
    print(
        f"Found {len(links)} possible "
        "Indicator story link(s)."
    )
    print()

    for link in links:
        print(f"  {link}")

    print()
    print(
        "STEP 2: Open individual "
        "episode pages"
    )
    print()

    episodes = []

    for url in links:

        if len(episodes) >= MAX_EPISODES:
            break

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

        if episode:
            episodes.append(
                episode
            )

        #
        # Be polite to NPR.
        #
        time.sleep(1)

    result = {
        "test_date": TEST_DATE,
        "archive_url": ARCHIVE_URL,
        "links_discovered": len(
            links
        ),
        "episode_count": len(
            episodes
        ),
        "episodes": episodes,
    }

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

    print()
    print(
        "============================"
    )
    print(
        f"Recovered {len(episodes)} "
        "episode(s)."
    )
    print(
        f"Saved to {OUTPUT_FILE}"
    )
    print(
        "============================"
    )

    for number, episode in enumerate(
        episodes,
        start=1
    ):
        print()
        print(
            f"{number}. "
            f"{episode['title']}"
        )
        print(
            f"   Date: "
            f"{episode['date']}"
        )
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

    if not links:
        raise RuntimeError(
            "Archive downloaded, but no "
            "Indicator story links were found."
        )

    if not episodes:
        raise RuntimeError(
            "Indicator links were found, "
            "but no audio could be recovered."
        )


if __name__ == "__main__":
    main()
