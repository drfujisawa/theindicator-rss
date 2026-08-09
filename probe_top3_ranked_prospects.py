#!/usr/bin/env python3
"""
Investigate the top 3 ranked prospects from
indicator_identity_audio_unresolved_ranked_report.json.

For each episode the script:
 - loads all existing evidence from the consolidated ledger and ranked report;
 - probes current NPR story/player URLs where credible;
 - queries Wayback CDX for NPR story/player captures and relevant
   affiliate captures;
 - inspects archived HTML for canonical NPR URLs, hidden nprStoryId,
   player story IDs, audio IDs, player embeds, and NPR-hosted audio;
 - probes only episode-specific ondemand.npr.org / NPR-hosted audio candidates;
 - validates HTTP status, explicit audio MIME type, Indicator path, and
   episode-specific identity chain;
 - rejects livestreams, StreamTheWorld, generic audio, unrelated NPR pages,
   and filename-only guesses;
 - preserves rejected candidates and failure reasons in the output.

Writes:
  top3_prospect_<YYYY-MM-DD>_diag.json   — one per episode
  top3_prospects_summary.json            — small summary
"""

import html
import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent

RANKED_REPORT_FILE = "indicator_identity_audio_unresolved_ranked_report.json"
LEDGER_FILE = "indicator_unresolved_consolidated_evidence_ledger.json"

SUMMARY_OUTPUT = "top3_prospects_summary.json"

TARGET_RANKS = {1, 2, 3}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IndicatorTop3ProspectProbe/1.0)"
    )
}

TIMEOUT = 30
RETRIES = 3

WAYBACK_CDX_LIMIT = 50
MAX_WAYBACK_CAPTURES = 10
MAX_PLAYER_PAGES = 15
MAX_AUDIO_CANDIDATES = 60

# Patterns that indicate a generic / live / non-Indicator audio source that
# must be rejected even if the MIME type is audio/*.
REJECTED_PATTERNS = [
    r"streamtheworld",
    r"live\.npr\.org",
    r"/stream/",
    r"/livestream",
    r"tunein\.com",
    r"icecast",
    r"shoutcast",
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch(url, max_bytes=3_000_000, range_request=False):
    headers = dict(HEADERS)
    if range_request:
        headers["Range"] = "bytes=0-4095"

    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=TIMEOUT) as resp:
                data = resp.read(4096 if range_request else max_bytes)
                return {
                    "status_code": getattr(resp, "status", None),
                    "final_url": resp.geturl(),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "data": data,
                }
        except Exception as exc:
            last_error = exc
            if attempt < RETRIES:
                time.sleep(attempt * 2)

    raise last_error


def fetch_text(url):
    resp = fetch(url)
    resp["text"] = resp["data"].decode("utf-8", errors="replace")
    return resp


# ---------------------------------------------------------------------------
# Text / HTML extraction helpers
# ---------------------------------------------------------------------------

def clean_text(value):
    """HTML-unescape and normalise whitespace for regex searching."""
    value = html.unescape(value)
    value = value.replace("\\/", "/")
    return value


def unique(values):
    seen = set()
    result = []
    for v in values:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def extract_npr_story_urls(page):
    page = clean_text(page)
    pattern = (
        r'https?://(?:www\.)?npr\.org/'
        r'(?:\d{4}/\d{2}/\d{2}/\d+/[^"\'<>\s\\]+|'
        r'sections?/[^"\'<>\s\\]+|'
        r'templates?/[^"\'<>\s\\]+|'
        r'player/embed/[^"\'<>\s\\]+|'
        r'transcripts?/[^"\'<>\s\\]+)'
    )
    return unique(re.findall(pattern, page, re.I))


def extract_player_embeds(page):
    page = clean_text(page)
    pattern = r'https?://(?:www\.)?npr\.org/player/embed/(\d+)/(\d+)'
    pairs = re.findall(pattern, page, re.I)
    urls = [f"https://www.npr.org/player/embed/{s}/{a}" for s, a in pairs]
    return unique(urls)


