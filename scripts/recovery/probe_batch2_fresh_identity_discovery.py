#!/usr/bin/env python3
"""
Batch 2: Fresh NPR identity discovery for the next nine unresolved Indicator
episodes (ranks 4–12 from indicator_identity_audio_unresolved_ranked_report.json):

  1. 2018-10-09 — "China's Social Credit System"          (rank 4)
  2. 2018-10-11 — "China's Brave New World"               (rank 5)
  3. 2018-07-23 — "Google's Mobile Monopoly"              (rank 6)
  4. 2018-04-26 — "California's Housing Conundrum"        (rank 7)
  5. 2018-08-17 — "Donald Trump's Economic Strategy... Maybe?" (rank 8)
  6. 2018-10-05 — "Who's Hiring?"                         (rank 9)
  7. 2018-04-24 — "When China's Ships Come In"            (rank 10)
  8. 2018-06-21 — "Teenage (Employment) Wasteland"        (rank 11)
  9. 2018-06-13 — "Dude, Where's My Trade War?"           (rank 12)

The first batch (probe_fresh_identity_discovery.py) successfully recovered:
  - 2018-07-11 "Fed Accounts For All!" (merged in PR #7)
  - 2018-08-10 "Privacy Please: Why Public Companies Go Private (Or Vice Versa)"
  - 2018-09-24 "Saudi Arabia & The Paradox of Plenty"

Ranks 1–3 are already recovered; the consolidated audit
(indicator_identity_audio_unresolved_ranked_report.json) contains exactly 9
remaining identity_found_but_audio_unresolved episodes (ranks 4–12, none
marked as probable_duplicate_rebroadcast).

This script applies exactly the same proven method to the next batch.

Strategy
--------
1. Issue a small, date-bounded set of CDX queries over NPR's dated story URL
   structures (including /YYYY/MM/DD/ forms and section wrappers).
2. Score returned original URLs locally using title/slug/date similarity without
   fetching them all.
3. Fetch only the top few candidate captures and require an episode-specific
   proof chain: strong title match, date evidence, same-page NPR story URL/story
   ID, and trusted provenance.
4. Only after identity is confirmed, probe live and archived NPR player pages
   and validate audio using the existing Indicator validator rules:
     - explicit audio/* MIME type;
     - final URL must be NPR-controlled and contain /indicator/;
     - unrelated/sidebar/related-story audio cannot anchor a recovery;
     - filename similarity, 510325, or generic Indicator context are never
       enough on their own.
5. Always write fresh placeholder diagnostics first; only overwrite them with
   run_complete results after the full probe finishes.

Writes (per episode):
  batch2_fresh_identity_discovery_<YYYY-MM-DD>_diag.json

Writes (summary):
  batch2_fresh_identity_discovery_summary.json
"""

import argparse
import datetime
import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = REPO_ROOT / "data" / "recovery"

SUMMARY_OUTPUT = str(OUTPUT_DIR / "batch2_fresh_identity_discovery_summary.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IndicatorFreshIdentityProbe/1.0)"
    )
}

REQUEST_TIMEOUT_SECONDS = 8
WAYBACK_DISCOVERY_RETRIES = 2  # additional retries after the initial attempt
CONTENT_RETRIES = 1  # additional retries after the initial attempt
RETRY_DELAY = 1  # seconds between retries

# CDX / fetch funnel caps.
CDX_DATE_WINDOW_LIMIT = 40
CDX_EXACT_LIMIT = 5
CDX_MATCH_TYPE = "prefix"  # Wayback CDX explicit prefix matching — no mid-path wildcards
MAX_STAGE_A_CDX_QUERIES = 6

# Known-good archived NPR URLs used to self-test CDX connectivity before a run.
# Both have been proven to return captures in repository-validated probes.
CDX_SELF_TEST_URLS = [
    # Paranormal Profits story page: 24 known captures (indicator_wayback_npr_probe.json)
    "https://www.npr.org/sections/money/2018/10/31/662708285/paranormal-profits",
    # Paranormal Profits player: 3 known captures (indicator_multi_archive_player_probe.json)
    "https://www.npr.org/player/embed/662706955/662707862",
]
MAX_FETCHED_CAPTURES = 4
MAX_PLAYER_PAGES = 2
MAX_PLAYER_CDX_LOOKUPS = 2
MAX_ARCHIVED_PLAYER_FETCHES = 2
MAX_AUDIO_CANDIDATES = 4
FETCH_SCORE_THRESHOLD = 0.35

# Trusted NPR story-page URL structures observed in repository-validated
# recoveries/current history. Only these forms may anchor confirmed identity:
#   - https://www.npr.org/YYYY/MM/DD/<story-id>/<slug>
#   - https://www.npr.org/sections/money/YYYY/MM/DD/<story-id>/<slug>
TRUSTED_STORY_PAGE_PATTERNS = (
    re.compile(
        r"^https?://(?:www\.)?npr\.org/\d{4}/\d{2}/\d{2}/(\d{7,12})/[^/?#]+/?(?:[?#].*)?$",
        re.I,
    ),
    re.compile(
        r"^https?://(?:www\.)?npr\.org/sections/money/\d{4}/\d{2}/\d{2}/(\d{7,12})/[^/?#]+/?(?:[?#].*)?$",
        re.I,
    ),
)

# Numeric ID bounds remain advisory metadata only. The sparse probe is disabled
# in this workflow so it cannot expand request volume or establish identity.
NUMERIC_PROBE_STEP = 50_000
NUMERIC_PROBE_MAX = 0

# Patterns indicating a generic / live / non-Indicator audio source.
REJECTED_PATTERNS = [
    r"streamtheworld",
    r"live\.npr\.org",
    r"/stream/",
    r"/livestream",
    r"tunein\.com",
    r"icecast",
    r"shoutcast",
    r"traffic\.megaphone\.fm",
    r"chrt\.fm",
    r"tracking\.swap\.fm",
]

# ---------------------------------------------------------------------------
# Target episodes with adjacent validated story IDs for numeric bounding.
# All adjacent IDs come from indicator_npr_audio_validation.json (checked in).
# ---------------------------------------------------------------------------

