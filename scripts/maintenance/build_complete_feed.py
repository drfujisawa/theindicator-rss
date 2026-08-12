#!/usr/bin/env python3
"""
Build the complete Indicator RSS feed by combining:

  1. Existing playable episodes from theindicator_feed.xml
  2. Resolved history episodes from indicator_history.json
     joined to indicator_enclosure_map.json

Episodes with status "no_audio" in the enclosure map are excluded.
Episodes already present in the existing feed are not duplicated.

Output: theindicator_feed.xml (overwritten in-place)
"""
from pathlib import Path

import json
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from email.utils import parsedate_to_datetime, format_datetime
from datetime import datetime, timezone, timedelta
REPO_ROOT = Path(__file__).resolve().parents[2]


HISTORY_FILE = str(REPO_ROOT / "indicator_history.json")
ENCLOSURE_MAP_FILE = str(REPO_ROOT / "indicator_enclosure_map.json")
FEED_FILE = str(REPO_ROOT / "theindicator_feed.xml")
ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

ET.register_namespace("itunes", ITUNES_NS)
ET.register_namespace("media", "http://search.yahoo.com/mrss/")
ET.register_namespace("podcast", "https://podcastindex.org/namespace/1.0")
ET.register_namespace("content", CONTENT_NS)


def _story_id_from_item(item):
    """Extract NPR story ID from a feed <item> element."""
    link = item.find("link")
    if link is not None and link.text:
        m = re.search(r"/(\d{7,})[/\-]", link.text)
        if m:
            return m.group(1)
        # newer nx-s1- style: story id appears as e= in the enclosure URL
        encl = item.find("enclosure")
        if encl is not None:
            url = encl.attrib.get("url", "")
            m2 = re.search(r"[?&]e=(\d+)", url)
            if m2:
                return m2.group(1)
    return None


def _guid_from_item(item):
    guid = item.find("guid")
    if guid is not None and guid.text:
        return guid.text.strip()
    encl = item.find("enclosure")
    if encl is not None:
        url = encl.attrib.get("url")
        if url:
            return url
    return None


def _pub_date_timestamp(item):
    pd = item.find("pubDate")
    if pd is None or not pd.text:
        return 0
    try:
        return parsedate_to_datetime(pd.text).timestamp()
    except Exception:
        return 0


def _parse_date_to_rfc2822(date_str):
    """Convert ISO-8601 date/datetime string to RFC 2822 pubDate."""
    # Handle "2018-10-09" or "2018-10-09T00:00:00-04:00"
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str)
        else:
            dt = datetime.fromisoformat(date_str + "T00:00:00+00:00")
        return format_datetime(dt)
    except Exception:
        return None


def build_history_item(hist_ep, enc_ep):
    """Build an RSS <item> element from a history + enclosure map entry."""
    item = ET.Element("item")

    title_text = hist_ep.get("title", "")
    ET.SubElement(item, "title").text = title_text

    desc_text = hist_ep.get("description", title_text)
    ET.SubElement(item, "description").text = desc_text

    pub_date = _parse_date_to_rfc2822(hist_ep.get("date", ""))
    if pub_date:
        ET.SubElement(item, "pubDate").text = pub_date

    npr_url = hist_ep.get("npr_url", "")
    ET.SubElement(item, "link").text = npr_url

    # Use story_id as guid (stable, permanent)
    story_id = hist_ep.get("story_id", "")
    guid_el = ET.SubElement(item, "guid")
    guid_el.text = story_id
    guid_el.set("isPermaLink", "false")

    enclosure_url = enc_ep.get("enclosure_url") or enc_ep.get("final_url", "")
    content_length = enc_ep.get("content_length") or 0
    encl = ET.SubElement(item, "enclosure")
    encl.set("url", enclosure_url)
    encl.set("length", str(content_length))
    encl.set("type", "audio/mpeg")

    ET.SubElement(item, f"{{{ITUNES_NS}}}title").text = title_text
    ET.SubElement(item, f"{{{ITUNES_NS}}}episodeType").text = "full"
    ET.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "no"

    return item


def main():
    print("Loading data files…")
    with open(HISTORY_FILE) as f:
        history = json.load(f)
    with open(ENCLOSURE_MAP_FILE) as f:
        enc_map = json.load(f)
    existing_tree = ET.parse(FEED_FILE)

    hist_episodes = history["episodes"]
    enc_episodes = enc_map["episodes"]  # dict keyed by story_id

    # Build lookup: story_id -> history episode
    hist_by_id = {ep["story_id"]: ep for ep in hist_episodes if ep.get("story_id")}

    # Resolved entries only
    resolved_enc = {
        sid: ep for sid, ep in enc_episodes.items() if ep.get("status") == "resolved"
    }
    print(f"  History episodes: {len(hist_episodes)}")
    print(f"  Resolved enclosure entries: {len(resolved_enc)}")

    # --- Existing feed ---
    existing_root = existing_tree.getroot()
    existing_channel = existing_root.find("channel")
    feed_items = existing_channel.findall("item")
    print(f"  Existing feed items: {len(feed_items)}")

    # Track what's already in the feed by story_id and GUID
    feed_story_ids = set()
    feed_guids = set()
    for item in feed_items:
        sid = _story_id_from_item(item)
        if sid:
            feed_story_ids.add(sid)
        g = _guid_from_item(item)
        if g:
            feed_guids.add(g)

    # --- Build history items for resolved episodes not already in feed ---
    added = 0
    for story_id, enc_ep in resolved_enc.items():
        # Skip if already in feed
        if story_id in feed_story_ids:
            continue
        # Skip if no matching history entry
        hist_ep = hist_by_id.get(story_id)
        if hist_ep is None:
            continue
        item = build_history_item(hist_ep, enc_ep)
        existing_channel.append(item)
        feed_story_ids.add(story_id)
        added += 1

    print(f"  Added {added} resolved history episodes.")

    # --- Sort all items newest-first ---
    all_items = existing_channel.findall("item")
    for it in all_items:
        existing_channel.remove(it)
    all_items.sort(key=_pub_date_timestamp, reverse=True)
    for it in all_items:
        existing_channel.append(it)

    # --- Reorder: metadata first, then items ---
    meta = []
    items_out = []
    for child in list(existing_channel):
        if child.tag == "item":
            items_out.append(child)
            existing_channel.remove(child)
        else:
            meta.append(child)
            existing_channel.remove(child)
    for i, el in enumerate(meta):
        existing_channel.insert(i, el)
    for it in items_out:
        existing_channel.append(it)

    # --- Write output ---
    ET.indent(existing_tree, space="  ")
    existing_tree.write(FEED_FILE, encoding="utf-8", xml_declaration=True)

    total = len(existing_channel.findall("item"))
    print()
    print(f"Saved {FEED_FILE}")
    print(f"Feed now contains {total} episodes.")


if __name__ == "__main__":
    main()
