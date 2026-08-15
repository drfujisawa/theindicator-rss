#!/usr/bin/env python3
"""Generate the Overcast archive feed from the complete Indicator feed."""

from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET


MAIN_FEED = Path("theindicator_feed.xml")
ARCHIVE_FEED = Path("theindicator_overcast_archive.xml")
OVERCAST_ITEM_LIMIT = 2000
ARCHIVE_OVERLAP = 250
ARCHIVE_TITLE = "The Indicator from Planet Money — Overcast Archive"

ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
ET.register_namespace("media", "http://search.yahoo.com/mrss/")
ET.register_namespace("podcast", "https://podcastindex.org/namespace/1.0")
ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")


def build_overcast_archive(
    main_feed=MAIN_FEED,
    archive_feed=ARCHIVE_FEED,
    item_limit=OVERCAST_ITEM_LIMIT,
    overlap=ARCHIVE_OVERLAP,
):
    """Write older items with overlap for Overcast's effective cached-item limit."""
    main_feed = Path(main_feed)
    archive_feed = Path(archive_feed)

    main_tree = ET.parse(main_feed)
    archive_root = deepcopy(main_tree.getroot())
    channel = archive_root.find("channel")
    if channel is None:
        raise RuntimeError(f"Could not find <channel> in {main_feed}.")

    title = channel.find("title")
    if title is None:
        title = ET.Element("title")
        channel.insert(0, title)
    title.text = ARCHIVE_TITLE

    items = channel.findall("item")
    archive_start = max(0, item_limit - overlap)
    for item in items[:archive_start]:
        channel.remove(item)

    archive_tree = ET.ElementTree(archive_root)
    ET.indent(archive_tree, space="  ")
    archive_tree.write(archive_feed, encoding="utf-8", xml_declaration=True)

    archive_count = max(0, len(items) - archive_start)
    print(f"Saved {archive_feed}")
    print(f"Archive feed contains {archive_count} episodes.")
    return archive_count


if __name__ == "__main__":
    build_overcast_archive()