TARGETS = [
    {
        "reference_date": "2018-10-09",
        "reference_title": "China's Social Credit System",
        "reference_episode": 143,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-10-08 e=655629329 (The Iron Lotus)
        #   2018-10-12 e=657017860 (The Economics of Apologies)
        # Note: 2018-10-11 is another unresolved episode that shares the same
        # numeric window, so IDs in this range may belong to either China episode.
        "id_lower_bound": 655_629_329,   # e from 2018-10-08 (validated)
        "id_upper_bound": 657_017_860,   # e from 2018-10-12 (validated)
        "id_lower_episode": "2018-10-08",
        "id_upper_episode": "2018-10-12",
        # NPR section paths that may have hosted an Indicator story page
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        # Slug variants derived from the title (best-effort; not assumed correct)
        "slug_variants": [
            "china-social-credit-system",
            "social-credit-system",
            "china-social-credit",
            "chinas-social-credit-system",
        ],
    },
    {
        "reference_date": "2018-10-11",
        "reference_title": "China's Brave New World",
        "reference_episode": 145,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-10-08 e=655629329 (The Iron Lotus)
        #   2018-10-12 e=657017860 (The Economics of Apologies)
        # Note: 2018-10-09 is another unresolved episode that shares this window;
        # a distinct page/player ID is required to separate the two China episodes.
        "id_lower_bound": 655_629_329,   # e from 2018-10-08 (validated)
        "id_upper_bound": 657_017_860,   # e from 2018-10-12 (validated)
        "id_lower_episode": "2018-10-08",
        "id_upper_episode": "2018-10-12",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "chinas-brave-new-world",
            "china-brave-new-world",
            "brave-new-world",
            "china-social-control",
        ],
    },
    {
        "reference_date": "2018-07-23",
        "reference_title": "Google's Mobile Monopoly",
        "reference_episode": 91,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-07-20 e=630941346 (The Market For Air)
        #   2018-07-24 e=632083181 (Trump Vs. The Fed, Or Trump Vs... Trump?)
        "id_lower_bound": 630_941_346,   # e from 2018-07-20 (validated)
        "id_upper_bound": 632_083_181,   # e from 2018-07-24 (validated)
        "id_lower_episode": "2018-07-20",
        "id_upper_episode": "2018-07-24",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "googles-mobile-monopoly",
            "google-mobile-monopoly",
            "google-monopoly",
            "google-antitrust",
        ],
    },
    {
        "reference_date": "2018-04-26",
        "reference_title": "California's Housing Conundrum",
        "reference_episode": 33,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-04-25 e=605819103 (The Farm Labor Drought — story_id)
        #   2018-04-27 e=606586601 (The Homeless Count — story_id)
        "id_lower_bound": 605_819_103,   # story_id from 2018-04-25 (validated)
        "id_upper_bound": 606_586_601,   # story_id from 2018-04-27 (validated)
        "id_lower_episode": "2018-04-25",
        "id_upper_episode": "2018-04-27",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "californias-housing-conundrum",
            "california-housing-conundrum",
            "california-housing",
            "housing-conundrum",
        ],
    },
    {
        "reference_date": "2018-08-17",
        "reference_title": "Donald Trump's Economic Strategy... Maybe?",
        "reference_episode": 112,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-08-16 e=639346300 (validated)
        #   2018-08-20 e=640319086 (validated)
        "id_lower_bound": 639_346_300,   # story_id from 2018-08-16 (validated)
        "id_upper_bound": 640_319_086,   # story_id from 2018-08-20 (validated)
        "id_lower_episode": "2018-08-16",
        "id_upper_episode": "2018-08-20",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "donald-trumps-economic-strategy-maybe",
            "trump-economic-strategy",
            "trumps-economic-strategy",
            "trump-economy-strategy",
        ],
    },
    {
        "reference_date": "2018-10-05",
        "reference_title": "Who's Hiring?",
        "reference_episode": 146,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-10-04 e=654533556 (validated)
        #   2018-10-08 e=655634932 (validated)
        # Note: 2018-10-08 is "The Iron Lotus" — adjacent but not same window
        # as the two China episodes (2018-10-09, 2018-10-11).
        "id_lower_bound": 654_533_556,   # story_id from 2018-10-04 (validated)
        "id_upper_bound": 655_634_932,   # story_id from 2018-10-08 (validated)
        "id_lower_episode": "2018-10-04",
        "id_upper_episode": "2018-10-08",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "whos-hiring",
            "who-is-hiring",
            "jobs-report",
            "hiring-report",
        ],
    },
    {
        "reference_date": "2018-04-24",
        "reference_title": "When China's Ships Come In",
        "reference_episode": 31,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-04-20 e=604419798 (validated)
        #   2018-04-25 e=605819103 (validated)
        "id_lower_bound": 604_419_798,   # story_id from 2018-04-20 (validated)
        "id_upper_bound": 605_819_103,   # story_id from 2018-04-25 (validated)
        "id_lower_episode": "2018-04-20",
        "id_upper_episode": "2018-04-25",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "when-chinas-ships-come-in",
            "china-ships",
            "china-trade-ships",
            "chinas-ships",
        ],
    },
    {
        "reference_date": "2018-06-21",
        "reference_title": "Teenage (Employment) Wasteland",
        "reference_episode": 72,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-06-20 e=622042080 (validated)
        #   2018-06-22 e=622699133 (validated)
        "id_lower_bound": 622_042_080,   # story_id from 2018-06-20 (validated)
        "id_upper_bound": 622_699_133,   # story_id from 2018-06-22 (validated)
        "id_lower_episode": "2018-06-20",
        "id_upper_episode": "2018-06-22",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "teenage-employment-wasteland",
            "teen-employment-wasteland",
            "teenage-wasteland",
            "teen-jobs",
        ],
    },
    {
        "reference_date": "2018-06-13",
        "reference_title": "Dude, Where's My Trade War?",
        "reference_episode": 66,
        # Validated adjacent episodes from indicator_npr_audio_validation.json:
        #   2018-06-12 e=619309279 (validated)
        #   2018-06-14 e=620106332 (validated)
        "id_lower_bound": 619_309_279,   # story_id from 2018-06-12 (validated)
        "id_upper_bound": 620_106_332,   # story_id from 2018-06-14 (validated)
        "id_lower_episode": "2018-06-12",
        "id_upper_episode": "2018-06-14",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "dude-wheres-my-trade-war",
            "trade-war",
            "where-is-my-trade-war",
            "trade-war-tariffs",
        ],
    },
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _fetch_raw(url, range_request=False, max_bytes=2_000_000, retries=CONTENT_RETRIES):
    headers = dict(HEADERS)
    if range_request:
        headers["Range"] = "bytes=0-4095"

    last_error = None
    total_attempts = retries + 1
    for attempt in range(1, total_attempts + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                data = resp.read(4096 if range_request else max_bytes)
                return {
                    "status_code": getattr(resp, "status", None),
                    "final_url": resp.geturl(),
                    "content_type": resp.headers.get("Content-Type", ""),
                    "data": data,
                }
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts:
                time.sleep(attempt * RETRY_DELAY)

    raise last_error


def fetch_text(url, retries=CONTENT_RETRIES):
    resp = _fetch_raw(url, retries=retries)
    resp["text"] = resp["data"].decode("utf-8", errors="replace")
    return resp


# ---------------------------------------------------------------------------
# Text / HTML helpers (all pure / network-free)
# ---------------------------------------------------------------------------


def clean_text(value: str) -> str:
    """HTML-unescape and normalise escaped slashes."""
    value = html.unescape(value)
    value = value.replace("\\/", "/")
    return value


def unique(values):
    """Deduplicate preserving insertion order."""
    seen = set()
    result = []
    for v in values:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def slugify(text: str) -> str:
    """Convert a title to a lower-kebab ASCII slug, max 80 chars."""
    text = html.unescape(text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:80]


def title_token_overlap(title_a: str, title_b: str) -> float:
    """
    Return a token overlap score in [0, 1] between two titles.
    Tokens shorter than 4 characters (stop-words) are ignored.

    Uses the maximum of the two directional precision scores so that a short
    reference title can match against a longer page title (which may include
    show name, episode suffix, etc.) without being diluted by the extra tokens.
    """
    def tokens(t):
        return {w for w in re.split(r'\W+', t.lower()) if len(w) >= 4}

    a_tok = tokens(title_a)
    b_tok = tokens(title_b)
    if not a_tok:
        return 0.0
    shared = len(a_tok & b_tok)
    score_a = shared / len(a_tok)
    score_b = shared / len(b_tok) if b_tok else 0.0
    return max(score_a, score_b)


def slug_similarity(slug_a: str, slug_b: str) -> float:
    """
    Return a simple slug-fragment overlap score in [0, 1].
    Splits each slug on '-', ignores tokens < 4 chars.
    """
    def parts(s):
        return {p for p in s.split("-") if len(p) >= 4}

    a_p = parts(slug_a)
    b_p = parts(slug_b)
    if not a_p:
        return 0.0
    return len(a_p & b_p) / len(a_p)


# ---------------------------------------------------------------------------
# NPR URL / content extraction helpers (pure / network-free)
# ---------------------------------------------------------------------------


def extract_npr_story_urls(page: str) -> list:
    page = clean_text(page)
    pattern = (
        r'https?://(?:www\.)?npr\.org/'
        r'(?:'
        r'\d{4}/\d{2}/\d{2}/\d+/[^\s"\'<>\\]+|'
        r'sections?/[^\s"\'<>\\]+|'
        r'templates?/[^\s"\'<>\\]+|'
        r'player/embed/[^\s"\'<>\\]+|'
        r'transcripts?/[^\s"\'<>\\]+|'
        r'podcasts?/[^\s"\'<>\\]+'
        r')'
    )
    return unique(re.findall(pattern, page, re.I))


def extract_player_embeds(page: str) -> list:
    page = clean_text(page)
    pattern = r'https?://(?:www\.)?npr\.org/player/embed/(\d+)/(\d+)'
    pairs = re.findall(pattern, page, re.I)
    return unique([f"https://www.npr.org/player/embed/{s}/{a}" for s, a in pairs])


def extract_numeric_ids(page: str) -> list:
    """Extract long numeric IDs (7–12 digits) likely to be NPR story/audio IDs."""
    page = clean_text(page)
    return unique(re.findall(r'\b(\d{7,12})\b', page))


def extract_audio_urls(page: str) -> list:
    page = clean_text(page)
    patterns = [
        r'https?://ondemand\.npr\.org/[^\s"\'<>\\]+',
        r'https?://edge\d*\.pod\.npr\.org/[^\s"\'<>\\]+',
        r'https?://prfx\.byspotify\.com/[^\s"\'<>\\]+',
        r'https?://play\.podtrac\.com/[^\s"\'<>\\]+',
        r'https?://[^\s"\'<>\\]+\.mp3(?:\?[^\s"\'<>\\]*)?',
    ]
    found = []
    for pat in patterns:
        found.extend(re.findall(pat, page, re.I))
    return unique(found)


def extract_page_title(page: str) -> str:
    """Extract the <title> tag text (or og:title) from HTML."""
    page = clean_text(page)
    m = re.search(r'<title[^>]*>([^<]+)</title>', page, re.I | re.S)
    if m:
        return m.group(1).strip()
    m = re.search(r'og:title["\s]+content=["\']([^"\']+)["\']', page, re.I)
    if m:
        return m.group(1).strip()
    return ""


def extract_publication_date(page: str) -> str:
    """Extract a publication date (YYYY-MM-DD) from HTML metadata."""
    page = clean_text(page)
    patterns = [
        r'datePublished["\s:]+["\'](\d{4}-\d{2}-\d{2})',
        r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})',
        r'<meta[^>]+name=["\']date["\'][^>]+content=["\'](\d{4}-\d{2}-\d{2})',
        r'<time[^>]+datetime=["\'](\d{4}-\d{2}-\d{2})',
        r'publishedAt["\s:]+["\'](\d{4}-\d{2}-\d{2})',
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\'](\d{4}-\d{2}-\d{2})',
    ]
    for pat in patterns:
        m = re.search(pat, page, re.I)
        if m:
            return m.group(1)
    return ""


