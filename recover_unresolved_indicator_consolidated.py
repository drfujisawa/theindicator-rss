#!/usr/bin/env python3

import html
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


INPUT_FILE = "indicator_completeness_audit.json"
OUTPUT_LEDGER_FILE = "indicator_unresolved_consolidated_evidence_ledger.json"
OUTPUT_AUDIT_FILE = "indicator_unresolved_consolidated_audit.json"

PRIOR_ARTIFACT_FILES = [
    "indicator_unresolved_batch_recovery.json",
    "indicator_unresolved_strict_review.json",
    "indicator_unresolved_web_discovery.json",
    "indicator_unresolved_web_audio_strict_validation.json",
    "indicator_unresolved_affiliate_recovery_00_09.json",
    "indicator_unresolved_affiliate_recovery_10_19.json",
    "indicator_unresolved_affiliate_recovery_20_49.json",
    "indicator_npr_story_found_recovery.json",
    "indicator_npr_story_identity_strict.json",
    "indicator_recovery_test.json",
    "indicator_recovery_validation.json",
]

AFFILIATE_DOMAIN_MARKERS = [
    "wbur.org",
    "wypr.org",
    "wamu.org",
    "kuow.org",
    "kqed.org",
    "mprnews.org",
    "delmarvapublicmedia.org",
    "wunc.org",
    "wbez.org",
    "knpr.org",
    "kpbs.org",
    "kcur.org",
    "wfdd.org",
    "wfae.org",
    "wesa.fm",
    "wrvo.org",
    "wvik.org",
    "wmra.org",
    "wkms.org",
    "wvxu.org",
    "wbaa.org",
    "wgbh.org",
    "ideastream.org",
    "ncpr.org",
    "kcrw.com",
    "wnyc.org",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; IndicatorConsolidatedRecovery/1.0)"
    )
}

TIMEOUT = 30
RETRIES = 3
REQUEST_DELAY = 0.1
MAX_AFFILIATE_PAGES_PER_EPISODE = 3
MAX_NPR_STORY_URLS_PER_EPISODE = 3
MAX_PLAYER_URLS_PER_EPISODE = 4
MAX_WAYBACK_CAPTURES_PER_URL = 2
MAX_AUDIO_CANDIDATES_PER_EPISODE = 12

LIVE_STREAM_MARKERS = [
    "livestream",
    "live-stream",
    "streamtheworld",
    "playerservices.streamtheworld.com",
    "icecast",
    "shoutcast",
    "/live/",
    "radio-stream",
]

AUDIO_EXTENSIONS = (
    ".mp3",
    ".m4a",
    ".aac",
    ".mp4",
)


def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def save_json(filename, payload):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False,
        )


def clean_text(value):
    if value is None:
        return ""

    value = html.unescape(str(value))
    value = value.replace("\\/", "/")
    value = value.replace("\\u0026", "&")
    value = value.replace("\\u003a", ":")
    value = value.replace("\\u002f", "/")

    return value


