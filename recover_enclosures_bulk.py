#!/usr/bin/env python3
"""
Bulk enclosure recovery for indicator_history.json.

Processes episodes in batches without requiring an offset.  Each run:
  1. Loads (or creates) indicator_enclosure_map.json.
  2. Seeds from theindicator_feed.xml for any episode not yet mapped.
  3. Builds the eligible queue:
       - episodes absent / pending
       - network_failed below retry cap
       (excludes: resolved, no_audio unless --force-retry-no-audio,
                  network_failed_exhausted unless --force-retry-no-audio)
  4. Sorts queue deterministically by (date, story_id).
  5. Processes the first BATCH_SIZE entries.
  6. Writes indicator_enclosure_map.json.
  7. Prints a progress report.

Environment variables (set by workflow):
  BATCH_SIZE              default 100
  FORCE_RETRY_NO_AUDIO    "true" to re-attempt no_audio episodes
  FORCE_RETRY_EXHAUSTED   "true" to re-attempt network_failed_exhausted
"""

import datetime
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

# ── constants ──────────────────────────────────────────────────────────────────

HISTORY_FILE = "indicator_history.json"
FEED_FILE = "theindicator_feed.xml"
MAP_FILE = "indicator_enclosure_map.json"
SCHEMA_VERSION = 1
MAX_RETRIES = 5
REQUEST_DELAY = 1.0   # seconds between outbound requests
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── URL / identity helpers ─────────────────────────────────────────────────────

_RE_AUDIO_DIRECT = [
    re.compile(p, re.I | re.S) for p in [
        r'https?://[^\s"\'<>]*simplecastaudio\.com[^\s"\'<>]*\.mp3[^\s"\'<>]*',
        r'https?://[^\s"\'<>]*npr\.simplecastaudio\.com[^\s"\'<>]*',
        r'https?://[^\s"\'<>]*play\.podtrac\.com/npr-510325[^\s"\'<>]*',
        r'https?://[^\s"\'<>]*prfx\.byspotify\.com[^\s"\'<>]*',
        r'https?://[^\s"\'<>]*\.mp3[^\s"\'<>]{0,200}',
    ]
]

_RE_AUDIO_KEY = re.compile(r'"audio[Uu]rl"\s*:\s*"([^"]+)"', re.I)
_RE_ENCLOSURE = re.compile(r'"enclosure(?:Url)?"\s*:\s*"([^"]+)"', re.I)
_RE_SC_EPISODE_UUID = re.compile(
    r'/episodes/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/',
    re.I,
)


def _netloc_matches(url, *domains):
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in domains)
    except Exception:
        return False


def is_audio_url(url):
    u = url.lower()
    return (
        _netloc_matches(u, "simplecastaudio.com")
        or (_netloc_matches(u, "play.podtrac.com") and "/npr-510325" in u)
        or _netloc_matches(u, "prfx.byspotify.com")
        or u.endswith(".mp3")
        or ".mp3?" in u
        or ".mp3&" in u
    )


def clean_url(value):
    value = value.replace("\\/", "/").replace("\\u0026", "&").replace("&amp;", "&")
    return value.strip()


def extract_episode_uuid(url):
    m = _RE_SC_EPISODE_UUID.search(url)
    return m.group(1).lower() if m else None


def audio_identity(url):
    uuid = extract_episode_uuid(url)
    if uuid:
        return ("uuid", uuid)
    p = urlparse(url)
    bare = urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", "", ""))
    return ("url", bare)


def extract_candidates(html):
    found = []
    seen = set()

    def add(url, method):
        url = clean_url(url)
        if url and url not in seen and is_audio_url(url):
            seen.add(url)
            found.append({"url": url, "method": method})

    for pattern in _RE_AUDIO_DIRECT:
        for m in pattern.findall(html):
            add(m, "regex_direct")
    for m in _RE_AUDIO_KEY.findall(html):
        add(m, "json_audioUrl_key")
    for m in _RE_ENCLOSURE.findall(html):
        add(m, "json_enclosure_key")
    for m in re.findall(
        r'"(https?://[^"]*(?:simplecastaudio\.com|podtrac\.com/npr-510325|prfx\.byspotify\.com)[^"]*)"',
        html, re.I,
    ):
        add(m, "inline_json_host")
    return found


# ── network helpers ────────────────────────────────────────────────────────────


def fetch(url, method="GET"):
    req = Request(url, headers=HEADERS, method=method)
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace") if method == "GET" else ""
            return {
                "ok": True,
                "status": resp.status,
                "final_url": resp.geturl(),
                "content_type": resp.headers.get("Content-Type", ""),
                "content_length": resp.headers.get("Content-Length"),
                "body": body,
                "error": None,
            }
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "final_url": url,
                "content_type": "", "content_length": None, "body": "", "error": f"HTTPError {exc.code}"}
    except URLError as exc:
        return {"ok": False, "status": 0, "final_url": url,
                "content_type": "", "content_length": None, "body": "", "error": f"URLError: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "final_url": url,
                "content_type": "", "content_length": None, "body": "", "error": str(exc)}