def extract_canonical_url(page: str) -> str:
    """Extract canonical URL from HTML."""
    page = clean_text(page)
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', page, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'og:url["\s]+content=["\']([^"\']+)["\']', page, re.I)
    if m:
        return m.group(1).strip()
    return ""


def extract_program_context(page: str) -> dict:
    """Extract NPR program/show context signals without treating them as proof."""
    page = clean_text(page)
    result = {
        "program_id": None,
        "show_name": None,
        "indicator_signals": [],
        "has_indicator_branding": False,
    }
    if "510325" in page:
        result["program_id"] = "510325"
        result["indicator_signals"].append("program_id_510325")
    m = re.search(r'(?:showTitle|programTitle|showName)["\s:]+["\']([^"\']{3,80})["\']', page, re.I)
    if m:
        result["show_name"] = m.group(1).strip()
        if "the indicator" in result["show_name"].lower():
            result["has_indicator_branding"] = True
            result["indicator_signals"].append("show_name_the_indicator")
    if "theindicator" in page.lower():
        result["indicator_signals"].append("theindicator_substring")
    return result


def _npr_story_id_from_url(url: str):
    """
    Extract a discovered NPR story ID from a URL like
    https://www.npr.org/2018/07/11/628123456/fed-accounts-for-all
    or transcript.php?storyId=628123456
    Returns the ID string or None.

    This is intentionally broader than the trusted-identity gate: transcript
    and query-style URLs may expose a story ID, but that evidence is
    discovered/unverified and must not by itself establish trusted identity.

    Player embed URLs (/player/embed/story/audio) are excluded because they
    contain two IDs; those are extracted separately by extract_player_embeds.
    """
    # Skip player embed URLs — they have a different structure
    if "/player/embed/" in url:
        return None
    m = re.search(r'/(\d{7,12})/[a-z0-9\-]+/?$', url)
    if m:
        return m.group(1)
    m = re.search(r'storyId=(\d{7,12})', url)
    if m:
        return m.group(1)
    return None


def _trusted_story_page_match(url: str):
    """Return the regex match for an allowed trusted NPR story-page URL."""
    cleaned = clean_text(url or "").strip()
    for pattern in TRUSTED_STORY_PAGE_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return match
    return None


def is_trusted_story_page_url(url: str) -> bool:
    """True only for explicitly allowed NPR episode story-page URL forms."""
    return _trusted_story_page_match(url) is not None


def _trusted_npr_story_id_from_url(url: str):
    """Extract a story ID only when the URL matches an allowed trusted story page."""
    match = _trusted_story_page_match(url)
    return match.group(1) if match else None


def _extract_url_date(url: str):
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url or "")
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _player_ids_from_url(url: str) -> dict:
    m = re.search(r'/player/embed/(\d{7,12})/(\d{7,12})', url or "", re.I)
    if not m:
        return {"story_id": None, "audio_id": None}
    return {"story_id": m.group(1), "audio_id": m.group(2)}


def _audio_id_from_url(url: str):
    matches = re.findall(r'(\d{7,12})', url or "")
    return matches[-1] if matches else None


def _run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or f"local-{int(time.time())}"


def _provenance(
    *,
    source_url: str,
    target: dict,
    evidence_type: str,
    trust_level: str = "untrusted",
    source_capture_timestamp: str = None,
    episode_qualified: bool = False,
) -> dict:
    return {
        "source_url": source_url,
        "source_capture_timestamp": source_capture_timestamp,
        "episode_qualified": episode_qualified,
        "target_episode": target["reference_date"],
        "target_title": target["reference_title"],
        "evidence_type": evidence_type,
        "trust_level": trust_level,
    }


def _clone_with_trust(evidence: dict, trust_level: str, episode_qualified: bool):
    clone = dict(evidence)
    clone["provenance"] = dict(clone.get("provenance") or {})
    clone["provenance"]["trust_level"] = trust_level
    clone["provenance"]["episode_qualified"] = episode_qualified
    return clone


def _make_story_evidence(url: str, target: dict, timestamp: str, qualified: bool):
    if not url:
        return None
    story_id = _npr_story_id_from_url(url)
    return {
        "story_url": url,
        "story_id": story_id,
        "provenance": _provenance(
            source_url=url,
            source_capture_timestamp=timestamp,
            target=target,
            evidence_type="story_url",
            trust_level="trusted" if qualified else "untrusted",
            episode_qualified=qualified,
        ),
    }


def _make_player_evidence(url: str, target: dict, source_url: str, timestamp: str, qualified: bool):
    ids = _player_ids_from_url(url)
    return {
        "player_url": url,
        "player_story_id": ids["story_id"],
        "player_audio_id": ids["audio_id"],
        "provenance": _provenance(
            source_url=source_url,
            source_capture_timestamp=timestamp,
            target=target,
            evidence_type="player_url",
            trust_level="trusted" if qualified else "untrusted",
            episode_qualified=qualified,
        ),
    }


def _make_audio_evidence(url: str, target: dict, source_url: str, timestamp: str, qualified: bool):
    return {
        "audio_url": url,
        "audio_id": _audio_id_from_url(url),
        "provenance": _provenance(
            source_url=source_url,
            source_capture_timestamp=timestamp,
            target=target,
            evidence_type="audio_url",
            trust_level="trusted" if qualified else "untrusted",
            episode_qualified=qualified,
        ),
    }


def _make_numeric_evidence(value: str, target: dict, source_url: str, timestamp: str, qualified: bool):
    return {
        "numeric_id": value,
        "provenance": _provenance(
            source_url=source_url,
            source_capture_timestamp=timestamp,
            target=target,
            evidence_type="numeric_id",
            trust_level="trusted" if qualified else "untrusted",
            episode_qualified=qualified,
        ),
    }