def extract_numeric_ids(page):
    page = clean_text(page)
    # Long numeric IDs likely to be NPR story/audio IDs (7-12 digits)
    hits = re.findall(r'\b(\d{7,12})\b', page)
    return unique(hits)


def extract_audio_urls(page):
    page = clean_text(page)
    patterns = [
        r'https?://ondemand\.npr\.org/[^"\'<>\s\\]+',
        r'https?://prfx\.byspotify\.com/[^"\'<>\s\\]+',
        r'https?://play\.podtrac\.com/[^"\'<>\s\\]+',
        r'https?://[^"\'<>\s\\]+\.mp3(?:\?[^"\'<>\s\\]*)?',
    ]
    found = []
    for pat in patterns:
        found.extend(re.findall(pat, page, re.I))
    return unique(found)


def extract_interesting_lines(page, title_keywords=None):
    page = clean_text(page)
    markers = [
        "npr.org", "ondemand.npr.org", "player/embed", ".mp3",
        "podtrac", "byspotify", "audio", "iframe", "story", "episode",
        "indicator",
    ]
    if title_keywords:
        markers.extend(kw.lower() for kw in title_keywords)

    lines = []
    for line in page.splitlines():
        lower = line.lower()
        if any(m in lower for m in markers):
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                lines.append(line[:8000])
    return unique(lines)[:300]


# ---------------------------------------------------------------------------
# Audio validation
# ---------------------------------------------------------------------------

def is_generic_reject(url):
    """Return True if the URL matches a known live/generic stream pattern."""
    lower = url.lower()
    return any(re.search(pat, lower) for pat in REJECTED_PATTERNS)


def is_indicator_path(final_url):
    """Return True only if the resolved URL contains /indicator/."""
    return "/indicator/" in final_url.lower()


def is_episode_specific(final_url, reference_date):
    """
    Return True if the resolved audio URL contains the episode date (YYYY/MM/DD
    or YYYYMMDD variant) so we can link it back to the target episode.
    """
    date_parts = reference_date.split("-")  # ['YYYY', 'MM', 'DD']
    if len(date_parts) != 3:
        return False
    slash_date = "/".join(date_parts)           # YYYY/MM/DD
    compact_date = "".join(date_parts)          # YYYYMMDD
    lower = final_url.lower()
    return slash_date in lower or compact_date in lower


def validate_audio_candidate(url, reference_date):
    """
    Validate a single audio URL.  Returns a dict with validation_status
    and structured reason fields.
    """
    result = {
        "candidate_url": url,
        "validation_status": None,
        "reason": None,
        "final_url": None,
        "status_code": None,
        "content_type": None,
        "is_indicator_path": False,
        "is_episode_specific": False,
        "valid_npr_indicator_audio": False,
    }

    # Pre-flight: reject obvious generic patterns without a network call
    if is_generic_reject(url):
        result["validation_status"] = "rejected_generic_stream"
        result["reason"] = "URL matches a live-stream or generic-audio pattern."
        return result

    # Pre-flight: require ondemand.npr.org or a credible NPR host
    lower = url.lower()
    if "ondemand.npr.org" not in lower and "npr.org" not in lower:
        result["validation_status"] = "rejected_non_npr"
        result["reason"] = "Not an NPR-hosted audio URL."
        return result

    try:
        resp = fetch(url, range_request=True)
    except Exception as exc:
        result["validation_status"] = "rejected_request_error"
        result["reason"] = str(exc)
        return result

    final_url = resp.get("final_url") or ""
    content_type = (resp.get("content_type") or "").lower()
    status_code = resp.get("status_code")

    result["final_url"] = final_url
    result["status_code"] = status_code
    result["content_type"] = resp.get("content_type")

    if status_code and status_code >= 400:
        result["validation_status"] = "rejected_http_error"
        result["reason"] = f"HTTP {status_code}"
        return result

    if not content_type.startswith("audio/"):
        result["validation_status"] = "rejected_wrong_content_type"
        result["reason"] = f"Content-Type is '{content_type}', not audio/*."
        return result

    ind = is_indicator_path(final_url)
    ep = is_episode_specific(final_url, reference_date)
    result["is_indicator_path"] = ind
    result["is_episode_specific"] = ep

    if not ind:
        result["validation_status"] = "rejected_not_indicator_path"
        result["reason"] = (
            "Final URL does not contain /indicator/ — likely ATC, ME, or "
            "another NPR programme."
        )
        return result

    if not ep:
        result["validation_status"] = "rejected_not_episode_specific"
        result["reason"] = (
            f"Final URL does not contain the episode date "
            f"({reference_date}) — cannot link to this specific episode."
        )
        return result

    result["validation_status"] = "validated"
    result["valid_npr_indicator_audio"] = True
    return result