def validate_audio(url):
    time.sleep(REQUEST_DELAY)
    r = fetch(url, method="HEAD")
    if not r["ok"]:
        time.sleep(REQUEST_DELAY)
        r = fetch(url, method="GET")
    ctype = r.get("content_type", "")
    final = r.get("final_url", url)
    is_audio = (
        "audio" in ctype.lower()
        or final.lower().endswith(".mp3")
        or _netloc_matches(final, "simplecastaudio.com")
        or _netloc_matches(final, "play.podtrac.com")
        or _netloc_matches(final, "prfx.byspotify.com")
    )
    is_player_page = "text/html" in ctype.lower() and _netloc_matches(final, "npr.org")
    cl = r.get("content_length")
    try:
        content_length = int(cl) if cl else None
    except (TypeError, ValueError):
        content_length = None
    return {
        "ok": r["ok"],
        "status": r["status"],
        "final_url": final,
        "content_type": ctype,
        "content_length": content_length,
        "is_audio": is_audio and not is_player_page,
        "error": r.get("error"),
    }


# ── map I/O ────────────────────────────────────────────────────────────────────


def load_map():
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE) as f:
            return json.load(f)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": None,
        "episodes": {},
    }


def save_map(m):
    m["generated_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(MAP_FILE, "w") as f:
        json.dump(m, f, indent=2)


# ── seeding ────────────────────────────────────────────────────────────────────


def seed_from_feed(enc_map, history_by_id):
    """
    Pre-populate map entries from theindicator_feed.xml for any history episode
    not yet mapped.  These are already validated; mark them resolved immediately.
    """
    if not os.path.exists(FEED_FILE):
        return 0
    seeded = 0
    try:
        tree = ET.parse(FEED_FILE)
    except Exception as exc:
        print(f"  [seed] Cannot parse {FEED_FILE}: {exc}", file=sys.stderr)
        return 0
    channel = tree.getroot().find("channel")
    if channel is None:
        return 0
    for item in channel.findall("item"):
        link = item.findtext("link") or ""
        enc_el = item.find("enclosure")
        if enc_el is None:
            continue
        enc_url = enc_el.attrib.get("url", "")
        if not enc_url:
            continue
        m = re.search(r"/(\d{9,12})/", link)
        if not m:
            continue
        story_id = m.group(1)
        if story_id not in history_by_id:
            continue
        if story_id in enc_map["episodes"]:
            continue
        ep = history_by_id[story_id]
        raw_date = item.findtext("pubDate") or ""
        try:
            iso_date = parsedate_to_datetime(raw_date).strftime("%Y-%m-%d")
        except Exception:
            iso_date = ep["date"][:10]
        enc_map["episodes"][story_id] = {
            "story_id": story_id,
            "audio_id": ep.get("audio_id", ""),
            "date": iso_date,
            "title": ep.get("title", ""),
            "npr_url": ep.get("npr_url", ""),
            "status": "resolved",
            "enclosure_url": enc_url,
            "final_url": enc_url,
            "episode_uuid": extract_episode_uuid(enc_url),
            "http_status": None,
            "content_type": None,
            "content_length": None,
            "extraction_method": "feed_seed",
            "provenance": "theindicator_feed.xml",
            "retry_count": 0,
            "resolved_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        seeded += 1
    return seeded


# ── episode processing ─────────────────────────────────────────────────────────


def process_episode(ep, run_label):
    story_id = ep["story_id"]
    npr_url = ep.get("npr_url", "")
    print(f"\n  [{story_id}] {ep['date'][:10]}  {ep['title'][:60]}")
    print(f"    URL: {npr_url}")

    result = {
        "story_id": story_id,
        "audio_id": ep.get("audio_id", ""),
        "date": ep["date"][:10],
        "title": ep.get("title", ""),
        "npr_url": npr_url,
        "status": None,
        "enclosure_url": None,
        "final_url": None,
        "episode_uuid": None,
        "http_status": None,
        "content_type": None,
        "content_length": None,
        "extraction_method": None,
        "provenance": run_label,
        "retry_count": 0,
        "error": None,
        "resolved_at": None,
    }

    if not npr_url:
        result["status"] = "no_audio"
        result["error"] = "no npr_url in history"
        return result

    # Fetch story page
    time.sleep(REQUEST_DELAY)
    page = fetch(npr_url)
    result["http_status"] = page["status"]

    if not page["ok"]:
        result["status"] = "network_failed"
        result["error"] = page.get("error") or f"HTTP {page['status']}"
        print(f"    ✗ network_failed: {result['error']}")
        return result

    html = page["body"]
    print(f"    ✓ HTTP {page['status']}  ({len(html):,} bytes)")

    candidates = extract_candidates(html)
    if not candidates:
        result["status"] = "no_audio"
        result["error"] = "no audio candidates found in page"
        print("    ✗ no audio candidates")
        return result

    # Validate first audio candidate (regex_direct is always first if present)
    best = None
    for c in candidates:
        v = validate_audio(c["url"])
        if v["is_audio"]:
            best = {"url": c["url"], "method": c["method"], "validation": v}
            break

    if best is None:
        result["status"] = "no_audio"
        result["error"] = f"candidates found but none validated as audio ({len(candidates)} tried)"
        print("    ✗ no validated audio")
        return result

    v = best["validation"]
    result["status"] = "resolved"
    result["enclosure_url"] = best["url"]
    result["final_url"] = v["final_url"]
    result["episode_uuid"] = extract_episode_uuid(v["final_url"]) or extract_episode_uuid(best["url"])
    result["http_status"] = v["status"]
    result["content_type"] = v["content_type"]
    result["content_length"] = v["content_length"]
    result["extraction_method"] = best["method"]
    result["resolved_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"    ✓ resolved via [{best['method']}]  {v['final_url'][:80]}")
    return result


# ── main ───────────────────────────────────────────────────────────────────────


def main():
    batch_size = int(os.environ.get("BATCH_SIZE", "100"))
    force_no_audio = os.environ.get("FORCE_RETRY_NO_AUDIO", "").lower() == "true"
    force_exhausted = os.environ.get("FORCE_RETRY_EXHAUSTED", "").lower() == "true"
    run_label = f"bulk_run_{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"

    print(f"=== Bulk Enclosure Recovery  batch_size={batch_size}  run={run_label} ===\n")

    # Load history
    with open(HISTORY_FILE) as f:
        hist_data = json.load(f)
    all_episodes = hist_data["episodes"]
    history_by_id = {ep["story_id"]: ep for ep in all_episodes}
    print(f"History: {len(all_episodes)} episodes")

    # Load (or create) map
    enc_map = load_map()
    episodes_map = enc_map["episodes"]

    # Seed from feed
    seeded = seed_from_feed(enc_map, history_by_id)
    if seeded:
        print(f"Seeded {seeded} episode(s) from {FEED_FILE}")

    # Build status counts across all history episodes
    def status_of(story_id):
        entry = episodes_map.get(story_id)
        if entry is None:
            return "pending"
        return entry.get("status", "pending")

    # Build eligible queue
    eligible = []
    for ep in all_episodes:
        sid = ep["story_id"]
        st = status_of(sid)
        if st == "resolved":
            continue
        if st == "no_audio" and not force_no_audio:
            continue
        if st == "network_failed_exhausted" and not force_exhausted:
            continue
        eligible.append(ep)

    # Sort deterministically: date asc, then story_id asc
    eligible.sort(key=lambda e: (e["date"][:10], e["story_id"]))

    # Count current map states
    map_counts = {}
    for ep in all_episodes:
        st = status_of(ep["story_id"])
        map_counts[st] = map_counts.get(st, 0) + 1

    batch = eligible[:batch_size]
    print(f"\nEligible this run: {len(eligible)}  →  processing {len(batch)}\n")

    # Process batch
    attempted = 0
    newly_resolved = 0
    for ep in batch:
        sid = ep["story_id"]
        prev = episodes_map.get(sid, {})
        prev_retries = prev.get("retry_count", 0) if prev else 0

        result = process_episode(ep, run_label)
        result["retry_count"] = prev_retries + (1 if result["status"] != "resolved" else 0)

        # Escalate to exhausted after MAX_RETRIES failed attempts
        if result["status"] == "network_failed" and result["retry_count"] >= MAX_RETRIES:
            result["status"] = "network_failed_exhausted"

        episodes_map[sid] = result
        attempted += 1
        if result["status"] == "resolved":
            newly_resolved += 1

    # Save map
    save_map(enc_map)

    # Final report
    final_counts = {}
    for ep in all_episodes:
        st = status_of(ep["story_id"])
        final_counts[st] = final_counts.get(st, 0) + 1

    # Remaining eligible after this run
    remaining = 0
    for ep in all_episodes:
        st = status_of(ep["story_id"])
        if st == "resolved":
            continue
        if st == "no_audio" and not force_no_audio:
            continue
        if st == "network_failed_exhausted" and not force_exhausted:
            continue
        remaining += 1

    print("\n" + "=" * 72)
    print("PROGRESS REPORT")
    print("=" * 72)
    print(f"  Total history episodes   : {len(all_episodes)}")
    print(f"  Resolved                 : {final_counts.get('resolved', 0)}")
    print(f"  Pending (never attempted): {final_counts.get('pending', 0)}")
    print(f"  Network failed (retryable): {final_counts.get('network_failed', 0)}")
    print(f"  Network failed (exhausted): {final_counts.get('network_failed_exhausted', 0)}")
    print(f"  No audio found           : {final_counts.get('no_audio', 0)}")
    print(f"  ─────────────────────────────")
    print(f"  Attempted this run       : {attempted}")
    print(f"  Newly resolved this run  : {newly_resolved}")
    print(f"  Remaining eligible       : {remaining}")
    print("=" * 72)

    if remaining > 0:
        print(f"\nNext run: trigger workflow again (no offset needed).")
    else:
        print("\nAll eligible episodes processed.")


if __name__ == "__main__":
    main()
