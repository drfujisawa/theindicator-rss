#!/usr/bin/env python3
"""
Fresh NPR identity discovery for the three unresolved Indicator episodes:

  1. 2018-07-11 — "Fed Accounts For All!"
  2. 2018-08-10 — "Privacy Please: Why Public Companies Go Private (Or Vice Versa)"
  3. 2018-09-24 — "Saudi Arabia & The Paradox of Plenty"

The previous probe (probe_top3_ranked_prospects.py) established that all
previously known story/player IDs are wrong — they belong to unrelated NPR
programs.  This script starts fresh:

Strategy
--------
1. Query the Wayback CDX API over a bounded date window around each target
   date, using NPR URL patterns:
     - https://www.npr.org/* (filtered to story-like paths)
     - https://www.npr.org/sections/money/*
     - https://www.npr.org/sections/theindicator/*
     - https://www.npr.org/podcasts/510325/* (The Indicator programme page)
2. Search returned original URLs and fetched page titles for slug/title
   similarity to the exact target title.
3. Fetch promising captures and verify: page title, publication date, canonical
   NPR URL, NPR story ID, player/embed ID, audio ID, Indicator show context.
4. Also probe known-slug-variant URLs directly via CDX.
5. Use adjacent validated-corpus IDs to bound a sparse numeric probe
   (never treated as proof — advisory only).
6. If an authentic identity is found, probe live and archived NPR player pages
   and validate audio using the existing Indicator validator rules:
     - explicit audio/* MIME type;
     - final URL must be NPR-controlled and contain /indicator/;
     - episode date match in filename is not required (only advisory);
     - unrelated/sidebar/related-story audio cannot anchor a recovery;
     - filename similarity alone is never enough.

Writes (per episode):
  fresh_identity_discovery_<YYYY-MM-DD>_diag.json

Writes (summary):
  fresh_identity_discovery_summary.json
"""

import datetime
import html
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent

SUMMARY_OUTPUT = "fresh_identity_discovery_summary.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; IndicatorFreshIdentityProbe/1.0)"
    )
}

TIMEOUT = 30
RETRIES = 3
RETRY_DELAY = 2  # seconds between retries

# CDX: per-pattern limit.  Keep small to avoid huge Wayback requests.
CDX_DATE_WINDOW_LIMIT = 100
CDX_SLUG_LIMIT = 20
# After CDX, max captures to actually fetch.
MAX_CAPTURES_PER_PATTERN = 5
MAX_PLAYER_PAGES = 10
MAX_AUDIO_CANDIDATES = 40

# Numeric ID probe: sparse step size — do not brute-force.
NUMERIC_PROBE_STEP = 50_000   # probe every 50 k IDs across the window
NUMERIC_PROBE_MAX = 20        # hard cap on IDs to probe per episode

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
        "reference_date": "2018-07-11",
        "reference_title": "Fed Accounts For All!",
        "reference_episode": 85,
        # Validated adjacent episodes from the corpus:
        #   2018-07-09 e=627425359, 2018-07-18 e=630262698
        # The target sits 2 weekdays after 07-09; typical daily increment ~350-400k
        "id_lower_bound": 627_425_359,   # e from 2018-07-09 (validated)
        "id_upper_bound": 630_262_698,   # e from 2018-07-18 (validated)
        "id_lower_episode": "2018-07-09",
        "id_upper_episode": "2018-07-18",
        # NPR section paths that may have hosted an Indicator story page
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        # Slug variants derived from the title (best-effort; not assumed correct)
        "slug_variants": [
            "fed-accounts-for-all",
            "fed-accounts",
            "federal-reserve-accounts",
        ],
    },
    {
        "reference_date": "2018-08-10",
        "reference_title": "Privacy Please: Why Public Companies Go Private (Or Vice Versa)",
        "reference_episode": 107,
        # Validated adjacent episodes:
        #   2018-08-09 e=637224491, 2018-08-14 e=638649514
        "id_lower_bound": 637_224_491,
        "id_upper_bound": 638_649_514,
        "id_lower_episode": "2018-08-09",
        "id_upper_episode": "2018-08-14",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "privacy-please",
            "public-companies-go-private",
            "why-public-companies-go-private",
            "companies-go-private",
        ],
    },
    {
        "reference_date": "2018-09-24",
        "reference_title": "Saudi Arabia & The Paradox of Plenty",
        "reference_episode": 137,
        # Validated adjacent episodes:
        #   2018-09-21 e=650559682, 2018-09-25 e=651545439
        "id_lower_bound": 650_559_682,
        "id_upper_bound": 651_545_439,
        "id_lower_episode": "2018-09-21",
        "id_upper_episode": "2018-09-25",
        "section_paths": [
            "sections/money/theindicator",
            "sections/theindicator",
            "sections/money",
        ],
        "slug_variants": [
            "saudi-arabia-paradox-of-plenty",
            "paradox-of-plenty",
            "saudi-arabia-oil",
        ],
    },
]

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _fetch_raw(url, range_request=False, max_bytes=2_000_000):
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
                time.sleep(attempt * RETRY_DELAY)

    raise last_error