# ---------------------------------------------------------------------------
# Wayback CDX + capture probing
# ---------------------------------------------------------------------------

def wayback_cdx(url, limit=WAYBACK_CDX_LIMIT):
    query = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={quote(url, safe='')}"
        "&output=json"
        "&filter=statuscode:200"
        "&collapse=digest"
        "&fl=timestamp,original,statuscode,mimetype"
        f"&limit={limit}"
    )
    try:
        resp = fetch_text(query)
        data = json.loads(resp["text"])
        if not isinstance(data, list) or len(data) < 2:
            return []
        header = data[0]
        return [dict(zip(header, row)) for row in data[1:]]
    except Exception:
        return []


def probe_wayback_url(url, title_keywords=None):
    """Return a list of capture dicts (with parsed HTML fields)."""
    rows = wayback_cdx(url)
    captures = []
    for row in rows[:MAX_WAYBACK_CAPTURES]:
        timestamp = row.get("timestamp")
        original = row.get("original")
        if not timestamp or not original:
            continue
        archive_url = (
            f"https://web.archive.org/web/{timestamp}id_/{original}"
        )
        item = {
            "timestamp": timestamp,
            "original": original,
            "archive_url": archive_url,
            "status": None,
        }
        try:
            resp = fetch_text(archive_url)
            page = resp["text"]
            item["status"] = "fetched"
            item["npr_story_urls"] = extract_npr_story_urls(page)
            item["player_embeds"] = extract_player_embeds(page)
            item["numeric_ids"] = extract_numeric_ids(page)
            item["audio_candidates"] = extract_audio_urls(page)
            item["interesting_lines"] = extract_interesting_lines(
                page, title_keywords
            )
        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)
        captures.append(item)
    return captures


# ---------------------------------------------------------------------------
# Per-episode investigation
# ---------------------------------------------------------------------------

