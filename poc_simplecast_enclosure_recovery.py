#!/usr/bin/env python3
"""
Proof-of-concept: recover Simplecast enclosure URLs from NPR story pages.

Selects ~12 sample episodes from indicator_history.json (2 per year, 2019-2024)
plus 2 control episodes from the existing feed with known enclosure URLs.

For each episode:
  1. Fetches the current live NPR story page.
  2. Exhaustively searches the HTML for audio references.
  3. Extracts Simplecast UUIDs and probes public API endpoints.
  4. Validates every candidate URL with an HTTP HEAD/GET request.

Saves diagnostic details to poc_simplecast_results.json and prints a
human-readable summary table.
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

HISTORY_FILE = "indicator_history.json"
FEED_FILE = "theindicator_feed.xml"
OUTPUT_FILE = "poc_simplecast_results.json"

TIMEOUT = 30
DELAY = 1.0  # seconds between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── pattern catalogue ──────────────────────────────────────────────────────────

# Simplecast UUID pattern (podcast collection and episode UUIDs)
RE_UUID = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.I,
)

# Well-known Simplecast / NPR audio URL fragments
RE_AUDIO_URLS = [
    re.compile(p, re.I | re.S) for p in [
        # Direct simplecast audio
        r'https?://[^\s"\'<>]*simplecastaudio\.com[^\s"\'<>]*\.mp3[^\s"\'<>]*',
        r'https?://[^\s"\'<>]*npr\.simplecastaudio\.com[^\s"\'<>]*',
        # Podtrac / prfx wrapper
        r'https?://[^\s"\'<>]*play\.podtrac\.com/npr-510325[^\s"\'<>]*',
        r'https?://[^\s"\'<>]*prfx\.byspotify\.com[^\s"\'<>]*',
        # Generic .mp3 URL
        r'https?://[^\s"\'<>]*\.mp3[^\s"\'<>]{0,200}',
    ]
]

# audioUrl / audio_url JSON keys
RE_AUDIO_KEY = re.compile(
    r'"audio[Uu]rl"\s*:\s*"([^"]+)"',
    re.I,
)

# enclosure fields in JSON
RE_ENCLOSURE = re.compile(
    r'"enclosure(?:Url)?"\s*:\s*"([^"]+)"',
    re.I,
)

# NPR player embed src
RE_PLAYER_EMBED = re.compile(
    r'<iframe[^>]+src=["\']([^"\']*(?:player\.embed|player/embed)[^"\']*)["\']',
    re.I | re.S,
)

# Simplecast public API endpoint patterns
# https://player.simplecast.com/api/episodes/{uuid}
RE_SC_EPISODE_ID = re.compile(
    r'(?:simplecast|episode[_-]?id|episodeId)["\s:=]+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.I,
)

# JSON-LD audio block
RE_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)

# Inline JSON state objects that contain audio
RE_STATE_BLOB = re.compile(
    r'(?:window\.__(?:STATE|INITIAL_STATE|DATA|PROPS)|__NEXT_DATA__|initialState)\s*=\s*({.*?})\s*;?\s*</script>',
    re.I | re.S,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def fetch(url, method="GET"):
    req = Request(url, headers=HEADERS, method=method)
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            final_url = resp.geturl()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace") if method == "GET" else ""
            return {
                "ok": True,
                "status": status,
                "final_url": final_url,
                "content_type": ctype,
                "body": body,
            }
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "final_url": url, "content_type": "", "body": ""}
    except URLError as exc:
        return {"ok": False, "status": 0, "final_url": url, "content_type": "", "body": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "final_url": url, "content_type": "", "body": str(exc)}


def clean(value):
    """Unescape common HTML / JSON escape sequences."""
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    value = value.replace("&amp;", "&")
    return value.strip()


def _netloc_matches(url, *domains):
    """Return True if the URL's netloc ends with one of the given domain strings."""
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in domains)
    except Exception:
        return False


def is_audio_url(url):
    u = url.lower()
    return (
        _netloc_matches(u, "simplecastaudio.com")
        or (
            _netloc_matches(u, "play.podtrac.com")
            and "/npr-510325" in u
        )
        or _netloc_matches(u, "prfx.byspotify.com")
        or u.endswith(".mp3")
        or ".mp3?" in u
        or ".mp3&" in u
    )


