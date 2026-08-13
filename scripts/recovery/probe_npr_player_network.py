#!/usr/bin/env python3
"""
NPR Player Network Observation Experiment
==========================================
Uses Playwright (headless Chromium) to load the NPR embed player and capture
every network request/response made during page load.

Targets
-------
Known-good control:
  story_id:  1106893731
  audio_id:  1198988689
  player URL: https://www.npr.org/player/embed/1106893731/1198988689
  known Simplecast UUID: e9827f64-db6e-4abb-aee9-a9fe394033ae

Unresolved target:
  story_id:  1104792247
  audio_id:  1198988717
  player URL: https://www.npr.org/player/embed/1104792247/1198988717

Output
------
  data/recovery/npr_player_network_probe.json   — full structured report
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_FILE = REPO_ROOT / "data" / "recovery" / "npr_player_network_probe.json"

TARGETS = [
    {
        "label": "known_good",
        "story_id": "1106893731",
        "audio_id": "1198988689",
        "player_url": "https://www.npr.org/player/embed/1106893731/1198988689",
        "known_simplecast_uuid": "e9827f64-db6e-4abb-aee9-a9fe394033ae",
    },
    {
        "label": "unresolved",
        "story_id": "1104792247",
        "audio_id": "1198988717",
        "player_url": "https://www.npr.org/player/embed/1104792247/1198988717",
        "known_simplecast_uuid": None,
    },
]

# Wait after initial page load to allow XHR/fetch to complete
PAGE_SETTLE_SECONDS = 10

# Hosts to always capture
RELEVANT_HOSTS = (
    "npr.org",
    "npr.net",
    "simplecast",
    "ondemand.npr",
    "podtrac",
    "byspotify",
)

# Content-type substrings that indicate relevant responses
RELEVANT_CONTENT_TYPES = (
    "json",
    "audio",
    "mpeg",
    "mp3",
    "m4a",
    "aac",
    "ogg",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
MP3_RE = re.compile(r"https?://[^\s\"'<>\\]+\.mp3(?:\?[^\s\"'<>\\]*)?", re.I)
M4A_RE = re.compile(r"https?://[^\s\"'<>\\]+\.m4a(?:\?[^\s\"'<>\\]*)?", re.I)
ONDEMAND_RE = re.compile(r"https?://ondemand\.npr\.org/[^\s\"'<>\\]+", re.I)


def _is_relevant_host(url: str) -> bool:
    return any(h in url for h in RELEVANT_HOSTS)


def _is_relevant_content_type(ct: str) -> bool:
    ct = (ct or "").lower()
    return any(s in ct for s in RELEVANT_CONTENT_TYPES)


def _search_body(body: str, story_id: str, audio_id: str, known_uuid: str | None) -> dict:
    """Return flags indicating what IDs/URLs appear in the response body."""
    found: dict[str, Any] = {
        "story_id_present": story_id in body,
        "audio_id_present": audio_id in body,
        "simplecast_uuid_present": bool(known_uuid and known_uuid.lower() in body.lower()),
        "mp3_urls": MP3_RE.findall(body)[:5],
        "m4a_urls": M4A_RE.findall(body)[:5],
        "ondemand_urls": ONDEMAND_RE.findall(body)[:5],
        "uuids_found": UUID_RE.findall(body)[:20],
    }
    return found


def _safe_json_loads(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _redact_truncate(text: str, max_len: int = 3000) -> str:
    if len(text) > max_len:
        return text[:max_len] + f"… [truncated {len(text) - max_len} chars]"
    return text


# ---------------------------------------------------------------------------
# Per-target probe using Playwright
# ---------------------------------------------------------------------------

def probe_target(target: dict, playwright_module: Any) -> dict:
    label = target["label"]
    player_url = target["player_url"]
    story_id = target["story_id"]
    audio_id = target["audio_id"]
    known_uuid = target.get("known_simplecast_uuid")

    print(f"\n{'='*70}")
    print(f"Probing [{label}]: {player_url}")
    print(f"{'='*70}")

    result: dict[str, Any] = {
        "label": label,
        "story_id": story_id,
        "audio_id": audio_id,
        "player_url": player_url,
        "known_simplecast_uuid": known_uuid,
        "status": None,
        "network_requests": [],
        "relevant_requests": [],
        "console_messages": [],
        "failed_requests": [],
        "dom_snapshot": None,
        "summary": {},
    }

    browser = playwright_module.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )
    page = context.new_page()

    # --- Console capture ---
    def on_console(msg):
        entry = {
            "type": msg.type,
            "text": msg.text[:500],
        }
        result["console_messages"].append(entry)

    page.on("console", on_console)

    # --- Request failure capture ---
    def on_request_failed(req):
        entry = {
            "url": req.url,
            "method": req.method,
            "failure": req.failure,
        }
        result["failed_requests"].append(entry)
        if _is_relevant_host(req.url):
            print(f"  [FAILED] {req.method} {req.url}  failure={req.failure}")

    page.on("requestfailed", on_request_failed)

    # --- Network interception ---
    captured_responses: list[dict] = []

    def on_response(response):
        url = response.url
        status = response.status
        headers = dict(response.headers)
        content_type = headers.get("content-type", "")
        method = response.request.method

        entry: dict[str, Any] = {
            "url": url,
            "method": method,
            "status": status,
            "content_type": content_type,
            "request_headers": {
                k: v for k, v in dict(response.request.headers).items()
                if k.lower() in (
                    "accept", "origin", "referer", "x-forwarded-for",
                    "authorization", "content-type"
                )
            },
            "response_headers": {
                k: v for k, v in headers.items()
                if k.lower() in (
                    "content-type", "content-length", "location",
                    "cache-control", "x-powered-by", "access-control-allow-origin"
                )
            },
            "body_text": None,
            "body_flags": {},
            "is_relevant_host": _is_relevant_host(url),
            "is_relevant_content_type": _is_relevant_content_type(content_type),
        }

        # Try to read body for relevant responses
        if entry["is_relevant_host"] or entry["is_relevant_content_type"]:
            try:
                body_bytes = response.body()
                body_text = body_bytes.decode("utf-8", errors="replace")
                entry["body_text"] = _redact_truncate(body_text)
                entry["body_flags"] = _search_body(body_text, story_id, audio_id, known_uuid)
            except Exception as exc:
                entry["body_read_error"] = str(exc)

        captured_responses.append(entry)

        if entry["is_relevant_host"] or entry["is_relevant_content_type"]:
            flags = entry.get("body_flags", {})
            print(
                f"  [{status}] {method} {url[:120]}"
                f"  ct={content_type[:40]}"
                f"  story={flags.get('story_id_present', '?')}"
                f"  audio={flags.get('audio_id_present', '?')}"
                f"  uuid={flags.get('simplecast_uuid_present', '?')}"
                f"  mp3={len(flags.get('mp3_urls', []))}"
                f"  uuids={len(flags.get('uuids_found', []))}"
            )

    page.on("response", on_response)

    # --- Load the page ---
    try:
        page.goto(player_url, timeout=30_000, wait_until="networkidle")
        result["status"] = "loaded"
    except Exception as exc:
        result["status"] = f"load_error: {exc}"
        print(f"  Page load error: {exc}")

    # Allow additional XHR/fetch to settle
    time.sleep(PAGE_SETTLE_SECONDS)

    # --- DOM snapshot ---
    try:
        result["dom_snapshot"] = _redact_truncate(page.content(), 5000)
    except Exception as exc:
        result["dom_snapshot"] = f"error: {exc}"

    page.close()
    context.close()
    browser.close()

    # --- Classify captured responses ---
    result["network_requests"] = captured_responses
    relevant = [
        r for r in captured_responses
        if r["is_relevant_host"] or r["is_relevant_content_type"]
    ]
    result["relevant_requests"] = relevant

    # --- Build summary ---
    uuid_source_request = None
    audio_url_source_request = None
    all_uuids: list[str] = []
    all_mp3s: list[str] = []
    all_ondemand: list[str] = []

    for req in relevant:
        flags = req.get("body_flags", {})
        uuids = flags.get("uuids_found", [])
        mp3s = flags.get("mp3_urls", [])
        ondemand = flags.get("ondemand_urls", [])
        all_uuids.extend(uuids)
        all_mp3s.extend(mp3s)
        all_ondemand.extend(ondemand)
        if known_uuid and flags.get("simplecast_uuid_present"):
            if uuid_source_request is None:
                uuid_source_request = req["url"]
        if mp3s or ondemand:
            if audio_url_source_request is None:
                audio_url_source_request = req["url"]

    # Deduplicate
    seen: set[str] = set()
    unique_uuids = [u for u in all_uuids if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]
    seen = set()
    unique_mp3s = [u for u in all_mp3s if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]
    seen = set()
    unique_ondemand = [u for u in all_ondemand if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

    result["summary"] = {
        "total_network_requests": len(captured_responses),
        "relevant_request_count": len(relevant),
        "known_uuid_found": bool(known_uuid and any(
            r.get("body_flags", {}).get("simplecast_uuid_present")
            for r in relevant
        )),
        "uuid_source_endpoint": uuid_source_request,
        "audio_url_found": bool(unique_mp3s or unique_ondemand),
        "audio_url_source_endpoint": audio_url_source_request,
        "all_uuids_seen": unique_uuids[:30],
        "all_mp3_urls_seen": unique_mp3s[:10],
        "all_ondemand_urls_seen": unique_ondemand[:10],
        "console_error_count": sum(
            1 for m in result["console_messages"] if m["type"] == "error"
        ),
        "failed_request_count": len(result["failed_requests"]),
    }

    print(f"\n  Summary for [{label}]:")
    for k, v in result["summary"].items():
        print(f"    {k}: {v}")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_report(results: list[dict]) -> dict:
    known = next((r for r in results if r["label"] == "known_good"), {})
    unresolved = next((r for r in results if r["label"] == "unresolved"), {})

    known_summary = known.get("summary", {})
    unresolved_summary = unresolved.get("summary", {})

    # Find the exact player data endpoint (the one that sourced the UUID or audio URL)
    exact_endpoint = (
        known_summary.get("uuid_source_endpoint")
        or known_summary.get("audio_url_source_endpoint")
    )

    # For the unresolved target, find any Simplecast UUID
    unresolved_uuids = unresolved_summary.get("all_uuids_seen", [])
    unresolved_uuid_found = bool(unresolved_uuids)
    unresolved_audio_found = bool(
        unresolved_summary.get("all_mp3_urls_seen")
        or unresolved_summary.get("all_ondemand_urls_seen")
    )

    # Gather full request detail for exact endpoint
    exact_endpoint_details = None
    if exact_endpoint:
        for req in known.get("relevant_requests", []):
            if req["url"] == exact_endpoint:
                exact_endpoint_details = req
                break

    return {
        "method": "playwright-headless-browser-network-observation",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "KNOWN-GOOD NETWORK TRACE": known.get("relevant_requests", []),
        "EXACT PLAYER DATA ENDPOINT": exact_endpoint,
        "EXACT PLAYER DATA ENDPOINT DETAILS": exact_endpoint_details,
        "HTTP METHOD / PARAMETERS": (
            {
                "method": exact_endpoint_details["method"],
                "url": exact_endpoint_details["url"],
                "request_headers": exact_endpoint_details.get("request_headers"),
            }
            if exact_endpoint_details
            else None
        ),
        "KNOWN-GOOD UUID SOURCE": known_summary.get("uuid_source_endpoint"),
        "UNRESOLVED NETWORK TRACE": unresolved.get("relevant_requests", []),
        "JUNE 13 UUID FOUND": "YES" if unresolved_uuid_found else "NO",
        "JUNE 13 UUID VALUE": unresolved_uuids[:5] if unresolved_uuid_found else None,
        "JUNE 13 AUDIO URL FOUND": "YES" if unresolved_audio_found else "NO",
        "JUNE 13 AUDIO URLS": (
            unresolved_summary.get("all_mp3_urls_seen", [])
            + unresolved_summary.get("all_ondemand_urls_seen", [])
        )[:5],
        "PLAYABILITY VALIDATION": {
            "known_good_uuid_confirmed": known_summary.get("known_uuid_found"),
            "known_good_audio_url_found": known_summary.get("audio_url_found"),
            "unresolved_uuid_found": unresolved_uuid_found,
            "unresolved_audio_url_found": unresolved_audio_found,
        },
        "MINIMAL API-BASED RECOVERY CHANGE": (
            "If the exact endpoint URL and parameters are confirmed, a plain "
            "urllib/requests call to that endpoint with the audio_id should "
            "be sufficient to retrieve the Simplecast UUID and audio URL — "
            "no headless browser required for production recovery."
            if (exact_endpoint and unresolved_audio_found)
            else "Endpoint not yet confirmed or unresolved target returned no audio."
        ),
        "HEADLESS BROWSER STILL REQUIRED AFTER DISCOVERY": (
            "NO" if (exact_endpoint and unresolved_audio_found) else "YES"
        ),
        "per_target_summaries": {
            r["label"]: r["summary"] for r in results
        },
        "console_messages": {
            r["label"]: r.get("console_messages", []) for r in results
        },
        "failed_requests": {
            r["label"]: r.get("failed_requests", []) for r in results
        },
    }


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright is not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    results = []
    with sync_playwright() as pw:
        for target in TARGETS:
            result = probe_target(target, pw)
            results.append(result)

    report = build_report(results)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    print(f"\n{'='*70}")
    print("NPR Player Network Probe complete")
    print(f"Output: {OUTPUT_FILE}")
    print()
    print("=== FINAL REPORT HEADINGS ===")
    for heading in (
        "EXACT PLAYER DATA ENDPOINT",
        "KNOWN-GOOD UUID SOURCE",
        "JUNE 13 UUID FOUND",
        "JUNE 13 AUDIO URL FOUND",
        "HEADLESS BROWSER STILL REQUIRED AFTER DISCOVERY",
    ):
        print(f"  {heading}: {report.get(heading)}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