def normalize_title(value):
    value = clean_text(value).lower()
    value = value.replace("&", " and ")
    value = value.replace("’", "'")
    value = value.replace("‘", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def normalize_date(value):
    if not value:
        return None

    match = re.search(
        r"((?:19|20)\d{2})-(\d{2})-(\d{2})",
        str(value),
    )

    if not match:
        return None

    return "-".join(match.groups())


def unique(values):
    output = []

    for value in values:
        if value and value not in output:
            output.append(value)

    return output


def hostname(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def path_lower(url):
    try:
        return (urlparse(url).path or "").lower()
    except Exception:
        return ""


def is_npr_story_url(url):
    host = hostname(url)
    path = path_lower(url)
    return (
        host.endswith("npr.org")
        and "/player/" not in path
        and "/transcripts/" not in path
    )


def is_npr_player_url(url):
    host = hostname(url)
    path = path_lower(url)
    return host.endswith("npr.org") and "/player/embed/" in path


def is_wayback_url(url):
    return hostname(url) == "web.archive.org"


def looks_like_affiliate_url(url):
    host = hostname(url)

    if not host or host.endswith("npr.org") or is_wayback_url(url):
        return False

    return any(
        marker in host
        for marker in AFFILIATE_DOMAIN_MARKERS
    )


def looks_like_livestream(url):
    lower = clean_text(url).lower()
    return any(marker in lower for marker in LIVE_STREAM_MARKERS)


def clean_url(value):
    value = clean_text(value).strip("\"' ")

    if not value.startswith(("http://", "https://")):
        return None

    return value


def title_similarity(a, b):
    a = normalize_title(a)
    b = normalize_title(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    a_words = a.split()
    b_words = b.split()
    overlap = len(set(a_words) & set(b_words))
    total = len(set(a_words) | set(b_words))

    if not total:
        return 0.0

    prefix_bonus = 0.2 if (a in b or b in a) else 0.0
    return round(min(1.0, (overlap / total) + prefix_bonus), 3)


def build_aliases(reference_title, matched_titles):
    aliases = [
        clean_text(reference_title).strip(),
        clean_text(reference_title).replace("&", "and").strip(),
    ]

    aliases.extend(clean_text(value).strip() for value in matched_titles)

    return unique(
        value
        for value in aliases
        if value
    )


def episode_date(item):
    if not isinstance(item, dict):
        return None

    for key in [
        "reference_date",
        "date",
        "published",
        "pub_date",
    ]:
        normalized = normalize_date(item.get(key))
        if normalized:
            return normalized

    return None


def episode_title(item):
    if not isinstance(item, dict):
        return None

    for key in [
        "reference_title",
        "title",
        "source_title",
        "npr_title",
    ]:
        value = item.get(key)
        if value:
            return clean_text(value).strip()

    return None


def matches_episode(item, reference_date, reference_title):
    return (
        episode_date(item) == reference_date
        and normalize_title(episode_title(item))
        == normalize_title(reference_title)
    )


def fetch(url, max_bytes=2000000, range_request=False):
    headers = dict(HEADERS)

    if range_request:
        headers["Range"] = "bytes=0-4095"

    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            request = Request(url, headers=headers)

            with urlopen(request, timeout=TIMEOUT) as response:
                data = response.read(
                    4096
                    if range_request
                    else max_bytes
                )

                return {
                    "status_code": getattr(response, "status", None),
                    "final_url": response.geturl(),
                    "content_type": response.headers.get(
                        "Content-Type",
                        ""
                    ),
                    "data": data,
                }

        except Exception as exc:
            last_error = exc

            if attempt < RETRIES:
                time.sleep(attempt)

    raise last_error


def fetch_text(url):
    response = fetch(url)
    response["text"] = response["data"].decode(
        "utf-8",
        errors="replace",
    )
    return response


def extract_meta(page):
    page = clean_text(page)

    title_match = re.search(
        r"<title[^>]*>(.*?)</title>",
        page,
        re.I | re.S,
    )

    html_title = None

    if title_match:
        html_title = re.sub(
            r"\s+",
            " ",
            title_match.group(1),
        ).strip()

    fields = {
        "html_title": html_title,
        "og_title": None,
        "canonical": None,
        "dates": [],
    }

    patterns = [
        (
            "og_title",
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
        ),
        (
            "og_title",
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        ),
        (
            "canonical",
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
        ),
        (
            "canonical",
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
        ),
    ]

    for key, pattern in patterns:
        match = re.search(pattern, page, re.I)
        if match and not fields[key]:
            fields[key] = clean_text(match.group(1)).strip()

    for pattern in [
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"dateModified"\s*:\s*"([^"]+)"',
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        r'<time[^>]+datetime=["\']([^"\']+)',
    ]:
        for value in re.findall(pattern, page, re.I):
            normalized = normalize_date(value)
            if normalized:
                fields["dates"].append(normalized)

    fields["dates"] = unique(fields["dates"])
    return fields


def extract_hidden_ids(page):
    page = clean_text(page)

    fields = {
        "npr_story_ids": [],
        "npr_player_story_ids": [],
        "npr_audio_ids": [],
    }

    patterns = {
        "npr_story_ids": [
            r'"nprStoryId"\s*:\s*"?(?P<id>\d{5,})"?',
            r'"storyId"\s*:\s*"?(?P<id>\d{5,})"?',
            r'"story_id"\s*:\s*"?(?P<id>\d{5,})"?',
        ],
        "npr_player_story_ids": [
            r'/player/embed/(?P<id>\d{5,})/\d{5,}',
            r'"player_story_id"\s*:\s*"?(?P<id>\d{5,})"?',
        ],
        "npr_audio_ids": [
            r'"audioId"\s*:\s*"?(?P<id>\d{5,})"?',
            r'"audio_id"\s*:\s*"?(?P<id>\d{5,})"?',
            r'/player/embed/\d{5,}/(?P<id>\d{5,})',
        ],
    }

    for field, field_patterns in patterns.items():
        for pattern in field_patterns:
            for match in re.finditer(pattern, page, re.I):
                value = match.group("id")
                if value and value not in fields[field]:
                    fields[field].append(value)

    return fields


def extract_npr_story_urls(page):
    page = clean_text(page)

    values = re.findall(
        r'https?://(?:www\.)?npr\.org/[^"\'<>\s\\]+',
        page,
        re.I,
    )

    cleaned = [
        clean_url(value.rstrip('.,;:)]}>"\''))
        for value in values
    ]

    return unique(
        value
        for value in cleaned
        if value and is_npr_story_url(value)
    )


def extract_player_urls(page):
    page = clean_text(page)

    values = re.findall(
        r'(?:https?://(?:www\.)?npr\.org)?/player/embed/\d+/\d+',
        page,
        re.I,
    )

    normalized = []

    for value in values:
        value = clean_text(value)

        if value.startswith("/"):
            value = "https://www.npr.org" + value

        normalized.append(clean_url(value))

    return unique(
        value
        for value in normalized
        if value and is_npr_player_url(value)
    )


def extract_audio_urls(page):
    page = clean_text(page)

    patterns = [
        r'https?://ondemand\.npr\.org/[^"\'<>\s\\]+',
        r'https?://[^"\'<>\s\\]+\.mp3(?:\?[^"\'<>\s\\]*)?',
        r'https?://[^"\'<>\s\\]+\.m4a(?:\?[^"\'<>\s\\]*)?',
        r'https?://[^"\'<>\s\\]+\.aac(?:\?[^"\'<>\s\\]*)?',
    ]

    found = []

    for pattern in patterns:
        found.extend(
            clean_url(value.rstrip('.,;:)]}>"\''))
            for value in re.findall(pattern, page, re.I)
        )

    return unique(value for value in found if value)


def score_page_match(
    expected_title,
    expected_date,
    metadata,
    url,
):
    candidate_titles = unique([
        metadata.get("og_title"),
        metadata.get("html_title"),
    ])

    best_title = None
    best_similarity = 0.0

    for candidate in candidate_titles:
        similarity = title_similarity(
            expected_title,
            candidate,
        )

        if similarity > best_similarity:
            best_similarity = similarity
            best_title = candidate

    date_match = expected_date in metadata.get("dates", [])
    url_date_match = expected_date.replace("-", "/") in clean_text(url)

    score = 0
    reasons = []

    if best_similarity >= 0.92:
        score += 6
        reasons.append("near_exact_title")
    elif best_similarity >= 0.75:
        score += 4
        reasons.append("strong_title_match")
    elif best_similarity >= 0.60:
        score += 2
        reasons.append("moderate_title_match")

    if date_match:
        score += 4
        reasons.append("exact_date")
    elif url_date_match:
        score += 2
        reasons.append("date_in_url")

    qualified = (
        (best_similarity >= 0.75 and (date_match or url_date_match))
        or best_similarity >= 0.92
    )

    return {
        "qualified": qualified,
        "score": score,
        "best_title": best_title,
        "title_similarity": best_similarity,
        "dates": metadata.get("dates", []),
        "date_match": date_match,
        "url_date_match": url_date_match,
        "canonical": metadata.get("canonical"),
        "reasons": reasons,
    }


def classify_audio_candidate(
    candidate_url,
    final_url=None,
    content_type="",
    status_code=None,
):
    candidate_url = clean_url(candidate_url) or ""
    final_url = clean_url(final_url) or candidate_url
    lower_final = final_url.lower()
    lower_candidate = candidate_url.lower()
    content_type = (content_type or "").lower()

    if looks_like_livestream(candidate_url) or looks_like_livestream(final_url):
        return {
            "status": "rejected_livestream",
            "accepted": False,
            "reason": "livestream_or_station_stream",
        }

    success = status_code in {200, 206}

    if not success:
        return {
            "status": "rejected_bad_response",
            "accepted": False,
            "reason": "http_or_transport_failure",
        }

    is_audio = (
        content_type.startswith("audio/")
        or "mpeg" in content_type
        or "mp3" in content_type
        or "aac" in content_type
        or "m4a" in content_type
        or "octet-stream" in content_type
    )

    if not is_audio:
        return {
            "status": "rejected_non_audio_response",
            "accepted": False,
            "reason": "content_type_not_audio",
        }

    if "ondemand.npr.org" not in lower_final:
        return {
            "status": "rejected_non_npr_audio",
            "accepted": False,
            "reason": "audio_not_hosted_by_npr",
        }

    if (
        "/indicator/" not in lower_final
        and "/indicator/" not in lower_candidate
    ):
        return {
            "status": "rejected_generic_audio",
            "accepted": False,
            "reason": "npr_audio_not_indicator_specific",
        }

    if not any(
        path_lower(final_url).endswith(ext)
        for ext in AUDIO_EXTENSIONS
    ):
        return {
            "status": "rejected_generic_audio",
            "accepted": False,
            "reason": "npr_audio_not_episode_file",
        }

    return {
        "status": "validated_npr_episode_audio",
        "accepted": True,
        "reason": "working_npr_indicator_audio",
    }


def validate_audio_candidate(entry):
    candidate_url = clean_url(entry.get("url"))

    result = {
        "candidate_url": candidate_url,
        "discovered_from": entry.get("discovered_from"),
        "source_type": entry.get("source_type"),
        "source_url": entry.get("source_url"),
        "validation_status": None,
    }

    if not candidate_url:
        result["validation_status"] = "rejected_empty_candidate"
        result["reason"] = "missing_candidate_url"
        return result

    if looks_like_livestream(candidate_url):
        result["validation_status"] = "rejected_livestream"
        result["reason"] = "livestream_or_station_stream"
        return result

    try:
        response = fetch(candidate_url, range_request=True)
    except Exception as exc:
        result["validation_status"] = "rejected_request_error"
        result["reason"] = str(exc)
        return result

    result.update({
        "status_code": response.get("status_code"),
        "final_url": response.get("final_url"),
        "content_type": response.get("content_type"),
        "sample_size": len(response.get("data", b"")),
    })

    classification = classify_audio_candidate(
        candidate_url,
        final_url=response.get("final_url"),
        content_type=response.get("content_type"),
        status_code=response.get("status_code"),
    )

    result["validation_status"] = classification["status"]
    result["reason"] = classification["reason"]
    result["accepted"] = classification["accepted"]

    time.sleep(REQUEST_DELAY)
    return result


def wayback_cdx(url):
    query = (
        "https://web.archive.org/cdx/search/cdx"
        "?url="
        + quote(url, safe="")
        + "&output=json"
        + "&filter=statuscode:200"
        + "&collapse=digest"
        + "&fl=timestamp,original"
        + "&limit=10"
    )

    try:
        response = fetch_text(query)
        payload = json.loads(response["text"])
    except Exception:
        return []

    if not isinstance(payload, list) or len(payload) < 2:
        return []

    header = payload[0]

    return [
        dict(zip(header, row))
        for row in payload[1:]
    ]


def build_archive_url(timestamp, original):
    return (
        "https://web.archive.org/web/"
        f"{timestamp}id_/"
        f"{original}"
    )


def append_source_record(container, record, dedupe_key):
    key = dedupe_key(record)

    if not key:
        return

    if key in container["_seen"]:
        return

    container["_seen"].add(key)
    container["items"].append(record)


def page_record(url, source_file, source_path, source_kind):
    return {
        "url": clean_url(url),
        "source_file": source_file,
        "source_path": source_path,
        "source_kind": source_kind,
    }


def audio_record(
    url,
    source_file,
    source_path,
    source_kind,
    source_url=None,
):
    return {
        "url": clean_url(url),
        "discovered_from": source_file,
        "source_type": source_kind,
        "source_url": clean_url(source_url),
        "source_path": source_path,
    }


def create_ledger(reference):
    return {
        "reference_date": reference["date"],
        "reference_title": reference["title"],
        "reference_year": reference.get("reference_year"),
        "reference_episode": reference.get("reference_episode"),
        "aliases": [],
        "affiliate_pages": [],
        "npr_story_ids": [],
        "npr_player_story_ids": [],
        "npr_audio_ids": [],
        "npr_story_urls": [],
        "player_urls": [],
        "archive_captures": [],
        "candidate_audio_urls": [],
        "validation_results": [],
        "current_affiliate_pages": [],
        "current_npr_story_pages": [],
        "current_player_pages": [],
        "prior_evidence": [],
        "final_status": None,
        "evidence_confidence_explanation": "",
        "duplicate_reference_dates": unique(
            reference.get("duplicate_reference_dates", [])
            if isinstance(reference.get("duplicate_reference_dates"), list)
            else []
        ),
    }


def walk_episode_matches(node, reference_date, reference_title, path="root"):
    matches = []

    if isinstance(node, dict):
        if matches_episode(node, reference_date, reference_title):
            matches.append((path, node))

        for key, value in node.items():
            matches.extend(
                walk_episode_matches(
                    value,
                    reference_date,
                    reference_title,
                    f"{path}.{key}",
                )
            )

    elif isinstance(node, list):
        for index, value in enumerate(node):
            matches.extend(
                walk_episode_matches(
                    value,
                    reference_date,
                    reference_title,
                    f"{path}[{index}]",
                )
            )

    return matches


def add_id_values(ledger, key, values):
    current = ledger[key]

    for value in values:
        value = clean_text(value).strip()
        if value and value not in current:
            current.append(value)


def extract_ids_from_mapping(mapping):
    story_ids = []
    player_story_ids = []
    audio_ids = []

    if not isinstance(mapping, dict):
        return story_ids, player_story_ids, audio_ids

    for key, value in mapping.items():
        if value in [None, "", []]:
            continue

        normalized_key = key.lower()
        values = value if isinstance(value, list) else [value]

        for item in values:
            item = clean_text(item).strip()
            if not re.fullmatch(r"\d{5,}", item):
                continue

            if normalized_key in {"player_story_id"}:
                player_story_ids.append(item)
            elif normalized_key in {"audio_id", "npr_audio_id"}:
                audio_ids.append(item)
            elif "story" in normalized_key:
                story_ids.append(item)

    return unique(story_ids), unique(player_story_ids), unique(audio_ids)


def seed_from_matched_record(
    ledger,
    source_file,
    source_path,
    item,
):
    ledger["prior_evidence"].append({
        "file": source_file,
        "path": source_path,
        "status": item.get("status")
        or item.get("strict_status")
        or item.get("validation_status"),
    })

    duplicate_dates = item.get("duplicate_reference_dates", [])

    if isinstance(duplicate_dates, list):
        ledger["duplicate_reference_dates"] = unique(
            ledger["duplicate_reference_dates"]
            + [
                normalize_date(value)
                for value in duplicate_dates
                if normalize_date(value)
            ]
        )

    aliases = []

    for key in [
        "source_title",
        "title",
        "reference_title",
        "npr_title",
    ]:
        if item.get(key):
            aliases.append(item.get(key))

    add_id_values(
        ledger,
        "npr_story_ids",
        item.get("npr_story_ids", []),
    )
    add_id_values(
        ledger,
        "npr_player_story_ids",
        item.get("npr_player_story_ids", []),
    )
    add_id_values(
        ledger,
        "npr_audio_ids",
        item.get("npr_audio_ids", []),
    )

    for key in ["known_ids", "ids", "possible_ids"]:
        story_ids, player_story_ids, audio_ids = extract_ids_from_mapping(
            item.get(key)
        )
        add_id_values(ledger, "npr_story_ids", story_ids)
        add_id_values(ledger, "npr_player_story_ids", player_story_ids)
        add_id_values(ledger, "npr_audio_ids", audio_ids)

    for url in item.get("npr_story_urls", []) or []:
        url = clean_url(url)
        if url and url not in ledger["npr_story_urls"]:
            ledger["npr_story_urls"].append(url)

    for key in ["player_urls", "npr_player_embeds", "player_embeds"]:
        for url in item.get(key, []) or []:
            url = clean_url(url)
            if url and url not in ledger["player_urls"]:
                ledger["player_urls"].append(url)

    for url in item.get("episode_audio_candidates", []) or []:
        record = audio_record(
            url,
            source_file,
            source_path,
            "episode_audio_candidates",
        )
        if record["url"]:
            ledger["candidate_audio_urls"].append(record)

    for evidence in item.get("prior_evidence", []) or []:
        for url in evidence.get("urls", []) or []:
            record = audio_record(
                url,
                source_file,
                source_path,
                "prior_evidence_url",
            )

            if record["url"]:
                ledger["candidate_audio_urls"].append(record)

        story_ids, player_story_ids, audio_ids = extract_ids_from_mapping(
            evidence.get("ids")
        )
        add_id_values(ledger, "npr_story_ids", story_ids)
        add_id_values(ledger, "npr_player_story_ids", player_story_ids)
        add_id_values(ledger, "npr_audio_ids", audio_ids)

    for report_key in ["page_reports", "qualified_pages"]:
        for report in item.get(report_key, []) or []:
            for url_key in ["requested_url", "final_url"]:
                url = clean_url(report.get(url_key))
                if not url:
                    continue

                if looks_like_affiliate_url(url):
                    ledger["affiliate_pages"].append(
                        page_record(
                            url,
                            source_file,
                            source_path,
                            report_key,
                        )
                    )
                elif is_npr_story_url(url):
                    if url not in ledger["npr_story_urls"]:
                        ledger["npr_story_urls"].append(url)
                elif is_npr_player_url(url):
                    if url not in ledger["player_urls"]:
                        ledger["player_urls"].append(url)

            for url in report.get("npr_story_urls", []) or []:
                url = clean_url(url)
                if url and url not in ledger["npr_story_urls"]:
                    ledger["npr_story_urls"].append(url)

            for url in report.get("player_embeds", []) or []:
                url = clean_url(url)
                if url and url not in ledger["player_urls"]:
                    ledger["player_urls"].append(url)

            for url in report.get("audio_candidates", []) or []:
                record = audio_record(
                    url,
                    source_file,
                    source_path,
                    report_key,
                    source_url=(
                        report.get("final_url")
                        or report.get("requested_url")
                    ),
                )
                if record["url"]:
                    ledger["candidate_audio_urls"].append(record)

    for story_page in item.get("story_pages", []) or []:
        url = clean_url(story_page.get("url") or story_page.get("final_url"))
        if url and url not in ledger["npr_story_urls"]:
            ledger["npr_story_urls"].append(url)

        for url in story_page.get("player_embeds", []) or []:
            url = clean_url(url)
            if url and url not in ledger["player_urls"]:
                ledger["player_urls"].append(url)

        for url in story_page.get("audio_candidates", []) or []:
            record = audio_record(
                url,
                source_file,
                source_path,
                "story_pages",
                source_url=(
                    story_page.get("final_url")
                    or story_page.get("url")
                ),
            )
            if record["url"]:
                ledger["candidate_audio_urls"].append(record)

    for player_page in item.get("player_pages", []) or []:
        url = clean_url(
            player_page.get("player_url")
            or player_page.get("final_url")
        )

        if url and url not in ledger["player_urls"]:
            ledger["player_urls"].append(url)

        add_id_values(
            ledger,
            "npr_player_story_ids",
            [player_page.get("player_story_id")],
        )
        add_id_values(
            ledger,
            "npr_audio_ids",
            [player_page.get("audio_id")],
        )

        for url in player_page.get("audio_candidates", []) or []:
            record = audio_record(
                url,
                source_file,
                source_path,
                "player_pages",
                source_url=(
                    player_page.get("final_url")
                    or player_page.get("player_url")
                ),
            )
            if record["url"]:
                ledger["candidate_audio_urls"].append(record)

    for key in ["validated_audio", "npr_indicator_audio", "non_npr_audio"]:
        for audio in item.get(key, []) or []:
            if isinstance(audio, str):
                url = audio
            else:
                url = (
                    audio.get("final_url")
                    or audio.get("candidate_url")
                    or audio.get("audio_url")
                    or audio.get("url")
                )

                add_id_values(
                    ledger,
                    "npr_story_ids",
                    [audio.get("npr_story_id")],
                )

            record = audio_record(
                url,
                source_file,
                source_path,
                key,
            )
            if record["url"]:
                ledger["candidate_audio_urls"].append(record)

    collection_keys = [
        "wayback_players",
        "wayback_story_reports",
        "wayback_player_reports",
    ]

    for collection_key in collection_keys:
        for row in item.get(collection_key, []) or []:
            captures = row.get("captures") if isinstance(row, dict) else None

            if captures is None and isinstance(row, dict):
                captures = [row]

            for capture in captures or []:
                archive_url = clean_url(capture.get("archive_url"))
                if archive_url:
                    ledger["archive_captures"].append({
                        "archive_url": archive_url,
                        "original_url": clean_url(capture.get("original_url"))
                        or clean_url(row.get("player_url"))
                        or clean_url(row.get("story_url")),
                        "timestamp": capture.get("timestamp"),
                        "source_file": source_file,
                        "source_path": source_path,
                    })

                for url in capture.get("player_embeds", []) or []:
                    url = clean_url(url)
                    if url and url not in ledger["player_urls"]:
                        ledger["player_urls"].append(url)

                for url in capture.get("audio_candidates", []) or []:
                    record = audio_record(
                        url,
                        source_file,
                        source_path,
                        collection_key,
                        source_url=archive_url,
                    )
                    if record["url"]:
                        ledger["candidate_audio_urls"].append(record)

    ledger["aliases"] = build_aliases(
        ledger["reference_title"],
        ledger["aliases"] + aliases,
    )


def seed_prior_evidence(ledger):
    matched_titles = []

    for filename in PRIOR_ARTIFACT_FILES:
        payload = load_json(filename)
        matches = walk_episode_matches(
            payload,
            ledger["reference_date"],
            ledger["reference_title"],
        )

        for source_path, item in matches:
            title = episode_title(item)
            if title:
                matched_titles.append(title)

            seed_from_matched_record(
                ledger,
                filename,
                source_path,
                item,
            )

    ledger["aliases"] = build_aliases(
        ledger["reference_title"],
        matched_titles + ledger["aliases"],
    )
    ledger["affiliate_pages"] = unique_dicts(
        ledger["affiliate_pages"],
        lambda item: item.get("url"),
    )
    ledger["candidate_audio_urls"] = unique_dicts(
        ledger["candidate_audio_urls"],
        lambda item: (
            item.get("url"),
            item.get("source_type"),
            item.get("source_path"),
        ),
    )
    ledger["archive_captures"] = unique_dicts(
        ledger["archive_captures"],
        lambda item: item.get("archive_url"),
    )


def unique_dicts(values, keyfunc):
    output = []
    seen = set()

    for value in values:
        key = keyfunc(value)
        if key and key not in seen:
            seen.add(key)
            output.append(value)

    return output


def investigate_page(
    ledger,
    url,
    source_type,
    source_url=None,
    archive_context=False,
):
    record = {
        "requested_url": url,
        "source_type": source_type,
        "source_url": source_url,
        "status": None,
    }

    try:
        response = fetch_text(url)
    except Exception as exc:
        record["status"] = "error"
        record["error"] = str(exc)
        return record

    metadata = extract_meta(response["text"])
    hidden_ids = extract_hidden_ids(response["text"])
    story_urls = extract_npr_story_urls(response["text"])
    player_urls = extract_player_urls(response["text"])
    audio_urls = extract_audio_urls(response["text"])
    page_score = score_page_match(
        ledger["reference_title"],
        ledger["reference_date"],
        metadata,
        response["final_url"],
    )

    record.update({
        "status": "fetched",
        "status_code": response.get("status_code"),
        "final_url": response.get("final_url"),
        "metadata": metadata,
        "page_match": page_score,
        "hidden_ids": hidden_ids,
        "npr_story_urls": story_urls,
        "player_urls": player_urls,
        "audio_urls": audio_urls,
        "archive_context": archive_context,
    })

    add_id_values(ledger, "npr_story_ids", hidden_ids["npr_story_ids"])
    add_id_values(
        ledger,
        "npr_player_story_ids",
        hidden_ids["npr_player_story_ids"],
    )
    add_id_values(ledger, "npr_audio_ids", hidden_ids["npr_audio_ids"])

    for story_url in story_urls:
        if story_url not in ledger["npr_story_urls"]:
            ledger["npr_story_urls"].append(story_url)

    for player_url in player_urls:
        if player_url not in ledger["player_urls"]:
            ledger["player_urls"].append(player_url)

    for audio_url in audio_urls:
        ledger["candidate_audio_urls"].append({
            "url": audio_url,
            "discovered_from": source_type,
            "source_type": source_type,
            "source_url": response["final_url"],
        })

    return record


def investigate_wayback_pages(ledger, urls, source_type):
    for original_url in urls:
        rows = wayback_cdx(original_url)[:MAX_WAYBACK_CAPTURES_PER_URL]

        for row in rows:
            timestamp = row.get("timestamp")
            original = row.get("original")

            if not timestamp or not original:
                continue

            archive_url = build_archive_url(timestamp, original)

            ledger["archive_captures"].append({
                "archive_url": archive_url,
                "original_url": original,
                "timestamp": timestamp,
                "source_file": "live_wayback_probe",
                "source_path": source_type,
            })

            record = investigate_page(
                ledger,
                archive_url,
                source_type=f"wayback_{source_type}",
                source_url=original_url,
                archive_context=True,
            )

            record["timestamp"] = timestamp

            if "affiliate" in source_type:
                ledger["current_affiliate_pages"].append(record)
            elif "story" in source_type:
                ledger["current_npr_story_pages"].append(record)
            else:
                ledger["current_player_pages"].append(record)


def investigate_affiliate_pages(ledger):
    candidates = unique_dicts(
        [
            item
            for item in ledger["affiliate_pages"]
            if clean_url(item.get("url"))
        ],
        lambda item: item.get("url"),
    )[:MAX_AFFILIATE_PAGES_PER_EPISODE]

    urls = []

    for item in candidates:
        urls.append(item["url"])
        record = investigate_page(
            ledger,
            item["url"],
            source_type="affiliate_page",
        )
        ledger["current_affiliate_pages"].append(record)

    investigate_wayback_pages(
        ledger,
        urls,
        "affiliate_page",
    )


def investigate_npr_pages(ledger):
    story_urls = unique(ledger["npr_story_urls"])[:MAX_NPR_STORY_URLS_PER_EPISODE]

    for url in story_urls:
        record = investigate_page(
            ledger,
            url,
            source_type="npr_story_page",
        )
        ledger["current_npr_story_pages"].append(record)

    investigate_wayback_pages(
        ledger,
        story_urls,
        "npr_story_page",
    )

    player_urls = unique(ledger["player_urls"])[:MAX_PLAYER_URLS_PER_EPISODE]

    for url in player_urls:
        record = investigate_page(
            ledger,
            url,
            source_type="npr_player_page",
        )
        ledger["current_player_pages"].append(record)

    investigate_wayback_pages(
        ledger,
        player_urls,
        "npr_player_page",
    )


def credible_identity_present(ledger):
    return bool(
        ledger["npr_story_ids"]
        or ledger["npr_player_story_ids"]
        or ledger["npr_story_urls"]
        or ledger["player_urls"]
    )


def validate_episode_audio(ledger):
    candidates = unique_dicts(
        ledger["candidate_audio_urls"],
        lambda item: item.get("url"),
    )[:MAX_AUDIO_CANDIDATES_PER_EPISODE]

    for candidate in candidates:
        result = validate_audio_candidate(candidate)
        ledger["validation_results"].append(result)


def determine_episode_status(ledger):
    if ledger.get("duplicate_reference_dates"):
        explanation = (
            "Reference audit links this episode to duplicate/rebroadcast "
            f"date(s) {', '.join(ledger['duplicate_reference_dates'])}. "
            "No independent NPR identity chain was established to count "
            "both entries as separate missing productions."
        )

        return (
            "probable_duplicate_rebroadcast",
            explanation,
        )

    valid_audio = [
        item
        for item in ledger["validation_results"]
        if item.get("validation_status")
        == "validated_npr_episode_audio"
    ]

    strong_page_matches = 0

    for collection_name in [
        "current_affiliate_pages",
        "current_npr_story_pages",
        "current_player_pages",
    ]:
        for page in ledger.get(collection_name, []):
            if page.get("status") != "fetched":
                continue

            if page.get("page_match", {}).get("qualified"):
                strong_page_matches += 1

    if valid_audio and (
        strong_page_matches
        or credible_identity_present(ledger)
    ):
        explanation = (
            "Recovered conservatively because at least one working "
            "NPR-hosted ondemand audio file survived validation and "
            "the episode also has corroborating NPR identity/page evidence."
        )

        return (
            "confirmed_recovered",
            explanation,
        )

    if credible_identity_present(ledger):
        rejected = [
            item["validation_status"]
            for item in ledger["validation_results"]
            if item.get("validation_status", "").startswith("rejected_")
        ]

        if rejected:
            detail = (
                " Candidate audio was rejected as "
                + ", ".join(unique(rejected))
                + "."
            )
        else:
            detail = ""

        explanation = (
            "Credible NPR identity evidence was found "
            "(story/page/player IDs or URLs), but no strong "
            "episode-specific working NPR-hosted audio file could be "
            f"validated.{detail}"
        )

        return (
            "identity_found_but_audio_unresolved",
            explanation,
        )

    explanation = (
        "No credible NPR identity was found after consolidating prior "
        "evidence and inspecting current/archived affiliate pages."
    )

    return (
        "no_identity_found",
        explanation,
    )


def build_false_positive_rows(ledger):
    rows = []

    for item in ledger.get("validation_results", []):
        status = item.get("validation_status") or ""

        if not status.startswith("rejected_"):
            continue

        rows.append({
            "reference_date": ledger["reference_date"],
            "reference_title": ledger["reference_title"],
            "candidate_url": item.get("candidate_url"),
            "final_url": item.get("final_url"),
            "validation_status": status,
            "reason": item.get("reason"),
            "source_type": item.get("source_type"),
            "source_url": item.get("source_url"),
        })

    return rows


def process_episode(reference):
    ledger = create_ledger(reference)
    seed_prior_evidence(ledger)
    investigate_affiliate_pages(ledger)

    if credible_identity_present(ledger):
        investigate_npr_pages(ledger)

    ledger["candidate_audio_urls"] = unique_dicts(
        ledger["candidate_audio_urls"],
        lambda item: (
            item.get("url"),
            item.get("source_type"),
            item.get("source_url"),
        ),
    )
    ledger["affiliate_pages"] = unique_dicts(
        ledger["affiliate_pages"],
        lambda item: item.get("url"),
    )
    ledger["current_affiliate_pages"] = unique_dicts(
        ledger["current_affiliate_pages"],
        lambda item: (
            item.get("final_url") or item.get("requested_url"),
            item.get("source_type"),
            item.get("timestamp"),
        ),
    )
    ledger["current_npr_story_pages"] = unique_dicts(
        ledger["current_npr_story_pages"],
        lambda item: (
            item.get("final_url") or item.get("requested_url"),
            item.get("source_type"),
            item.get("timestamp"),
        ),
    )
    ledger["current_player_pages"] = unique_dicts(
        ledger["current_player_pages"],
        lambda item: (
            item.get("final_url") or item.get("requested_url"),
            item.get("source_type"),
            item.get("timestamp"),
        ),
    )
    ledger["archive_captures"] = unique_dicts(
        ledger["archive_captures"],
        lambda item: item.get("archive_url"),
    )

    validate_episode_audio(ledger)
    final_status, explanation = determine_episode_status(ledger)
    ledger["final_status"] = final_status
    ledger["evidence_confidence_explanation"] = explanation

    return ledger


def build_audit(ledgers):
    grouped = {
        "confirmed_recovered": [],
        "probable_duplicate_rebroadcast": [],
        "identity_found_but_audio_unresolved": [],
        "no_identity_found": [],
    }

    false_positives = []

    for ledger in ledgers:
        grouped.setdefault(ledger["final_status"], []).append({
            "reference_date": ledger["reference_date"],
            "reference_title": ledger["reference_title"],
            "reference_year": ledger.get("reference_year"),
            "reference_episode": ledger.get("reference_episode"),
            "evidence_confidence_explanation": (
                ledger["evidence_confidence_explanation"]
            ),
        })
        false_positives.extend(build_false_positive_rows(ledger))

    summary = {
        "confirmed_recovered": len(grouped["confirmed_recovered"]),
        "probable_duplicate_rebroadcast": len(
            grouped["probable_duplicate_rebroadcast"]
        ),
        "identity_found_but_audio_unresolved": len(
            grouped["identity_found_but_audio_unresolved"]
        ),
        "no_identity_found": len(grouped["no_identity_found"]),
        "rejected_false_positive_count": len(false_positives),
        "input_unresolved_count": len(ledgers),
        "unique_missing_production_count_excluding_probable_duplicates": (
            len(ledgers)
            - len(grouped["probable_duplicate_rebroadcast"])
        ),
    }

    return {
        "method": "consolidated-recovery-pipeline-for-unresolved-indicator-episodes",
        "generated_at": (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        ),
        "canonical_input": INPUT_FILE,
        "summary": summary,
        "confirmed_recovered": grouped["confirmed_recovered"],
        "probable_duplicate_rebroadcast": grouped[
            "probable_duplicate_rebroadcast"
        ],
        "identity_found_but_audio_unresolved": grouped[
            "identity_found_but_audio_unresolved"
        ],
        "no_identity_found": grouped["no_identity_found"],
        "rejected_false_positives": false_positives,
    }


def load_unresolved_reference_episodes():
    audit = load_json(INPUT_FILE)
    unresolved = audit.get("unresolved_reference_episodes", [])

    if not isinstance(unresolved, list):
        return []

    return unresolved


def main():
    unresolved = load_unresolved_reference_episodes()
    ledgers = []

    print()
    print("================================")
    print("CONSOLIDATED UNRESOLVED RECOVERY")
    print("================================")

    for index, reference in enumerate(unresolved, start=1):
        print(
            f"[{index}/{len(unresolved)}]",
            reference.get("date"),
            reference.get("title"),
        )

        ledger = process_episode(reference)
        ledgers.append(ledger)

        print("  ->", ledger["final_status"])

    audit = build_audit(ledgers)

    save_json(
        OUTPUT_LEDGER_FILE,
        {
            "method": audit["method"],
            "generated_at": audit["generated_at"],
            "canonical_input": INPUT_FILE,
            "summary": audit["summary"],
            "episodes": ledgers,
        },
    )
    save_json(OUTPUT_AUDIT_FILE, audit)

    print()
    print("Summary:")
    for key, value in audit["summary"].items():
        print(f"{key}: {value}")

    print()
    print("Saved:", OUTPUT_LEDGER_FILE)
    print("Saved:", OUTPUT_AUDIT_FILE)
    print("================================")


if __name__ == "__main__":
    main()