def investigate_episode(episode, ledger):
    """
    Run a full investigation for one ranked episode.
    Returns a diagnostic dict and a list of output filenames to write.
    """
    reference_date = episode["reference_date"]
    reference_title = episode["reference_title"]
    rank = episode["rank"]

    # Title keywords for interesting-line extraction
    title_keywords = [
        w for w in re.split(r'\W+', reference_title) if len(w) > 3
    ]

    # Load any pre-existing evidence from the ledger
    ledger_entry = ledger.get(reference_date, {})

    diag = {
        "method": "top3-ranked-prospect-deep-investigation",
        "rank": rank,
        "reference_date": reference_date,
        "reference_title": reference_title,
        "reference_episode": episode.get("reference_episode"),
        "identity_confidence_from_ranked_report": episode.get(
            "identity_confidence"
        ),
        "strongest_evidence_from_ranked_report": episode.get(
            "strongest_evidence"
        ),
        "known_npr_ids": episode.get("npr_ids_found", {}),
        "known_story_urls": episode.get("npr_story_urls", []),
        "known_player_urls": episode.get("player_urls", []),
        "known_candidate_audio_urls": [
            c["url"] for c in episode.get("candidate_audio_urls", [])
        ],
        "known_archived_captures": episode.get("archived_captures", []),
        "remaining_recovery_avenues_from_ranked_report": episode.get(
            "remaining_recovery_avenues", []
        ),
        "ledger_snapshot": ledger_entry,
        # Investigation results
        "live_story_probes": [],
        "live_player_probes": [],
        "wayback_story_captures": [],
        "wayback_player_captures": [],
        "wayback_affiliate_captures": [],
        "audio_candidates_discovered": [],
        "audio_candidates_tested": [],
        "validated_audio": [],
        "validation_summary": None,
        "final_classification": None,
    }

    # ------------------------------------------------------------------
    # Collect seed URLs from the ranked-report entry
    # ------------------------------------------------------------------
    story_urls = list(episode.get("npr_story_urls", []))
    player_urls = list(episode.get("player_urls", []))
    affiliate_pages = list(episode.get("affiliate_pages", []))
    seed_audio = [c["url"] for c in episode.get("candidate_audio_urls", [])]

    # Track all audio candidates found during this run
    all_audio = list(seed_audio)
    all_player_urls = list(player_urls)

    # ------------------------------------------------------------------
    # Probe live NPR story pages
    # ------------------------------------------------------------------
    for url in story_urls[:5]:
        probe = {"url": url, "status": None}
        try:
            resp = fetch_text(url)
            page = resp["text"]
            probe["status"] = "fetched"
            probe["final_url"] = resp["final_url"]
            probe["player_embeds"] = extract_player_embeds(page)
            probe["npr_story_urls"] = extract_npr_story_urls(page)
            probe["numeric_ids"] = extract_numeric_ids(page)
            probe["audio_candidates"] = extract_audio_urls(page)
            probe["interesting_lines"] = extract_interesting_lines(
                page, title_keywords
            )
            all_audio.extend(probe["audio_candidates"])
            all_player_urls.extend(probe["player_embeds"])
        except Exception as exc:
            probe["status"] = "error"
            probe["error"] = str(exc)
        diag["live_story_probes"].append(probe)

    # ------------------------------------------------------------------
    # Probe live NPR player pages
    # ------------------------------------------------------------------
    for url in unique(all_player_urls)[:MAX_PLAYER_PAGES]:
        probe = {"url": url, "status": None}
        try:
            resp = fetch_text(url)
            page = resp["text"]
            probe["status"] = "fetched"
            probe["final_url"] = resp["final_url"]
            probe["player_embeds"] = extract_player_embeds(page)
            probe["numeric_ids"] = extract_numeric_ids(page)
            probe["audio_candidates"] = extract_audio_urls(page)
            probe["interesting_lines"] = extract_interesting_lines(
                page, title_keywords
            )
            all_audio.extend(probe["audio_candidates"])
        except Exception as exc:
            probe["status"] = "error"
            probe["error"] = str(exc)
        diag["live_player_probes"].append(probe)

    # ------------------------------------------------------------------
    # Wayback CDX + capture for story URLs
    # ------------------------------------------------------------------
    for url in story_urls[:5]:
        captures = probe_wayback_url(url, title_keywords)
        diag["wayback_story_captures"].extend(captures)
        for cap in captures:
            all_audio.extend(cap.get("audio_candidates", []))
            all_player_urls.extend(cap.get("player_embeds", []))

    # ------------------------------------------------------------------
    # Wayback CDX + capture for player URLs
    # ------------------------------------------------------------------
    for url in unique(all_player_urls)[:MAX_PLAYER_PAGES]:
        captures = probe_wayback_url(url, title_keywords)
        diag["wayback_player_captures"].extend(captures)
        for cap in captures:
            all_audio.extend(cap.get("audio_candidates", []))

    # ------------------------------------------------------------------
    # Wayback CDX + capture for affiliate pages
    # ------------------------------------------------------------------
    for url in affiliate_pages[:5]:
        captures = probe_wayback_url(url, title_keywords)
        diag["wayback_affiliate_captures"].extend(captures)
        for cap in captures:
            all_audio.extend(cap.get("audio_candidates", []))
            all_player_urls.extend(cap.get("player_embeds", []))

    # ------------------------------------------------------------------
    # Also query Wayback for NPR sections/theindicator on the target date
    # ------------------------------------------------------------------
    indicator_section_url = (
        f"https://www.npr.org/sections/theindicator/{reference_date}"
        f"-{_date_slug(reference_title)}"
    )
    section_captures = probe_wayback_url(indicator_section_url, title_keywords)
    diag["wayback_story_captures"].extend(section_captures)
    for cap in section_captures:
        all_audio.extend(cap.get("audio_candidates", []))
        all_player_urls.extend(cap.get("player_embeds", []))

    # ------------------------------------------------------------------
    # Deduplicate discovered audio
    # ------------------------------------------------------------------
    all_audio = unique(all_audio)
    diag["audio_candidates_discovered"] = all_audio

    # ------------------------------------------------------------------
    # Validate audio candidates
    # ------------------------------------------------------------------
    tested = []
    validated = []
    for candidate_url in all_audio[:MAX_AUDIO_CANDIDATES]:
        check = validate_audio_candidate(candidate_url, reference_date)
        tested.append(check)
        if check.get("valid_npr_indicator_audio"):
            validated.append(check)

    diag["audio_candidates_tested"] = tested
    diag["validated_audio"] = validated

    # ------------------------------------------------------------------
    # Final classification
    # ------------------------------------------------------------------
    if validated:
        diag["final_classification"] = "recovered"
        diag["validation_summary"] = (
            f"RECOVERED: {len(validated)} validated NPR Indicator audio "
            f"file(s) confirmed for {reference_date}."
        )
    else:
        diag["final_classification"] = "identity_found_but_audio_unresolved"
        rejection_reasons = list({
            t["validation_status"] for t in tested if t["validation_status"]
        })
        diag["validation_summary"] = (
            f"No valid NPR Indicator audio confirmed. "
            f"Tested {len(tested)} candidates. "
            f"Rejection reasons: {', '.join(rejection_reasons) or 'none'}."
        )

    return diag