def fetch_text(url):
    resp = _fetch_raw(url)
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
    """Extract NPR program/show context from bootstrap JSON or meta tags."""
    page = clean_text(page)
    result = {
        "program_id": None,
        "show_name": None,
        "is_indicator": False,
    }
    # Look for program ID 510325 (The Indicator from Planet Money)
    if "510325" in page:
        result["program_id"] = "510325"
        result["is_indicator"] = True
    # Look for "indicator" specifically in known program/show title context keys
    m = re.search(r'(?:showTitle|programTitle|showName)["\s:]+["\']([^"\']{3,80})["\']', page, re.I)
    if m:
        result["show_name"] = m.group(1).strip()
        if "indicator" in result["show_name"].lower():
            result["is_indicator"] = True
    # Check for explicit theindicator section path (strong signal)
    if "theindicator" in page.lower():
        result["is_indicator"] = True
    return result


def _npr_story_id_from_url(url: str):
    """
    Extract the NPR story ID from a dated story URL like
    https://www.npr.org/2018/07/11/628123456/fed-accounts-for-all
    or transcript.php?storyId=628123456
    Returns the ID string or None.

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
            "Final URL does not contain /indicator/ — likely another NPR programme."
        )
        return result

    result["validation_status"] = "validated"
    result["valid_npr_indicator_audio"] = True
    return result


# ---------------------------------------------------------------------------
# Wayback CDX API
# ---------------------------------------------------------------------------


def wayback_cdx_date_window(
    url_pattern: str,
    from_date: str,
    to_date: str,
    limit: int = CDX_DATE_WINDOW_LIMIT,
) -> list:
    """
    Query the Wayback CDX API for captures of url_pattern in [from_date, to_date].
    Returns a list of row dicts.  from_date/to_date format: YYYYMMDD.
    """
    params = {
        "url": url_pattern,
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "digest",
        "fl": "timestamp,original,statuscode,mimetype",
        "from": from_date,
        "to": to_date,
        "limit": str(limit),
    }
    query = "https://web.archive.org/cdx/search/cdx?" + urlencode(params)
    try:
        resp = fetch_text(query)
        data = json.loads(resp["text"])
        if not isinstance(data, list) or len(data) < 2:
            return []
        header = data[0]
        return [dict(zip(header, row)) for row in data[1:]]
    except Exception:
        return []


def wayback_cdx_url_exact(url: str, limit: int = CDX_SLUG_LIMIT) -> list:
    """Query CDX for exact URL matches (no wildcards)."""
    params = {
        "url": url,
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "digest",
        "fl": "timestamp,original,statuscode,mimetype",
        "limit": str(limit),
    }
    query = "https://web.archive.org/cdx/search/cdx?" + urlencode(params)
    try:
        resp = fetch_text(query)
        data = json.loads(resp["text"])
        if not isinstance(data, list) or len(data) < 2:
            return []
        header = data[0]
        return [dict(zip(header, row)) for row in data[1:]]
    except Exception:
        return []


def _cdx_date_window(reference_date: str, days_before: int = 3, days_after: int = 7) -> tuple:
    """Return (from_date, to_date) as YYYYMMDD strings."""
    d = datetime.date.fromisoformat(reference_date)
    from_d = d - datetime.timedelta(days=days_before)
    to_d = d + datetime.timedelta(days=days_after)
    return from_d.strftime("%Y%m%d"), to_d.strftime("%Y%m%d")


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
    date_match = (pub_date == reference_date) if pub_date else None
    date_adjacent = (
        abs((datetime.date.fromisoformat(pub_date)
             - datetime.date.fromisoformat(reference_date)).days) <= 1
        if pub_date else False
    )
    is_indicator = program_ctx.get("is_indicator", False)

    verdict = "no_match"
    if title_score >= TITLE_MATCH_THRESHOLD and is_indicator:
        if date_match or date_adjacent:
            verdict = "strong_match"
        else:
            verdict = "partial_match_no_date"
    elif title_score >= TITLE_MATCH_THRESHOLD:
        verdict = "title_match_not_indicator"
    elif is_indicator and (date_match or date_adjacent):
        verdict = "indicator_date_match_not_title"

    return {
        "title_score": round(title_score, 3),
        "date_match": date_match,
        "date_adjacent": date_adjacent,
        "is_indicator": is_indicator,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Fetch and analyse one archived capture
# ---------------------------------------------------------------------------


def analyse_capture(archive_url: str, original_url: str, timestamp: str,
                    reference_date: str, reference_title: str) -> dict:
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
        "player_embeds": [],
        "audio_candidates": [],
        "numeric_ids": [],
    }
    try:
        resp = fetch_text(archive_url)
        page = resp["text"]
        item["status"] = "fetched"

        page_title = extract_page_title(page)
        pub_date = extract_publication_date(page)
        canonical = extract_canonical_url(page)
        program_ctx = extract_program_context(page)
        embeds = extract_player_embeds(page)
        audio = extract_audio_urls(page)
        num_ids = extract_numeric_ids(page)

        item["page_title"] = page_title
        item["pub_date"] = pub_date
        item["canonical_url"] = canonical
        item["program_context"] = program_ctx
        item["player_embeds"] = embeds
        item["audio_candidates"] = audio
        item["numeric_ids"] = num_ids

        # Try to extract story ID from canonical/original URL
        story_id = _npr_story_id_from_url(canonical) or _npr_story_id_from_url(original_url)
        item["story_id"] = story_id

        item["match_score"] = score_page_for_target(
            page_title, pub_date, canonical, program_ctx,
            reference_date, reference_title,
        )

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
    """
    window = id_upper - id_lower
    if window <= 0:
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
        # Try to find Wayback captures for a dated NPR story URL with this ID
        cdx_rows = wayback_cdx_url_exact(
            f"https://www.npr.org/*/{nid}/*", limit=5
        )
        if not cdx_rows:
            cdx_rows = wayback_cdx_url_exact(
                f"https://www.npr.org/{nid}", limit=3
            )

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
            cap = analyse_capture(arch, orig, ts, reference_date, reference_title)
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
    section_paths = target["section_paths"]
    id_lower = target["id_lower_bound"]
    id_upper = target["id_upper_bound"]

    from_date, to_date = _cdx_date_window(reference_date)

    diag = {
        "method": "fresh-identity-discovery",
        "run_state": "run_complete",
        "reference_date": reference_date,
        "reference_title": reference_title,
        "reference_episode": target["reference_episode"],
        "strategy": "cdx_date_window_plus_slug_variants_plus_sparse_numeric",
        "date_window": {"from": from_date, "to": to_date},
        "id_bounds": {
            "lower": id_lower,
            "upper": id_upper,
            "lower_from_episode": target["id_lower_episode"],
            "upper_from_episode": target["id_upper_episode"],
            "advisory_only": True,
        },
        "cdx_queries": [],
        "slug_variant_probes": [],
        "date_window_captures": [],
        "numeric_id_probes": [],
        "identity_candidates": [],
        "confirmed_identity": None,
        "player_probes": [],
        "audio_candidates_tested": [],
        "validated_audio": [],
        "final_classification": None,
        "validation_summary": None,
        "discovery_exhausted_for_window": False,
        "counts": {
            "cdx_queries_issued": 0,
            "cdx_urls_returned": 0,
            "slug_candidates_found": 0,
            "captures_fetched": 0,
            "captures_failed": 0,
            "strong_matches": 0,
            "partial_matches": 0,
            "audio_candidates_tested": 0,
            "audio_validated": 0,
            "numeric_ids_probed": 0,
        },
    }

    all_audio = []
    all_player_urls = []
    best_identity = None  # Will be set if a strong/partial match is confirmed

    # ------------------------------------------------------------------
    # Step 1: CDX date-window queries for each section path
    # ------------------------------------------------------------------
    for section_path in section_paths:
        url_pattern = f"https://www.npr.org/{section_path}/*"
        cdx_entry = {
            "pattern": url_pattern,
            "from": from_date,
            "to": to_date,
            "limit": CDX_DATE_WINDOW_LIMIT,
            "rows_returned": 0,
            "scored_candidates": [],
        }

        rows = wayback_cdx_date_window(url_pattern, from_date, to_date, limit=CDX_DATE_WINDOW_LIMIT)
        diag["counts"]["cdx_queries_issued"] += 1
        diag["counts"]["cdx_urls_returned"] += len(rows)
        cdx_entry["rows_returned"] = len(rows)

        # Score each CDX row by slug similarity — pick best candidates
        scored = []
        for row in rows:
            orig = row.get("original", "")
            score = score_url_for_target(orig, reference_title, slug_variants)
            scored.append({"url": orig, "timestamp": row.get("timestamp"), "score": round(score, 3)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        cdx_entry["scored_candidates"] = scored[:20]  # top 20 for evidence

        diag["cdx_queries"].append(cdx_entry)
        diag["counts"]["slug_candidates_found"] += len([s for s in scored if s["score"] >= SLUG_MATCH_THRESHOLD])

        # Fetch promising captures (score >= threshold)
        fetched_this_pattern = 0
        for item in scored:
            if fetched_this_pattern >= MAX_CAPTURES_PER_PATTERN:
                break
            if item["score"] < SLUG_MATCH_THRESHOLD:
                break

            ts = item["timestamp"]
            orig = item["url"]
            arch = f"https://web.archive.org/web/{ts}id_/{orig}"
            cap = analyse_capture(arch, orig, ts, reference_date, reference_title)
            cap["cdx_score"] = item["score"]
            cap["source"] = "cdx_date_window"
            diag["date_window_captures"].append(cap)
            fetched_this_pattern += 1

            if cap["status"] == "fetched":
                diag["counts"]["captures_fetched"] += 1
            else:
                diag["counts"]["captures_failed"] += 1

            all_audio.extend(cap.get("audio_candidates", []))
            all_player_urls.extend(cap.get("player_embeds", []))

            verdict = (cap.get("match_score") or {}).get("verdict", "")
            if verdict == "strong_match":
                diag["counts"]["strong_matches"] += 1
                if best_identity is None:
                    best_identity = cap
            elif verdict in ("partial_match_no_date", "indicator_date_match_not_title", "title_match_not_indicator"):
                diag["counts"]["partial_matches"] += 1
                diag["identity_candidates"].append(cap)

    # ------------------------------------------------------------------
    # Step 2: Direct slug-variant CDX probes
    # For each slug variant, try both section/theindicator and dated paths.
    # ------------------------------------------------------------------
    year, month, day = reference_date.split("-")
    for sv in slug_variants:
        for section in ["sections/theindicator", "sections/money/theindicator"]:
            candidate_url = f"https://www.npr.org/{section}/{year}/{month}/{day}/{sv}"
            rows = wayback_cdx_url_exact(candidate_url, limit=CDX_SLUG_LIMIT)
            diag["counts"]["cdx_queries_issued"] += 1
            entry = {
                "slug_variant": sv,
                "url_tried": candidate_url,
                "rows_found": len(rows),
                "captures": [],
            }
            for row in rows[:2]:
                ts = row.get("timestamp", "")
                orig = row.get("original", "")
                if not ts or not orig:
                    continue
                arch = f"https://web.archive.org/web/{ts}id_/{orig}"
                cap = analyse_capture(arch, orig, ts, reference_date, reference_title)
                cap["source"] = "slug_variant_probe"
                entry["captures"].append(cap)
                diag["counts"]["captures_fetched" if cap["status"] == "fetched" else "captures_failed"] += 1
                all_audio.extend(cap.get("audio_candidates", []))
                all_player_urls.extend(cap.get("player_embeds", []))
                verdict = (cap.get("match_score") or {}).get("verdict", "")
                if verdict == "strong_match" and best_identity is None:
                    best_identity = cap
                elif verdict not in ("no_match",):
                    diag["identity_candidates"].append(cap)
            diag["slug_variant_probes"].append(entry)

    # ------------------------------------------------------------------
    # Step 3: Sparse numeric ID probe (advisory)
    # ------------------------------------------------------------------
    numeric_results = sparse_numeric_probe(id_lower, id_upper, reference_date, reference_title)
    diag["numeric_id_probes"] = numeric_results
    diag["counts"]["numeric_ids_probed"] = sum(
        len(r.get("captures", [])) for r in numeric_results
    )
    for nr in numeric_results:
        for cap in nr.get("captures", []):
            all_audio.extend(cap.get("audio_candidates", []))
            all_player_urls.extend(cap.get("player_embeds", []))
            verdict = (cap.get("match_score") or {}).get("verdict", "")
            if verdict == "strong_match" and best_identity is None:
                best_identity = cap
                best_identity["source"] = "numeric_id_probe_advisory"
            elif verdict not in ("no_match",):
                cap["source"] = "numeric_id_probe_advisory"
                diag["identity_candidates"].append(cap)

    # ------------------------------------------------------------------
    # Step 4: Record confirmed identity (if found)
    # ------------------------------------------------------------------
    if best_identity:
        diag["confirmed_identity"] = {
            "source": best_identity.get("source"),
            "archive_url": best_identity.get("archive_url"),
            "original_url": best_identity.get("original_url"),
            "page_title": best_identity.get("page_title"),
            "pub_date": best_identity.get("pub_date"),
            "canonical_url": best_identity.get("canonical_url"),
            "story_id": best_identity.get("story_id"),
            "player_embeds": best_identity.get("player_embeds", []),
            "program_context": best_identity.get("program_context"),
            "match_score": best_identity.get("match_score"),
            "advisory_note": (
                "Confirmed only if title + date + NPR story page evidence align. "
                "Numeric ID estimates are advisory only and cannot confirm identity alone."
            ),
        }
        all_audio.extend(best_identity.get("audio_candidates", []))
        all_player_urls.extend(best_identity.get("player_embeds", []))

    # ------------------------------------------------------------------
    # Step 5: Probe live and archived NPR player pages (only if identity found)
    # ------------------------------------------------------------------
    player_urls_to_probe = unique(all_player_urls)[:MAX_PLAYER_PAGES]
    for pu in player_urls_to_probe:
        pr = {"url": pu, "status": None}
        try:
            resp = fetch_text(pu)
            page = resp["text"]
            pr["status"] = "fetched"
            pr["final_url"] = resp["final_url"]
            pr["player_embeds"] = extract_player_embeds(page)
            pr["audio_candidates"] = extract_audio_urls(page)
            pr["numeric_ids"] = extract_numeric_ids(page)
            all_audio.extend(pr["audio_candidates"])
        except Exception as exc:
            pr["status"] = "error"
            pr["error"] = str(exc)
        diag["player_probes"].append(pr)

    # Also probe Wayback for any discovered player URLs
    for pu in player_urls_to_probe[:5]:
        rows = wayback_cdx_url_exact(pu, limit=5)
        for row in rows[:2]:
            ts = row.get("timestamp", "")
            orig = row.get("original", "")
            if not ts or not orig:
                continue
            arch = f"https://web.archive.org/web/{ts}id_/{orig}"
            cap = analyse_capture(arch, orig, ts, reference_date, reference_title)
            cap["source"] = "wayback_player_probe"
            diag["player_probes"].append(cap)
            all_audio.extend(cap.get("audio_candidates", []))

    # ------------------------------------------------------------------
    # Step 6: Validate all discovered audio candidates
    # ------------------------------------------------------------------
    all_audio = unique(all_audio)
    tested = []
    validated = []
    for candidate_url in all_audio[:MAX_AUDIO_CANDIDATES]:
        check = validate_audio_candidate_live(candidate_url, reference_date)
        tested.append(check)
        if check.get("valid_npr_indicator_audio"):
            validated.append(check)

    diag["audio_candidates_tested"] = tested
    diag["validated_audio"] = validated
    diag["counts"]["audio_candidates_tested"] = len(tested)
    diag["counts"]["audio_validated"] = len(validated)

    # ------------------------------------------------------------------
    # Step 7: Final classification
    # ------------------------------------------------------------------
    diag["discovery_exhausted_for_window"] = True  # we probed all planned patterns

    if validated:
        diag["final_classification"] = "recovered"
        diag["validation_summary"] = (
            f"RECOVERED: {len(validated)} validated NPR Indicator audio "
            f"file(s) confirmed for {reference_date}."
        )
    elif best_identity:
        diag["final_classification"] = "identity_found_audio_unresolved"
        diag["validation_summary"] = (
            f"Identity candidate found (verdict={best_identity.get('match_score', {}).get('verdict')}) "
            f"but no validated NPR Indicator audio. "
            f"Tested {len(tested)} audio candidates."
        )
    elif diag["identity_candidates"]:
        diag["final_classification"] = "partial_identity_no_audio"
        diag["validation_summary"] = (
            f"Partial identity candidates found ({len(diag['identity_candidates'])}) "
            f"but none confirmed (title+date+indicator). No audio validated."
        )
    else:
        diag["final_classification"] = "no_identity_found"
        diag["validation_summary"] = (
            f"CDX date-window and slug probes exhausted. "
            f"No NPR Indicator story page found for {reference_date} with title similarity >= {TITLE_MATCH_THRESHOLD}. "
            f"Tested {len(tested)} audio candidates."
        )

    return diag


# ---------------------------------------------------------------------------
# Placeholders for output files (written before investigation, overwritten after)
# ---------------------------------------------------------------------------


def _placeholder_diag(target: dict) -> dict:
    return {
        "run_state": "placeholder",
        "reference_date": target["reference_date"],
        "reference_title": target["reference_title"],
        "final_classification": None,
    }


def _placeholder_summary(targets: list) -> dict:
    return {
        "run_state": "placeholder",
        "method": "fresh-identity-discovery",
        "generated_at": _now_iso(),
        "episodes": [
            {
                "reference_date": t["reference_date"],
                "reference_title": t["reference_title"],
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
    return f"fresh_identity_discovery_{reference_date}_diag.json"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run():
    # Write placeholder files immediately so the workflow can always upload
    for t in TARGETS:
        path = BASE_DIR / output_filename(t["reference_date"])
        if not path.exists():
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(_placeholder_diag(t), fh, indent=2, ensure_ascii=False)

    summary_path = BASE_DIR / SUMMARY_OUTPUT
    if not summary_path.exists():
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(_placeholder_summary(TARGETS), fh, indent=2, ensure_ascii=False)

    summary = {
        "run_state": "run_complete",
        "method": "fresh-identity-discovery",
        "generated_at": _now_iso(),
        "targets_investigated": len(TARGETS),
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
                "run_state": "failed",
                "reference_date": ref_date,
                "reference_title": ref_title,
                "error": str(exc),
                "final_classification": "failed",
                "validation_summary": f"Investigation failed: {exc}",
            }
            summary["counts"]["failed"] += 1
        else:
            summary["counts"]["completed"] += 1
            if diag.get("final_classification") == "recovered":
                summary["counts"]["recovered"] += 1

        out_path = BASE_DIR / output_filename(ref_date)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(diag, fh, indent=2, ensure_ascii=False)
        print(f"  → Written: {out_path.name}")
        print(f"  → {diag.get('validation_summary') or diag.get('final_classification')}")

        summary["episodes"].append({
            "reference_date": ref_date,
            "reference_title": ref_title,
            "final_classification": diag.get("final_classification"),
            "validation_summary": diag.get("validation_summary"),
            "counts": diag.get("counts", {}),
        })

    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print(f"\nSummary written: {summary_path.name}")
    return summary


if __name__ == "__main__":
    run()
