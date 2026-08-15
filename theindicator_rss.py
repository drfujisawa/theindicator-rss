#!/usr/bin/env python3

import os
import urllib.request
import xml.etree.ElementTree as ET
from copy import deepcopy
from email.utils import parsedate_to_datetime

from theindicator_archive import build_overcast_archive


OFFICIAL_FEED = "https://feeds.npr.org/510325/podcast.xml"
OUTPUT_FILE = "theindicator_feed.xml"


def download_official_feed():
    print("Downloading current NPR Indicator feed...")

    request = urllib.request.Request(
        OFFICIAL_FEED,
        headers={
            "User-Agent": "Mozilla/5.0 theindicator-rss"
        }
    )

    with urllib.request.urlopen(request) as response:
        return response.read()


def get_episode_id(item):
    """
    Return something stable that identifies an episode.

    Prefer GUID. If that is unavailable, use the enclosure URL.
    """

    guid = item.find("guid")

    if guid is not None and guid.text:
        return guid.text.strip()

    enclosure = item.find("enclosure")

    if enclosure is not None:
        url = enclosure.attrib.get("url")

        if url:
            return url

    return None


def episode_date(item):
    pubdate = item.find("pubDate")

    if pubdate is None or not pubdate.text:
        return 0

    try:
        return parsedate_to_datetime(pubdate.text).timestamp()
    except Exception:
        return 0


def load_existing_feed():
    if not os.path.exists(OUTPUT_FILE):
        return None

    print(f"Loading existing {OUTPUT_FILE}...")
    return ET.parse(OUTPUT_FILE)


def build_feed():
    official_xml = download_official_feed()

    official_root = ET.fromstring(official_xml)
    official_channel = official_root.find("channel")

    if official_channel is None:
        raise RuntimeError("Could not find <channel> in NPR feed.")

    existing_tree = load_existing_feed()

    #
    # First run:
    # Start with NPR's current feed exactly as NPR provides it.
    #
    if existing_tree is None:
        print("No existing full-history feed found.")
        print("Creating one from NPR's current feed.")

        tree = ET.ElementTree(official_root)

    else:
        tree = existing_tree

        existing_root = tree.getroot()
        existing_channel = existing_root.find("channel")

        if existing_channel is None:
            raise RuntimeError(
                f"Could not find <channel> in {OUTPUT_FILE}."
            )

        #
        # Find all episodes already saved.
        #
        known_ids = set()

        for item in existing_channel.findall("item"):
            episode_id = get_episode_id(item)

            if episode_id:
                known_ids.add(episode_id)

        #
        # Copy episodes from NPR that we haven't saved before.
        #
        new_items = []

        for item in official_channel.findall("item"):
            episode_id = get_episode_id(item)

            if episode_id and episode_id not in known_ids:
                new_items.append(deepcopy(item))
                known_ids.add(episode_id)

        print(f"Found {len(new_items)} new episode(s).")

        if not new_items:
            print("Feed is already current; leaving the archive unchanged.")
            return

        for item in new_items:
            existing_channel.append(item)

        #
        # Sort episodes newest first.
        #
        items = existing_channel.findall("item")

        for item in items:
            existing_channel.remove(item)

        items.sort(
            key=episode_date,
            reverse=True
        )

        for item in items:
            existing_channel.append(item)

        #
        # Update show-level metadata from NPR.
        #
        # Leave the stored episode list alone.
        #
        item_tag_names = {"item"}

        for old_element in list(existing_channel):
            if old_element.tag not in item_tag_names:
                existing_channel.remove(old_element)

        metadata = []

        for element in official_channel:
            if element.tag != "item":
                metadata.append(deepcopy(element))

        #
        # Put NPR metadata before episodes.
        #
        for index, element in enumerate(metadata):
            existing_channel.insert(index, element)

    #
    # Write XML.
    #
    ET.indent(tree, space="  ")

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True
    )

    channel = tree.getroot().find("channel")
    count = len(channel.findall("item"))

    print()
    print(f"Saved {OUTPUT_FILE}")
    print(f"Feed now contains {count} episodes.")
    build_overcast_archive(OUTPUT_FILE)


if __name__ == "__main__":
    build_feed()