def _date_slug(title):
    """Convert a title to a URL-safe lower-kebab slug (best-effort)."""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug[:60]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def load_sources():
    """Load the ranked report and the evidence ledger from disk."""
    with open(BASE_DIR / RANKED_REPORT_FILE, encoding="utf-8") as fh:
        ranked_report = json.load(fh)

    try:
        with open(BASE_DIR / LEDGER_FILE, encoding="utf-8") as fh:
            ledger = json.load(fh)
    except FileNotFoundError:
        ledger = {}

    return ranked_report, ledger


def build_episode_output_filename(episode):
    return f"top3_prospect_{episode['reference_date']}_diag.json"


def run_investigation():
    ranked_report, ledger = load_sources()

    episodes = ranked_report.get("episodes", [])
    top3 = [e for e in episodes if e.get("rank") in TARGET_RANKS]
    top3.sort(key=lambda e: e["rank"])

    summary = {
        "method": "top3-ranked-prospect-investigation-summary",
        "generated_at": _now_iso(),
        "source_ranked_report": RANKED_REPORT_FILE,
        "target_ranks": sorted(TARGET_RANKS),
        "episodes_investigated": [],
    }

    for episode in top3:
        print(
            f"\n{'='*60}\n"
            f"Investigating rank {episode['rank']}: "
            f"{episode['reference_date']} — {episode['reference_title']}\n"
            f"{'='*60}"
        )

        diag = investigate_episode(episode, ledger)

        out_file = build_episode_output_filename(episode)
        out_path = BASE_DIR / out_file
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(diag, fh, indent=2, ensure_ascii=False)

        print(
            f"  → Written: {out_file}\n"
            f"  → Classification: {diag['final_classification']}\n"
            f"  → {diag['validation_summary']}"
        )

        summary["episodes_investigated"].append({
            "rank": episode["rank"],
            "reference_date": episode["reference_date"],
            "reference_title": episode["reference_title"],
            "final_classification": diag["final_classification"],
            "validated_audio_count": len(diag["validated_audio"]),
            "candidates_tested": len(diag["audio_candidates_tested"]),
            "output_file": out_file,
            "validation_summary": diag["validation_summary"],
        })

    summary_path = BASE_DIR / SUMMARY_OUTPUT
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)

    print(f"\nSummary written: {SUMMARY_OUTPUT}")
    return summary


def _now_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


if __name__ == "__main__":
    run_investigation()