def extract_audio_candidates(html, story_id, audio_id):
    """
    Exhaustively extract every candidate audio URL from page HTML.
    Returns a list of dicts: {url, method}.
    """
    found = []
    seen = set()

    def add(url, method):
        url = clean(url)
        if url and url not in seen and is_audio_url(url):
            seen.add(url)
            found.append({"url": url, "method": method})

    # 1. Direct pattern matching for known audio URL forms
    for pattern in RE_AUDIO_URLS:
        for m in pattern.findall(html):
            add(m, "regex_direct")

    # 2. audioUrl / audio_url JSON keys
    for m in RE_AUDIO_KEY.findall(html):
        add(m, "json_audioUrl_key")

    # 3. enclosure fields
    for m in RE_ENCLOSURE.findall(html):
        add(m, "json_enclosure_key")

    # 4. JSON-LD blocks
    for block in RE_JSONLD.findall(html):
        try:
            obj = json.loads(block)
            _walk_json(obj, add, "json_ld")
        except Exception:
            # Still try regex on raw text
            for m in re.findall(r'"(https?://[^"]+\.mp3[^"]*)"', block):
                add(m, "json_ld_raw")

    # 5. Inline state/data blobs
    for blob in RE_STATE_BLOB.findall(html):
        # Truncated JSON is common; try regex first
        for m in re.findall(r'"(https?://[^"]+\.mp3[^"]*)"', blob):
            add(m, "state_blob_raw")
        # Also try full parse on smaller blobs
        if len(blob) < 500_000:
            try:
                obj = json.loads(blob)
                _walk_json(obj, add, "state_blob_json")
            except Exception:
                pass

    # 6. Any remaining JSON strings containing audio hosts anywhere in page
    for m in re.findall(
        r'"(https?://[^"]*(?:simplecastaudio\.com|podtrac\.com/npr-510325|prfx\.byspotify\.com)[^"]*)"',
        html,
        re.I,
    ):
        add(m, "inline_json_host")

    # 7. Player embed iframes — record embed src URLs for diagnostic context
    for m in RE_PLAYER_EMBED.findall(html):
        found.append({"url": m, "method": "player_embed_iframe"})

    return found