# ---------------------------------------------------------------------------
# Audio validation (same rules as the existing probe)
# ---------------------------------------------------------------------------


def is_generic_reject(url: str) -> bool:
    lower = url.lower()
    return any(re.search(pat, lower) for pat in REJECTED_PATTERNS)


def is_indicator_path(url: str) -> bool:
    return "/indicator/" in url.lower()


_NPR_DISTRIBUTION_HOSTS = frozenset({
    "play.podtrac.com",
    "prfx.byspotify.com",
})


def _is_npr_host(url: str) -> bool:
    """
    Return True when the URL is NPR-controlled or a known NPR audio
    distribution wrapper (podtrac, byspotify) whose path embeds an
    ondemand.npr.org segment that will resolve to NPR after redirect.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url.lower())
        netloc = parsed.netloc.split(":")[0]  # strip port
        if netloc == "npr.org" or netloc.endswith(".npr.org"):
            return True
        # Allow known distribution wrappers whose path embeds an NPR host as a
        # whole path segment (e.g. play.podtrac.com/.../ondemand.npr.org/...).
        # Split on "/" to check whole segments — not substrings of the full URL.
        if netloc in _NPR_DISTRIBUTION_HOSTS:
            path_parts = [p for p in parsed.path.split("/") if p]
            return any(
                p == "npr.org" or p.endswith(".npr.org")
                for p in path_parts
            )
        return False
    except Exception:
        return False


def validate_audio_candidate(url: str, reference_date: str) -> dict:
    """
    Validate one audio candidate without touching the network.
    Returns status 'needs_network_check' when pre-flight passes.
    """
    result = {
        "candidate_url": url,
        "validation_status": None,
        "reason": None,
        "final_url": None,
        "status_code": None,
        "content_type": None,
        "is_indicator_path": False,
        "valid_npr_indicator_audio": False,
    }

    if is_generic_reject(url):
        result["validation_status"] = "rejected_generic_stream"
        result["reason"] = "URL matches a live-stream or generic-audio pattern."
        return result

    if not _is_npr_host(url):
        result["validation_status"] = "rejected_non_npr"
        result["reason"] = "Not an NPR-hosted audio URL."
        return result

    result["validation_status"] = "needs_network_check"
    return result


def validate_audio_candidate_live(url: str, reference_date: str) -> dict:
    """Full validation including a network range-request."""
    result = validate_audio_candidate(url, reference_date)
    if result["validation_status"] != "needs_network_check":
        return result

    try:
        resp = _fetch_raw(url, range_request=True)
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
    result["is_indicator_path"] = ind

    if not ind:
        result["validation_status"] = "rejected_not_indicator_path"
        result["reason"] = (
            "Final URL does not contain /indicator/ — likely another NPR program."
        )
        return result

    result["validation_status"] = "validated"
    result["valid_npr_indicator_audio"] = True
    return result


def validate_audio_evidence_live(audio_evidence: dict, reference_date: str) -> dict:
    result = validate_audio_candidate_live(audio_evidence.get("audio_url"), reference_date)
    result["audio_url"] = audio_evidence.get("audio_url")
    result["audio_id"] = audio_evidence.get("audio_id")
    result["provenance"] = dict(audio_evidence.get("provenance") or {})
    result["trusted_for_recovery"] = (
        result.get("valid_npr_indicator_audio")
        and result["provenance"].get("trust_level") == "trusted"
        and result["provenance"].get("episode_qualified") is True
    )
    return result


# ---------------------------------------------------------------------------
# Wayback CDX API
# ---------------------------------------------------------------------------

# CDX result dict shape returned by both CDX helpers:
#   rows            – list of row dicts (may be empty on a genuine zero-row response)
#   query_url       – full CDX URL as issued
#   error_type      – None | "network_error" | "parse_error" | "empty_response"
#   error_message   – string description when error_type is set
#   response_length – byte length of raw response text (0 on network failure)
#   zero_row_response – True when the CDX API responded successfully but
#                       returned only a header row (genuine empty result);
#                       False on errors and on results with ≥ 1 data row.


def wayback_cdx_date_window(
    url_pattern: str,
    from_date: str,
    to_date: str,
    limit: int = CDX_DATE_WINDOW_LIMIT,
) -> dict:
    """
    Query the Wayback CDX API for captures of url_pattern in [from_date, to_date].

    Uses matchType=prefix so that url_pattern is treated as a URL prefix with
    trailing-only matching — the same semantics as a trailing ``*`` wildcard
    but expressed through an explicit CDX parameter, avoiding mid-path wildcard
    ambiguity.

    Returns a CDX result dict (see module-level comment above).
    from_date/to_date format: YYYYMMDD.
    """
    params = {
        "url": url_pattern,
        "matchType": CDX_MATCH_TYPE,
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "digest",
        "fl": "timestamp,original,statuscode,mimetype",
        "from": from_date,
        "to": to_date,
        "limit": str(limit),
    }
    query_url = "https://web.archive.org/cdx/search/cdx?" + urlencode(params)
    result: dict = {
        "rows": [],
        "query_url": query_url,
        "error_type": None,
        "error_message": None,
        "response_length": 0,
        "zero_row_response": False,
    }
    try:
        resp = fetch_text(query_url, retries=WAYBACK_DISCOVERY_RETRIES)
        raw = resp["text"]
        result["response_length"] = len(raw)
        data = json.loads(raw)
        if not isinstance(data, list):
            result["error_type"] = "parse_error"
            result["error_message"] = "CDX response was not a JSON array"
            return result
        if len(data) < 2:
            # Valid response from CDX with no data rows (header-only or empty array)
            result["zero_row_response"] = True
            return result
        header = data[0]
        result["rows"] = [dict(zip(header, row)) for row in data[1:]]
        return result
    except OSError as exc:
        result["error_type"] = "network_error"
        result["error_message"] = str(exc)
        return result
    except (json.JSONDecodeError, ValueError) as exc:
        result["error_type"] = "parse_error"
        result["error_message"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error_type"] = "network_error"
        result["error_message"] = str(exc)
        return result


def wayback_cdx_url_exact(url: str, limit: int = CDX_EXACT_LIMIT) -> dict:
    """
    Query CDX for exact URL matches (no wildcards, matchType=exact).

    Returns a CDX result dict (see module-level comment above).
    """
    params = {
        "url": url,
        "matchType": "exact",
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "digest",
        "fl": "timestamp,original,statuscode,mimetype",
        "limit": str(limit),
    }
    query_url = "https://web.archive.org/cdx/search/cdx?" + urlencode(params)
    result: dict = {
        "rows": [],
        "query_url": query_url,
        "error_type": None,
        "error_message": None,
        "response_length": 0,
        "zero_row_response": False,
    }
    try:
        resp = fetch_text(query_url, retries=WAYBACK_DISCOVERY_RETRIES)
        raw = resp["text"]
        result["response_length"] = len(raw)
        data = json.loads(raw)
        if not isinstance(data, list):
            result["error_type"] = "parse_error"
            result["error_message"] = "CDX response was not a JSON array"
            return result
        if len(data) < 2:
            result["zero_row_response"] = True
            return result
        header = data[0]
        result["rows"] = [dict(zip(header, row)) for row in data[1:]]
        return result
    except OSError as exc:
        result["error_type"] = "network_error"
        result["error_message"] = str(exc)
        return result
    except (json.JSONDecodeError, ValueError) as exc:
        result["error_type"] = "parse_error"
        result["error_message"] = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result["error_type"] = "network_error"
        result["error_message"] = str(exc)
        return result


def cdx_self_test() -> dict:
    """
    Verify that the CDX helper can retrieve at least one capture for each URL
    in CDX_SELF_TEST_URLS before a real run begins.

    These URLs are proven to have Wayback captures in repository-validated
    probes (indicator_wayback_npr_probe.json,
    indicator_multi_archive_player_probe.json).

    Returns ``{"passed": bool, "results": [...per-url dicts...]}``.
    A failed self-test means CDX connectivity or response parsing is broken;
    zero-row target results should not be interpreted as evidence of no archive
    coverage until the self-test passes.
    """
    results = []
    for url in CDX_SELF_TEST_URLS:
        cdx = wayback_cdx_url_exact(url, limit=2)
        results.append({
            "url": url,
            "passed": len(cdx["rows"]) > 0,
            "capture_count": len(cdx["rows"]),
            "query_url": cdx["query_url"],
            "error_type": cdx["error_type"],
            "error_message": cdx["error_message"],
            "response_length": cdx["response_length"],
            "zero_row_response": cdx["zero_row_response"],
        })
    return {
        "passed": all(r["passed"] for r in results),
        "results": results,
    }


def _cdx_date_window(reference_date: str, days_before: int = 3, days_after: int = 7) -> tuple:
    """Return (from_date, to_date) as YYYYMMDD strings."""
    d = datetime.date.fromisoformat(reference_date)
    from_d = d - datetime.timedelta(days=days_before)
    to_d = d + datetime.timedelta(days=days_after)
    return from_d.strftime("%Y%m%d"), to_d.strftime("%Y%m%d")


def stage_a_patterns(reference_date: str) -> list:
    """
    Return the Stage-A CDX URL prefixes for the given reference date.

    Each pattern covers one day (day-1, day, day+1 relative to reference_date)
    in both HTTPS and HTTP, targeting the historically valid NPR Planet Money
    story URL structure:

        https://www.npr.org/sections/money/YYYY/MM/DD/

    These are passed to ``wayback_cdx_date_window`` which sets
    ``matchType=prefix``, so no trailing wildcard is needed or used.
    """
    base = datetime.date.fromisoformat(reference_date)
    patterns = []
    for offset in (-1, 0, 1):
        day = base + datetime.timedelta(days=offset)
        y = day.strftime("%Y")
        m = day.strftime("%m")
        d = day.strftime("%d")
        patterns.append(f"https://www.npr.org/sections/money/{y}/{m}/{d}/")
        patterns.append(f"http://www.npr.org/sections/money/{y}/{m}/{d}/")
    return patterns[:MAX_STAGE_A_CDX_QUERIES]


# ---------------------------------------------------------------------------
# Title / slug match scoring
# ---------------------------------------------------------------------------


TITLE_MATCH_THRESHOLD = 0.5    # minimum token overlap to consider a match
SLUG_MATCH_THRESHOLD = 0.4     # minimum slug fragment overlap


def score_url_for_target(url: str, reference_title: str, slug_variants: list) -> float:
    """
    Return a match score (0–1) for how likely url is the target episode page.
    Checks both the URL slug and the title tokens.
    """
    url_lower = url.lower()
    url_slug_part = url_lower.split("/")[-1]

    # Slug-fragment match against each variant
    best_slug = max(
        (slug_similarity(url_slug_part, sv) for sv in slug_variants),
        default=0.0,
    )

    # Token overlap between URL slug and reference title
    title_score = title_token_overlap(url_slug_part.replace("-", " "), reference_title)

    return max(best_slug, title_score)


def request_budget() -> dict:
    per_episode = {
        "stage_a_cdx_queries": MAX_STAGE_A_CDX_QUERIES,
        "capture_fetches": MAX_FETCHED_CAPTURES,
        "live_player_fetches": MAX_PLAYER_PAGES,
        "player_cdx_queries": MAX_PLAYER_CDX_LOOKUPS,
        "archived_player_fetches": MAX_ARCHIVED_PLAYER_FETCHES,
        "audio_validations": MAX_AUDIO_CANDIDATES,
    }
    per_episode["max_logical_requests"] = sum(per_episode.values())
    discovery_max = MAX_STAGE_A_CDX_QUERIES + MAX_PLAYER_CDX_LOOKUPS
    content_max = (
        MAX_FETCHED_CAPTURES
        + MAX_PLAYER_PAGES
        + MAX_ARCHIVED_PLAYER_FETCHES
        + MAX_AUDIO_CANDIDATES
    )
    per_episode["conservative_timeout_ceiling_seconds"] = (
        discovery_max * REQUEST_TIMEOUT_SECONDS * (WAYBACK_DISCOVERY_RETRIES + 1)
        + content_max * REQUEST_TIMEOUT_SECONDS * (CONTENT_RETRIES + 1)
    )
    per_episode["realistic_worst_case_runtime_seconds"] = (
        discovery_max * 4 + content_max * 5
    )
    return {
        "per_episode": per_episode,
        "per_run": {
            "targets": len(TARGETS),
            "max_logical_requests": per_episode["max_logical_requests"] * len(TARGETS),
            "realistic_worst_case_runtime_seconds": per_episode["realistic_worst_case_runtime_seconds"] * len(TARGETS),
            "conservative_timeout_ceiling_seconds": per_episode["conservative_timeout_ceiling_seconds"] * len(TARGETS),
        },
    }


def score_page_for_target(
    page_title: str,
    pub_date: str,
    canonical: str,
    program_ctx: dict,
    reference_date: str,
    reference_title: str,
) -> dict:
    """
    Score an archived page as a candidate for the target episode.
    Returns a dict with individual scores and overall verdict.
    """
    title_score = title_token_overlap(page_title, reference_title)
    discovered_story_id = _npr_story_id_from_url(canonical)
    canonical_story_id = _trusted_npr_story_id_from_url(canonical)
    trusted_story_page = is_trusted_story_page_url(canonical)
    url_date = _extract_url_date(canonical)
    date_value = pub_date or url_date
    date_match = (date_value == reference_date) if date_value else None
    date_adjacent = (
        abs((datetime.date.fromisoformat(date_value)
             - datetime.date.fromisoformat(reference_date)).days) <= 1
        if date_value else False
    )
    has_story_id = bool(discovered_story_id)
    has_trusted_story_id = bool(canonical_story_id)
    has_indicator_branding = bool(program_ctx.get("has_indicator_branding"))
    has_indicator_program = program_ctx.get("program_id") == "510325"
    has_episode_context = has_indicator_branding or has_indicator_program

    verdict = "no_match"
    if (
        title_score >= TITLE_MATCH_THRESHOLD
        and has_trusted_story_id
        and trusted_story_page
        and (date_match or date_adjacent)
        and has_episode_context
    ):
        verdict = "strong_match"
    elif (
        title_score >= TITLE_MATCH_THRESHOLD
        and has_story_id
        and (date_match or date_adjacent)
        and not trusted_story_page
    ):
        verdict = "title_date_story_id_no_trusted_story_page"
    elif title_score >= TITLE_MATCH_THRESHOLD and has_trusted_story_id and (date_match or date_adjacent):
        verdict = "title_date_story_id_no_episode_context"
    elif title_score >= TITLE_MATCH_THRESHOLD and has_story_id:
        verdict = "title_match_story_id_no_date"
    elif title_score >= TITLE_MATCH_THRESHOLD:
        verdict = "title_match_no_story_id"
    elif has_story_id and (date_match or date_adjacent):
        verdict = "story_id_date_match_not_title"

    return {
        "title_score": round(title_score, 3),
        "date_match": date_match,
        "date_adjacent": date_adjacent,
        "date_source": "publication_date" if pub_date else ("canonical_url" if url_date else None),
        "has_story_id": has_story_id,
        "has_trusted_story_id": has_trusted_story_id,
        "canonical_story_id": canonical_story_id,
        "discovered_story_id": discovered_story_id,
        "trusted_story_page": trusted_story_page,
        "has_indicator_branding": has_indicator_branding,
        "has_episode_context": has_episode_context,
        "indicator_signals": program_ctx.get("indicator_signals", []),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Fetch and analyse one archived capture
# ---------------------------------------------------------------------------


def analyse_capture(archive_url: str, original_url: str, timestamp: str, target: dict) -> dict:
    item = {
        "timestamp": timestamp,
        "original_url": original_url,
        "archive_url": archive_url,
        "status": None,
        "page_title": None,
        "pub_date": None,
        "canonical_url": None,
        "program_context": None,
        "match_score": None,
        "story_id": None,
        "story_evidence": None,
        "player_embeds": [],
        "audio_candidates": [],
        "numeric_ids": [],
        "episode_qualified": False,
        "trust_level": "untrusted",
        "provenance": _provenance(
            source_url=original_url,
            source_capture_timestamp=timestamp,
            target=target,
            evidence_type="archive_capture",
            trust_level="untrusted",
            episode_qualified=False,
        ),
    }
    try:
        resp = fetch_text(archive_url)
        page = resp["text"]
        item["status"] = "fetched"

        page_title = extract_page_title(page)
        pub_date = extract_publication_date(page)
        canonical = extract_canonical_url(page)
        program_ctx = extract_program_context(page)
        story_url = canonical or original_url
        item["match_score"] = score_page_for_target(
            page_title,
            pub_date,
            story_url,
            program_ctx,
            target["reference_date"],
            target["reference_title"],
        )
        qualified = item["match_score"]["verdict"] == "strong_match"
        trust_level = "trusted" if qualified else "untrusted"
        story_evidence = _make_story_evidence(story_url, target, timestamp, qualified)
        embeds = [
            _make_player_evidence(url, target, original_url, timestamp, qualified)
            for url in extract_player_embeds(page)
        ]
        audio = [
            _make_audio_evidence(url, target, original_url, timestamp, qualified)
            for url in extract_audio_urls(page)
        ]
        num_ids = [
            _make_numeric_evidence(value, target, original_url, timestamp, qualified)
            for value in extract_numeric_ids(page)
        ]

        item["page_title"] = page_title
        item["pub_date"] = pub_date
        item["canonical_url"] = canonical
        item["program_context"] = program_ctx
        item["player_embeds"] = embeds
        item["audio_candidates"] = audio
        item["numeric_ids"] = num_ids
        item["story_evidence"] = story_evidence
        item["story_id"] = (story_evidence or {}).get("story_id")
        item["episode_qualified"] = qualified
        item["trust_level"] = trust_level
        item["provenance"]["episode_qualified"] = qualified
        item["provenance"]["trust_level"] = trust_level

    except Exception as exc:
        item["status"] = "error"
        item["error"] = str(exc)

    return item


# ---------------------------------------------------------------------------
# Numeric ID sparse probe
# ---------------------------------------------------------------------------


def sparse_numeric_probe(id_lower: int, id_upper: int,
                         reference_date: str, reference_title: str) -> list:
    """
    Probe a sparse set of NPR story IDs in [id_lower, id_upper].
    Uses CDX to find Wayback captures for each candidate ID.
    Returns a list of probe result dicts.
    Advisory only — a match here is only evidence, never proof.

    NOTE: This function is intentionally disabled via NUMERIC_PROBE_MAX=0.
    Numeric probing must remain disabled unless separately justified and
    reviewed per the investigation safety rules.
    """
    window = id_upper - id_lower
    if window <= 0 or NUMERIC_PROBE_MAX <= 0:
        return []

    # Build probe IDs: evenly spaced points across the window + midpoint
    # Use dynamic step so we always distribute probes across the full range
    dynamic_step = max(NUMERIC_PROBE_STEP, window // NUMERIC_PROBE_MAX)
    probe_ids = []
    for i in range(1, NUMERIC_PROBE_MAX + 1):
        candidate = id_lower + (i * dynamic_step)
        if candidate > id_upper:
            break
        probe_ids.append(candidate)

    # Always include midpoint
    mid = (id_lower + id_upper) // 2
    probe_ids.append(mid)
    probe_ids = sorted(set(probe_ids))[:NUMERIC_PROBE_MAX]

    results = []
    for nid in probe_ids:
        # Try to find Wayback captures for the numeric story ID using two
        # well-known NPR story URL structures.  Both are exact-URL lookups;
        # wildcard patterns are not compatible with wayback_cdx_url_exact.
        cdx_result = wayback_cdx_url_exact(
            f"https://www.npr.org/sections/money/{nid}", limit=3
        )
        cdx_rows = cdx_result["rows"]
        if not cdx_rows:
            cdx_result = wayback_cdx_url_exact(
                f"https://www.npr.org/{nid}", limit=3
            )
            cdx_rows = cdx_result["rows"]

        probe_result = {
            "probe_id": nid,
            "advisory_only": True,
            "cdx_rows_found": len(cdx_rows),
            "captures": [],
        }

        for row in cdx_rows[:2]:
            ts = row.get("timestamp", "")
            orig = row.get("original", "")
            if not ts or not orig:
                continue
            arch = f"https://web.archive.org/web/{ts}id_/{orig}"
            cap = analyse_capture(
                arch,
                orig,
                ts,
                {
                    "reference_date": reference_date,
                    "reference_title": reference_title,
                },
            )
            cap["source"] = "numeric_id_probe_advisory"
            probe_result["captures"].append(cap)

        results.append(probe_result)

    return results


# ---------------------------------------------------------------------------
# Main episode investigation
# ---------------------------------------------------------------------------


def investigate_episode(target: dict) -> dict:
    reference_date = target["reference_date"]
    reference_title = target["reference_title"]
    slug_variants = target["slug_variants"]
    id_lower = target["id_lower_bound"]
    id_upper = target["id_upper_bound"]

    from_date, to_date = _cdx_date_window(reference_date)
    budget = request_budget()["per_episode"]

    diag = {
        "method": "fresh-identity-discovery",
        "placeholder": False,
        "run_complete": True,
        "run_state": "run_complete",
        "run_id": _run_id(),
        "generated_at": _now_iso(),
        "reference_date": reference_date,
        "reference_title": reference_title,
        "reference_episode": target["reference_episode"],
        "strategy": "staged_cdx_funnel_identity_first",
        "date_window": {"from": from_date, "to": to_date},
        "request_budget": budget,
        "id_bounds": {
            "lower": id_lower,
            "upper": id_upper,
            "lower_from_episode": target["id_lower_episode"],
            "upper_from_episode": target["id_upper_episode"],
            "advisory_only": True,
        },
        "slug_filters_used": list(slug_variants),
        "cdx_queries": [],
        "slug_variant_probes": [],
        "date_window_captures": [],
        "numeric_id_probes": [],
        "numeric_probe_status": "disabled_advisory_only",
        "identity_candidates": [],
        "confirmed_identity": None,
        "player_probes": [],
        "trusted_audio_evidence": [],
        "untrusted_audio_evidence": [],
        "audio_candidates_tested": [],
        "validated_audio": [],
        "final_classification": None,
        "validation_summary": None,
        "discovery_exhausted_for_window": False,
        "counts": {
            "cdx_queries_issued": 0,
            "cdx_urls_returned": 0,
            "candidate_urls_scored": 0,
            "captures_fetched": 0,
            "captures_failed": 0,
            "strong_matches": 0,
            "partial_matches": 0,
            "player_pages_fetched": 0,
            "player_pages_failed": 0,
            "audio_candidates_tested": 0,
            "audio_validated": 0,
            "trusted_audio_candidates": 0,
            "untrusted_audio_candidates": 0,
            "numeric_ids_probed": 0,
            "logical_requests_issued": 0,
        },
    }

    candidate_map = {}
    best_identity = None

    # ------------------------------------------------------------------
    # Stage A/B: bounded date-window CDX queries + local scoring only
    # ------------------------------------------------------------------
    for url_pattern in stage_a_patterns(reference_date):
        cdx_entry = {
            "pattern": url_pattern,
            "from": from_date,
            "to": to_date,
            "limit": CDX_DATE_WINDOW_LIMIT,
            "query_url": "",
            "rows_returned": 0,
            "error_type": None,
            "error_message": None,
            "response_length": 0,
            "zero_row_response": False,
            "scored_candidates": [],
        }

        cdx_result = wayback_cdx_date_window(url_pattern, from_date, to_date, limit=CDX_DATE_WINDOW_LIMIT)
        rows = cdx_result["rows"]
        cdx_entry["query_url"] = cdx_result["query_url"]
        cdx_entry["error_type"] = cdx_result["error_type"]
        cdx_entry["error_message"] = cdx_result["error_message"]
        cdx_entry["response_length"] = cdx_result["response_length"]
        cdx_entry["zero_row_response"] = cdx_result["zero_row_response"]
        diag["counts"]["cdx_queries_issued"] += 1
        diag["counts"]["logical_requests_issued"] += 1
        diag["counts"]["cdx_urls_returned"] += len(rows)
        cdx_entry["rows_returned"] = len(rows)

        scored = []
        for row in rows:
            orig = row.get("original", "")
            url_date = _extract_url_date(orig)
            score = score_url_for_target(orig, reference_title, slug_variants)
            if url_date == reference_date:
                score = min(1.0, score + 0.2)
            elif url_date and abs(
                (datetime.date.fromisoformat(url_date) - datetime.date.fromisoformat(reference_date)).days
            ) <= 1:
                score = min(1.0, score + 0.1)
            entry = {
                "url": orig,
                "timestamp": row.get("timestamp"),
                "url_date": url_date,
                "score": round(score, 3),
            }
            scored.append(entry)
            prev = candidate_map.get(orig)
            if prev is None or entry["score"] > prev["score"]:
                candidate_map[orig] = entry
        scored.sort(key=lambda x: x["score"], reverse=True)
        cdx_entry["scored_candidates"] = scored[:20]  # top 20 for evidence

        diag["cdx_queries"].append(cdx_entry)
        diag["counts"]["candidate_urls_scored"] += len(scored)

    ranked_candidates = sorted(candidate_map.values(), key=lambda x: x["score"], reverse=True)
    fetch_queue = [item for item in ranked_candidates if item["score"] >= FETCH_SCORE_THRESHOLD][:MAX_FETCHED_CAPTURES]
    if not fetch_queue and ranked_candidates:
        fetch_queue = ranked_candidates[:1]

    # ------------------------------------------------------------------
    # Stage C: fetch only the highest-scoring candidate captures
    # ------------------------------------------------------------------
    for item in fetch_queue:
        ts = item.get("timestamp")
        orig = item.get("url")
        if not ts or not orig:
            continue
        arch = f"https://web.archive.org/web/{ts}id_/{orig}"
        diag["counts"]["logical_requests_issued"] += 1
        cap = analyse_capture(arch, orig, ts, target)
        cap["cdx_score"] = item["score"]
        cap["source"] = "stage_c_capture_fetch"
        diag["date_window_captures"].append(cap)

        if cap["status"] == "fetched":
            diag["counts"]["captures_fetched"] += 1
        else:
            diag["counts"]["captures_failed"] += 1

        if cap.get("episode_qualified"):
            diag["trusted_audio_evidence"].extend(cap.get("audio_candidates", []))
        else:
            diag["untrusted_audio_evidence"].extend(cap.get("audio_candidates", []))
        verdict = (cap.get("match_score") or {}).get("verdict", "")
        if verdict == "strong_match":
            diag["counts"]["strong_matches"] += 1
            if best_identity is None:
                best_identity = cap
        elif verdict != "no_match":
            diag["counts"]["partial_matches"] += 1
            diag["identity_candidates"].append(cap)

    # ------------------------------------------------------------------
    # Stage D: identity-gated player/audio probing
    # ------------------------------------------------------------------
    if best_identity:
        diag["confirmed_identity"] = {
            "source": best_identity.get("source"),
            "trusted": True,
            "episode_qualified": True,
            "archive_url": best_identity.get("archive_url"),
            "original_url": best_identity.get("original_url"),
            "page_title": best_identity.get("page_title"),
            "pub_date": best_identity.get("pub_date"),
            "canonical_url": best_identity.get("canonical_url"),
            "story_evidence": best_identity.get("story_evidence"),
            "story_id": best_identity.get("story_id"),
            "player_embeds": best_identity.get("player_embeds", []),
            "program_context": best_identity.get("program_context"),
            "match_score": best_identity.get("match_score"),
            "archive_capture_provenance": best_identity.get("provenance"),
            "evidence_chain": {
                "title_score": best_identity.get("match_score", {}).get("title_score"),
                "date_source": best_identity.get("match_score", {}).get("date_source"),
                "date_match": best_identity.get("match_score", {}).get("date_match"),
                "story_id": best_identity.get("story_id"),
                "player_embed_count": len(best_identity.get("player_embeds", [])),
            },
        }
        trusted_audio = [
            _clone_with_trust(audio, "trusted", True)
            for audio in best_identity.get("audio_candidates", [])
        ]
        trusted_players = [
            _clone_with_trust(player, "trusted", True)
            for player in best_identity.get("player_embeds", [])
        ]
    else:
        trusted_audio = []
        trusted_players = []

    diag["trusted_audio_evidence"].extend(trusted_audio)
    diag["counts"]["trusted_audio_candidates"] = len(diag["trusted_audio_evidence"])
    diag["counts"]["untrusted_audio_candidates"] = len(diag["untrusted_audio_evidence"])

    for player_evidence in trusted_players[:MAX_PLAYER_PAGES]:
        pu = player_evidence.get("player_url")
        pr = {
            "url": pu,
            "status": None,
            "source": "trusted_live_player",
            "provenance": player_evidence.get("provenance"),
        }
        try:
            diag["counts"]["logical_requests_issued"] += 1
            resp = fetch_text(pu)
            page = resp["text"]
            pr["status"] = "fetched"
            pr["final_url"] = resp["final_url"]
            pr["audio_candidates"] = []
            for url in extract_audio_urls(page):
                evidence = _make_audio_evidence(url, target, pu, None, True)
                evidence["provenance"]["evidence_type"] = "audio_url_from_live_player"
                pr["audio_candidates"].append(evidence)
                diag["trusted_audio_evidence"].append(evidence)
            diag["counts"]["player_pages_fetched"] += 1
        except Exception as exc:
            pr["status"] = "error"
            pr["error"] = str(exc)
            diag["counts"]["player_pages_failed"] += 1
        diag["player_probes"].append(pr)

    archived_player_fetches = 0
    for player_evidence in trusted_players[:MAX_PLAYER_CDX_LOOKUPS]:
        pu = player_evidence.get("player_url")
        diag["counts"]["cdx_queries_issued"] += 1
        diag["counts"]["logical_requests_issued"] += 1
        player_cdx = wayback_cdx_url_exact(pu, limit=CDX_EXACT_LIMIT)
        rows = player_cdx["rows"]
        for row in rows:
            if archived_player_fetches >= MAX_ARCHIVED_PLAYER_FETCHES:
                break
            ts = row.get("timestamp", "")
            orig = row.get("original", "")
            if not ts or not orig:
                continue
            arch = f"https://web.archive.org/web/{ts}id_/{orig}"
            probe_item = {
                "url": pu,
                "archive_url": arch,
                "timestamp": ts,
                "status": None,
                "source": "trusted_archived_player",
                "provenance": player_evidence.get("provenance"),
                "audio_candidates": [],
            }
            try:
                diag["counts"]["logical_requests_issued"] += 1
                resp = fetch_text(arch)
                probe_item["status"] = "fetched"
                probe_item["final_url"] = resp["final_url"]
                for url in extract_audio_urls(resp["text"]):
                    evidence = _make_audio_evidence(url, target, pu, ts, True)
                    evidence["provenance"]["evidence_type"] = "audio_url_from_archived_player"
                    probe_item["audio_candidates"].append(evidence)
                    diag["trusted_audio_evidence"].append(evidence)
                diag["counts"]["player_pages_fetched"] += 1
            except Exception as exc:
                probe_item["status"] = "error"
                probe_item["error"] = str(exc)
                diag["counts"]["player_pages_failed"] += 1
            diag["player_probes"].append(probe_item)
            archived_player_fetches += 1

    # ------------------------------------------------------------------
    # Validate only trusted audio evidence descending from confirmed identity
    # ------------------------------------------------------------------
    deduped_audio = []
    seen_audio_urls = set()
    for evidence in diag["trusted_audio_evidence"]:
        url = evidence.get("audio_url")
        if not url or url in seen_audio_urls:
            continue
        seen_audio_urls.add(url)
        deduped_audio.append(evidence)
    tested = []
    validated = []
    for audio_evidence in deduped_audio[:MAX_AUDIO_CANDIDATES]:
        diag["counts"]["logical_requests_issued"] += 1
        check = validate_audio_evidence_live(audio_evidence, reference_date)
        tested.append(check)
        if check.get("trusted_for_recovery"):
            validated.append(check)

    diag["audio_candidates_tested"] = tested
    diag["validated_audio"] = validated
    diag["counts"]["trusted_audio_candidates"] = len(deduped_audio)
    diag["counts"]["untrusted_audio_candidates"] = len(diag["untrusted_audio_evidence"])
    diag["counts"]["audio_candidates_tested"] = len(tested)
    diag["counts"]["audio_validated"] = len(validated)

    # ------------------------------------------------------------------
    # Final classification
    # ------------------------------------------------------------------
    diag["discovery_exhausted_for_window"] = True  # we probed all planned patterns

    if diag["confirmed_identity"] and validated:
        diag["final_classification"] = "recovered"
        diag["validation_summary"] = (
            f"RECOVERED: verified identity plus {len(validated)} trusted NPR Indicator audio "
            f"file(s) for {reference_date}."
        )
    elif best_identity:
        diag["final_classification"] = "identity_found_audio_unresolved"
        diag["validation_summary"] = (
            f"Verified identity found (verdict={best_identity.get('match_score', {}).get('verdict')}) "
            f"but no trusted validated NPR Indicator audio. "
            f"Tested {len(tested)} audio candidates."
        )
    elif diag["identity_candidates"]:
        diag["final_classification"] = "partial_identity_no_audio"
        diag["validation_summary"] = (
            f"Partial identity candidates found ({len(diag['identity_candidates'])}) "
            f"but none satisfied the title+date+story-ID proof chain. No trusted audio validated."
        )
    else:
        diag["final_classification"] = "no_identity_found"
        diag["validation_summary"] = (
            f"Bounded CDX discovery exhausted. "
            f"No episode-qualified NPR story page found for {reference_date}. "
            f"Trusted audio candidates tested: {len(tested)}."
        )

    return diag


# ---------------------------------------------------------------------------
# Placeholders for output files (written before investigation, overwritten after)
# ---------------------------------------------------------------------------


def _placeholder_diag(target: dict) -> dict:
    return {
        "placeholder": True,
        "run_complete": False,
        "run_state": "placeholder",
        "run_id": _run_id(),
        "generated_at": _now_iso(),
        "reference_date": target["reference_date"],
        "reference_title": target["reference_title"],
        "reference_episode": target["reference_episode"],
        "final_classification": None,
    }


def _placeholder_summary(targets: list) -> dict:
    return {
        "placeholder": True,
        "run_complete": False,
        "run_state": "placeholder",
        "run_id": _run_id(),
        "method": "fresh-identity-discovery",
        "generated_at": _now_iso(),
        "episodes": [
            {
                "reference_date": t["reference_date"],
                "reference_title": t["reference_title"],
                "reference_episode": t["reference_episode"],
                "final_classification": None,
            }
            for t in targets
        ],
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def output_filename(reference_date: str) -> str:
    return f"batch2_fresh_identity_discovery_{reference_date}_diag.json"


def _write_json(path: Path, payload: dict):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def write_placeholders():
    for target in TARGETS:
        _write_json(OUTPUT_DIR / output_filename(target["reference_date"]), _placeholder_diag(target))
    _write_json(Path(SUMMARY_OUTPUT), _placeholder_summary(TARGETS))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(write_placeholders_only: bool = False):
    write_placeholders()
    if write_placeholders_only:
        return _placeholder_summary(TARGETS)

    # ------------------------------------------------------------------
    # CDX self-test: verify connectivity using known-good archived URLs
    # before running any target investigation.  If the self-test fails,
    # zero-row CDX results from target queries must NOT be interpreted
    # as evidence that no archive coverage exists for those targets.
    # ------------------------------------------------------------------
    print("\nRunning CDX self-test …")
    self_test = cdx_self_test()
    print(f"  CDX self-test passed: {self_test['passed']}")
    for r in self_test["results"]:
        status = "OK" if r["passed"] else f"FAIL (error_type={r['error_type']})"
        print(f"    {status}  captures={r['capture_count']}  {r['url']}")

    if not self_test["passed"]:
        print("\nCDX self-test failed — aborting target investigation.")
        print("Zero-row results cannot be used as evidence of no archive coverage.")
        self_test_summary = {
            "placeholder": False,
            "run_complete": False,
            "run_state": "cdx_self_test_failed",
            "run_id": _run_id(),
            "method": "fresh-identity-discovery",
            "generated_at": _now_iso(),
            "cdx_self_test": self_test,
            "targets_investigated": 0,
            "request_budget": request_budget(),
            "episodes": [],
            "counts": {
                "attempted": 0,
                "completed": 0,
                "failed": 0,
                "skipped": len(TARGETS),
                "recovered": 0,
            },
        }
        _write_json(Path(SUMMARY_OUTPUT), self_test_summary)
        print(f"\nSummary written: {SUMMARY_OUTPUT}")
        return self_test_summary

    episode_results = []
    summary = {
        "placeholder": False,
        "run_complete": True,
        "run_state": "run_complete",
        "run_id": _run_id(),
        "method": "fresh-identity-discovery",
        "generated_at": _now_iso(),
        "cdx_self_test": self_test,
        "targets_investigated": len(TARGETS),
        "request_budget": request_budget(),
        "episodes": [],
        "counts": {
            "attempted": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "recovered": 0,
        },
    }

    for target in TARGETS:
        ref_date = target["reference_date"]
        ref_title = target["reference_title"]
        print(f"\n{'='*60}")
        print(f"Fresh identity discovery: {ref_date} — {ref_title}")
        print(f"{'='*60}")

        summary["counts"]["attempted"] += 1
        try:
            diag = investigate_episode(target)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            diag = {
                "placeholder": False,
                "run_complete": False,
                "run_state": "failed",
                "run_id": _run_id(),
                "generated_at": _now_iso(),
                "reference_date": ref_date,
                "reference_title": ref_title,
                "reference_episode": target["reference_episode"],
                "error": str(exc),
                "final_classification": "failed",
                "validation_summary": f"Investigation failed: {exc}",
            }
            summary["counts"]["failed"] += 1
        else:
            summary["counts"]["completed"] += 1
            if diag.get("final_classification") == "recovered":
                summary["counts"]["recovered"] += 1

        episode_results.append((ref_date, diag))
        summary["episodes"].append({
            "reference_date": ref_date,
            "reference_title": ref_title,
            "final_classification": diag.get("final_classification"),
            "validation_summary": diag.get("validation_summary"),
            "counts": diag.get("counts", {}),
        })

    if summary["counts"]["failed"]:
        summary["run_complete"] = False
        summary["run_state"] = "failed"

    for ref_date, diag in episode_results:
        out_path = OUTPUT_DIR / output_filename(ref_date)
        _write_json(out_path, diag)
        print(f"  → Written: {out_path.name}")
        print(f"  → {diag.get('validation_summary') or diag.get('final_classification')}")

    summary_path = Path(SUMMARY_OUTPUT)
    _write_json(summary_path, summary)
    print(f"\nSummary written: {summary_path.name}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-placeholders-only",
        action="store_true",
        help="Overwrite all diagnostic outputs with placeholder sentinels and exit.",
    )
    args = parser.parse_args()
    run(write_placeholders_only=args.write_placeholders_only)