def _walk_json(obj, add_fn, method, depth=0):
    """Recursively walk a parsed JSON object and collect audio URLs."""
    if depth > 20:
        return
    if isinstance(obj, str):
        if is_audio_url(obj):
            add_fn(obj, method)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_json(v, add_fn, method, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk_json(v, add_fn, method, depth + 1)


def extract_simplecast_uuids(html):
    """Return all UUIDs found in the page that look Simplecast-related."""
    uuids = []
    seen = set()

    # Find UUIDs in simplecast-adjacent context
    for m in re.finditer(
        r'(?:simplecast|episode[_-]?uuid|episodeUuid|collection)[^"\']{0,30}'
        r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
        html,
        re.I,
    ):
        uid = m.group(1).lower()
        if uid not in seen:
            seen.add(uid)
            uuids.append(uid)

    # All UUIDs within 200 chars of "simplecast"
    for sc_match in re.finditer(r'simplecast', html, re.I):
        start = max(0, sc_match.start() - 200)
        end = min(len(html), sc_match.end() + 200)
        snippet = html[start:end]
        for uid in RE_UUID.findall(snippet):
            uid = uid.lower()
            if uid not in seen:
                seen.add(uid)
                uuids.append(uid)

    return uuids


def probe_simplecast_api(episode_uuid):
    """
    Try public Simplecast API endpoints for an episode UUID.
    Returns audio URL string or None.
    """
    endpoints = [
        f"https://api.simplecast.com/episodes/{episode_uuid}",
        f"https://player.simplecast.com/episodes/{episode_uuid}",
    ]
    for url in endpoints:
        time.sleep(DELAY)
        r = fetch(url)
        if r["ok"] and r["body"]:
            # Look for enclosure/audio URL in response
            for m in re.findall(
                r'"(https?://[^"]*simplecastaudio\.com[^"]*\.mp3[^"]*)"',
                r["body"],
                re.I,
            ):
                return clean(m)
            # Broader search
            for m in re.findall(r'"(https?://[^"]*\.mp3[^"]*)"', r["body"], re.I):
                u = clean(m)
                if is_audio_url(u):
                    return u
    return None


def validate_audio_url(url):
    """
    Validate a candidate URL: follow redirects, check Content-Type.
    Returns dict with validation details.
    """
    time.sleep(DELAY)
    r = fetch(url, method="HEAD")
    if not r["ok"]:
        # Fallback to GET for servers that reject HEAD
        time.sleep(DELAY)
        r = fetch(url)

    ctype = r.get("content_type", "")
    final = r.get("final_url", url)
    host = urlparse(final).netloc if r["ok"] else ""
    is_audio = (
        "audio" in ctype.lower()
        or final.lower().endswith(".mp3")
        or _netloc_matches(final, "simplecastaudio.com")
        or _netloc_matches(final, "play.podtrac.com")
        or _netloc_matches(final, "prfx.byspotify.com")
    )
    is_player_page = (
        "text/html" in ctype.lower()
        and _netloc_matches(final, "npr.org")
    )

    return {
        "status": r["status"],
        "final_url": final,
        "content_type": ctype,
        "is_audio": is_audio and not is_player_page,
        "is_player_page": is_player_page,
        "host": host,
    }


# ── sample selection ───────────────────────────────────────────────────────────


def select_sample_episodes():
    with open(HISTORY_FILE) as f:
        data = json.load(f)
    episodes = data["episodes"]

    by_year = {}
    for ep in episodes:
        y = ep["date"][:4]
        by_year.setdefault(y, []).append(ep)

    # Sort each year chronologically
    for y in by_year:
        by_year[y].sort(key=lambda x: x["date"])

    sample = []
    target_years = ["2019", "2020", "2021", "2022", "2023", "2024"]
    for y in target_years:
        eps = by_year.get(y, [])
        if not eps:
            continue
        # First episode of the year
        sample.append({**eps[0], "sample_label": f"{y}_first", "known_enclosure": None})
        if len(eps) >= 2:
            # Mid-year episode
            mid = eps[len(eps) // 2]
            sample.append({**mid, "sample_label": f"{y}_mid", "known_enclosure": None})

    return sample


def select_control_episodes():
    """
    Pick 2 recent episodes from the existing feed where the enclosure URL
    is already known. We will use these to verify that our extraction method
    can rediscover a known enclosure.
    """
    tree = ET.parse(FEED_FILE)
    root = tree.getroot()
    channel = root.find("channel")
    items = channel.findall("item")

    controls = []
    for item in items:
        link = item.findtext("link") or ""
        enc = item.find("enclosure")
        if not link or enc is None:
            continue
        enc_url = enc.attrib.get("url", "")
        if not enc_url or "simplecastaudio.com" not in enc_url:
            continue
        # Extract story_id from link
        m = re.search(r'/(\d{9,12})/', link)
        if not m:
            continue
        story_id = m.group(1)
        # Normalise pubDate (RFC 2822) to YYYY-MM-DD ISO date string
        raw_date = item.findtext("pubDate") or ""
        try:
            iso_date = parsedate_to_datetime(raw_date).strftime("%Y-%m-%d")
        except Exception:
            iso_date = raw_date
        # Build matching history entry if possible
        controls.append({
            "title": item.findtext("title") or "",
            "date": iso_date,
            "npr_url": link,
            "story_id": story_id,
            "audio_id": "",
            "sample_label": "control",
            "known_enclosure": enc_url,
        })
        if len(controls) >= 2:
            break

    return controls


# ── main ───────────────────────────────────────────────────────────────────────


def process_episode(ep):
    label = ep["sample_label"]
    story_id = ep.get("story_id", "")
    audio_id = ep.get("audio_id", "")
    npr_url = ep.get("npr_url", "")
    known_enc = ep.get("known_enclosure")

    print(f"\n{'='*72}")
    print(f"  {label}  |  {ep['date'][:10]}  |  {ep['title'][:50]}")
    print(f"  URL: {npr_url}")

    result = {
        "sample_label": label,
        "date": ep["date"][:10],
        "title": ep["title"],
        "story_id": story_id,
        "audio_id": audio_id,
        "npr_url": npr_url,
        "known_enclosure": known_enc,
        "http_status": None,
        "candidates": [],
        "simplecast_uuids": [],
        "api_probes": [],
        "validation": None,
        "audio_found": False,
        "host": "",
        "extraction_method": "",
        "validation_result": "",
        "control_rediscovered": None,
    }

    # ── 1. Fetch story page ────────────────────────────────────────────────
    time.sleep(DELAY)
    page = fetch(npr_url)
    result["http_status"] = page["status"]

    if not page["ok"]:
        print(f"  ✗ HTTP {page['status']}  →  {page.get('body','')[:80]}")
        result["validation_result"] = f"page fetch failed: HTTP {page['status']}"
        return result

    html = page["body"]
    print(f"  ✓ HTTP {page['status']}  ({len(html):,} bytes)")

    # ── 2. Extract candidates ──────────────────────────────────────────────
    candidates = extract_audio_candidates(html, story_id, audio_id)
    result["candidates"] = [c["url"] for c in candidates]
    print(f"  Candidates from page: {len(candidates)}")
    for c in candidates[:5]:
        print(f"    [{c['method']}] {c['url'][:100]}")

    # ── 3. Extract Simplecast UUIDs ────────────────────────────────────────
    uuids = extract_simplecast_uuids(html)
    result["simplecast_uuids"] = uuids
    if uuids:
        print(f"  Simplecast-adjacent UUIDs: {uuids[:3]}")

    # ── 4. Probe Simplecast API for episode UUIDs ──────────────────────────
    api_audio = None
    for uid in uuids[:3]:  # limit API probes
        print(f"  Probing Simplecast API for {uid} …")
        api_url = probe_simplecast_api(uid)
        probe_entry = {"uuid": uid, "audio_url": api_url}
        result["api_probes"].append(probe_entry)
        if api_url and not api_audio:
            api_audio = api_url
            candidates.append({"url": api_url, "method": "simplecast_api"})
            print(f"    → {api_url[:100]}")

    # ── 5. Validate candidates ─────────────────────────────────────────────
    best = None
    for c in candidates:
        url = c["url"]
        method = c["method"]
        print(f"  Validating [{method}] {url[:90]} …")
        v = validate_audio_url(url)
        c["validation"] = v
        if v["is_audio"] and best is None:
            best = c

    if best:
        result["audio_found"] = True
        result["host"] = best["validation"]["host"]
        result["extraction_method"] = best["method"]
        result["validation"] = best["validation"]
        result["validation_result"] = (
            f"HTTP {best['validation']['status']} → "
            f"{best['validation']['final_url'][:80]}"
        )
        print(f"  ✓ AUDIO FOUND via [{best['method']}] host={result['host']}")

        # Control check: did we rediscover the known enclosure?
        if known_enc:
            rediscovered = (
                best["url"] == known_enc
                or best["validation"]["final_url"] == known_enc
            )
            result["control_rediscovered"] = rediscovered
            print(f"  Control rediscovered: {rediscovered}")
    else:
        result["validation_result"] = "no audio URL found"
        print("  ✗ No audio URL found")

    return result


def print_summary(results):
    print("\n\n" + "=" * 90)
    print("SUMMARY TABLE")
    print("=" * 90)
    hdr = f"{'Date':<12} {'StoryID':<13} {'AudioID':<12} {'HTTP':>4} {'Found':>6} {'Host':<35} {'Method':<22} {'Result'}"
    print(hdr)
    print("-" * 90)

    by_year = {}
    for r in results:
        y = r["date"][:4]
        by_year.setdefault(y, []).append(r)
        found_str = "YES" if r["audio_found"] else "NO"
        host = r["host"][:34] if r["host"] else ""
        method = r["extraction_method"][:21] if r["extraction_method"] else ""
        val = r["validation_result"][:40] if r["validation_result"] else ""
        print(
            f"{r['date']:<12} {r['story_id']:<13} {r['audio_id']:<12} "
            f"{r['http_status'] or 0:>4} {found_str:>6} {host:<35} {method:<22} {val}"
        )

    print("\n--- Success by year ---")
    for y in sorted(by_year.keys()):
        eps = by_year[y]
        successes = sum(1 for e in eps if e["audio_found"])
        label = "control" if eps[0]["sample_label"] == "control" else y
        print(f"  {label}: {successes}/{len(eps)}")

    print("\n--- Controls ---")
    controls = [r for r in results if r["sample_label"] == "control"]
    for c in controls:
        rediscovered = c.get("control_rediscovered")
        print(
            f"  {c['date'][:10]}  audio_found={c['audio_found']}  "
            f"rediscovered_known={rediscovered}"
        )

    # Overall stats
    total = len(results)
    found = sum(1 for r in results if r["audio_found"])
    print(f"\nOverall: {found}/{total} audio URLs found ({100*found//total if total else 0}%)")

    # Method breakdown
    methods = {}
    for r in results:
        if r["audio_found"]:
            m = r["extraction_method"]
            methods[m] = methods.get(m, 0) + 1
    if methods:
        print("Methods that worked:")
        for m, count in sorted(methods.items(), key=lambda x: -x[1]):
            print(f"  {m}: {count}")

    # Simplecast UUID analysis
    uuid_found = sum(1 for r in results if r["simplecast_uuids"])
    print(f"\nSimplecast UUIDs found in page HTML: {uuid_found}/{total}")
    api_success = sum(
        1 for r in results
        for p in r.get("api_probes", [])
        if p.get("audio_url")
    )
    print(f"Simplecast API probes yielded audio URL: {api_success}")


def main():
    print("Selecting sample episodes …")
    sample = select_sample_episodes()
    controls = select_control_episodes()
    all_episodes = sample + controls

    print(f"\nTotal episodes to probe: {len(all_episodes)}")
    print(f"  Sample: {len(sample)}")
    print(f"  Controls: {len(controls)}")

    results = []
    for ep in all_episodes:
        r = process_episode(ep)
        results.append(r)

    # Save full results
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n\nFull diagnostic results saved to {OUTPUT_FILE}")

    print_summary(results)

    # Conclusions
    print("\n\n=== CONCLUSIONS ===")
    total = len(results)
    found_count = sum(1 for r in results if r["audio_found"])
    uuid_count = sum(1 for r in results if r["simplecast_uuids"])
    api_count = sum(
        1 for r in results
        for p in r.get("api_probes", [])
        if p.get("audio_url")
    )

    print(f"1. Success rate: {found_count}/{total}")
    by_year_counts = {}
    for r in results:
        if r["sample_label"] != "control":
            y = r["date"][:4]
            by_year_counts.setdefault(y, [0, 0])
            by_year_counts[y][1] += 1
            if r["audio_found"]:
                by_year_counts[y][0] += 1
    for y in sorted(by_year_counts):
        s, t = by_year_counts[y]
        print(f"   {y}: {s}/{t}")

    html_sufficient = all(
        r["extraction_method"] != "simplecast_api"
        for r in results if r["audio_found"]
    )
    print(f"2. Live NPR HTML alone sufficient: {html_sufficient}")
    methods = set(r["extraction_method"] for r in results if r["audio_found"])
    print(f"3. Extraction methods that worked: {methods or 'none'}")
    print(f"4. Simplecast UUIDs recoverable from HTML: {uuid_count}/{total}")
    print(f"   Simplecast API yielded audio: {api_count}")
    safe_to_scale = found_count >= total * 0.8
    print(f"5. Safe to scale to all 1,470 episodes: {'YES' if safe_to_scale else 'NEEDS_FALLBACK'}")
    controls_ok = all(
        r.get("control_rediscovered") is not False
        for r in results if r["sample_label"] == "control"
    )
    print(f"   Controls rediscovered known enclosures: {controls_ok}")
    print("6. Recommended fallback for failures: Wayback Machine CDX + Simplecast API UUID probe")


if __name__ == "__main__":
    main()
