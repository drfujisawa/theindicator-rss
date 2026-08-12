#!/usr/bin/env python3
"""
Targeted narrow recovery probe for the final 2 episodes remaining in
identity_found_but_audio_unresolved after prior batch2 recovery passes.

Outputs:
  - batch2_final3_identity_recovery_<YYYY-MM-DD>_diag.json
  - batch2_final3_identity_recovery_summary.json
"""

import argparse
import datetime
import json
import os
import time
from pathlib import Path

from scripts.recovery import probe_batch2_fresh_identity_discovery as base
REPO_ROOT = Path(__file__).resolve().parents[2]


BASE_DIR = Path(__file__).parent
SUMMARY_OUTPUT = str(REPO_ROOT / "batch2_final3_identity_recovery_summary.json")
REQUEST_TIMEOUT_SECONDS = base.REQUEST_TIMEOUT_SECONDS
WAYBACK_DISCOVERY_RETRIES = base.WAYBACK_DISCOVERY_RETRIES
CONTENT_RETRIES = 2

MAX_DISCOVERY_CDX_QUERIES = 8
MAX_CAPTURE_ATTEMPTS = 10
MAX_SEEDED_EXACT_CDX_URLS = 4
MAX_PLAYER_FETCHES = 2
MAX_ARCHIVED_PLAYER_FETCHES = 2
MAX_ARCHIVED_PLAYER_CDX_QUERIES = MAX_ARCHIVED_PLAYER_FETCHES
MAX_AUDIO_VALIDATIONS = 4

SEED_SOURCE_PRIORITY = {
    "prior_stage_c": 0,
    "prior_identity_candidate": 1,
    "prior_cdx_scored": 2,
}

TARGETS = [
    {
        "reference_date": "2018-10-11",
        "reference_title": "China's Brave New World",
        "reference_episode": 145,
        "section_paths": ["sections/money"],
        "date_offsets": [0],
        "slug_variants": [
            "chinas-brave-new-world",
            "china-brave-new-world",
            "brave-new-world",
            "china-social-control",
        ],
        "blocked_story_ids": {"656978022"},
        "blocked_title_terms": ["student loan whistleblower"],
        "broad_discovery_if_no_identity": True,
    },
    {
        "reference_date": "2018-04-24",
        "reference_title": "When China's Ships Come In",
        "reference_episode": 31,
        "section_paths": ["sections/money"],
        "date_offsets": [0],
        "slug_variants": [
            "when-chinas-ships-come-in",
            "china-ships",
            "china-trade-ships",
            "chinas-ships",
            "ships-come-in",
        ],
        "blocked_story_ids": {"605033696"},
        "blocked_title_terms": ["3 things you didn't know about la"],
        "broad_discovery_if_no_identity": True,
    },
]


def _run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or f"local-{int(time.time())}"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def output_filename(reference_date: str) -> str:
    return f"batch2_final3_identity_recovery_{reference_date}_diag.json"


def _write_json(path: Path, payload: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


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


def _placeholder_summary() -> dict:
    return {
        "placeholder": True,
        "run_complete": False,
        "run_state": "placeholder",
        "run_id": _run_id(),
        "method": "batch2-final3-targeted-recovery",
        "generated_at": _now_iso(),
        "episodes": [
            {
                "reference_date": t["reference_date"],
                "reference_title": t["reference_title"],
                "reference_episode": t["reference_episode"],
            }
            for t in TARGETS
        ],
    }


def write_placeholders():
    for target in TARGETS:
        _write_json(BASE_DIR / output_filename(target["reference_date"]), _placeholder_diag(target))
    _write_json(BASE_DIR / SUMMARY_OUTPUT, _placeholder_summary())


def request_budget() -> dict:
    per_episode = {
        "discovery_cdx_queries": MAX_DISCOVERY_CDX_QUERIES,
        "seeded_exact_cdx_queries": MAX_SEEDED_EXACT_CDX_URLS,
        "capture_attempts": MAX_CAPTURE_ATTEMPTS,
        "live_player_fetches": MAX_PLAYER_FETCHES,
        "archived_player_cdx_queries": MAX_ARCHIVED_PLAYER_CDX_QUERIES,
        "archived_player_fetches": MAX_ARCHIVED_PLAYER_FETCHES,
        "audio_validations": MAX_AUDIO_VALIDATIONS,
    }
    per_episode["max_logical_requests"] = (
        per_episode["discovery_cdx_queries"]
        + per_episode["seeded_exact_cdx_queries"]
        + per_episode["capture_attempts"]
        + per_episode["live_player_fetches"]
        + per_episode["archived_player_cdx_queries"]
        + per_episode["archived_player_fetches"]
        + per_episode["audio_validations"]
    )
    per_episode["conservative_timeout_ceiling_seconds"] = (
        (
            (
                per_episode["discovery_cdx_queries"]
                + per_episode["seeded_exact_cdx_queries"]
                + per_episode["archived_player_cdx_queries"]
            )
            * REQUEST_TIMEOUT_SECONDS
            * (WAYBACK_DISCOVERY_RETRIES + 1)
        )
        + (
            (
                per_episode["capture_attempts"]
                + per_episode["live_player_fetches"]
                + per_episode["archived_player_fetches"]
                + per_episode["audio_validations"]
            )
            * REQUEST_TIMEOUT_SECONDS
            * (CONTENT_RETRIES + 1)
        )
    )
    return {
        "per_episode": per_episode,
        "per_run": {
            "targets": len(TARGETS),
            "max_logical_requests": per_episode["max_logical_requests"] * len(TARGETS),
            "conservative_timeout_ceiling_seconds": per_episode["conservative_timeout_ceiling_seconds"] * len(TARGETS),
        },
    }


def _extract_episode_entry(container, reference_date):
    if not isinstance(container, dict):
        return None
    for key in ("episodes", "targets", "entries", "results"):
        rows = container.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("reference_date") == reference_date:
                    return row
    return None


def load_prior_evidence(reference_date: str) -> dict:
    files = {
        "batch2_diag": BASE_DIR / f"batch2_fresh_identity_discovery_{reference_date}_diag.json",
        "batch2_summary": BASE_DIR / "batch2_fresh_identity_discovery_summary.json",
        "ranked_report": BASE_DIR / "indicator_identity_audio_unresolved_ranked_report.json",
        "consolidated_ledger": BASE_DIR / "indicator_unresolved_consolidated_evidence_ledger.json",
        "wayback_npr_probe": BASE_DIR / "indicator_wayback_npr_probe.json",
        "wayback_player_probe": BASE_DIR / "indicator_wayback_player_probe.json",
    }
    loaded = {name: _read_json(path) for name, path in files.items()}
    batch2_summary = loaded.get("batch2_summary")
    cdx_self_test = batch2_summary.get("cdx_self_test") if isinstance(batch2_summary, dict) else None
    global_cdx_self_test_passed = bool(cdx_self_test.get("passed")) if isinstance(cdx_self_test, dict) else False
    return {
        "sources": {name: str(path.name) for name, path in files.items()},
        "batch2_diag": loaded["batch2_diag"],
        "batch2_summary_episode": _extract_episode_entry(loaded["batch2_summary"], reference_date),
        "global_cdx_self_test_passed": global_cdx_self_test_passed,
        "ranked_report_episode": _extract_episode_entry(loaded["ranked_report"], reference_date),
        "consolidated_ledger_episode": _extract_episode_entry(loaded["consolidated_ledger"], reference_date),
        # These probe files are loaded and tracked as required evidence sources.
        "wayback_npr_probe_loaded": loaded["wayback_npr_probe"] is not None,
        "wayback_player_probe_loaded": loaded["wayback_player_probe"] is not None,
    }


def _archive_url_variants(timestamp: str, original_url: str):
    return [
        {"variant": "id_", "archive_url": f"https://web.archive.org/web/{timestamp}id_/{original_url}"},
        {"variant": "raw", "archive_url": f"https://web.archive.org/web/{timestamp}/{original_url}"},
    ]


def _capture_seed_from_prior_diag(prior_diag: dict) -> list:
    seeds = []
    if not isinstance(prior_diag, dict):
        return seeds

    def add(ts, url, source):
        if ts and url:
            seeds.append({"timestamp": ts, "url": url, "source": source})

    for cap in prior_diag.get("date_window_captures", []):
        add(cap.get("timestamp"), cap.get("original_url"), "prior_stage_c")
    for cap in prior_diag.get("identity_candidates", []):
        add(cap.get("timestamp"), cap.get("original_url"), "prior_identity_candidate")

    for q in prior_diag.get("cdx_queries", []):
        for cand in q.get("scored_candidates", [])[:8]:
            add(cand.get("timestamp"), cand.get("url"), "prior_cdx_scored")

    uniq = []
    seen = set()
    for item in seeds:
        key = (item["timestamp"], item["url"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def _with_backoff_fetch(url: str):
    return base.fetch_text(url, retries=CONTENT_RETRIES)


def _seeded_urls_for_exact_cdx(seeds: list[dict]) -> list[str]:
    by_url = {}
    for item in seeds:
        url = item.get("url")
        if not url:
            continue
        ts = item.get("timestamp")
        source_priority = SEED_SOURCE_PRIORITY.get(item.get("source"), 99)
        stats = by_url.setdefault(url, {
            "best_source_priority": source_priority,
            "seed_count": 0,
            "earliest_timestamp": ts or "99999999999999",
        })
        stats["best_source_priority"] = min(stats["best_source_priority"], source_priority)
        stats["seed_count"] += 1
        if ts and ts < stats["earliest_timestamp"]:
            stats["earliest_timestamp"] = ts
    ranked = sorted(
        by_url.items(),
        key=lambda x: (
            x[1]["best_source_priority"],
            -x[1]["seed_count"],
            x[1]["earliest_timestamp"],
            x[0],
        ),
    )
    return [url for url, _ in ranked[:MAX_SEEDED_EXACT_CDX_URLS]]


def _load_affiliate_exact_title_evidence(target: dict) -> dict:
    files = [
        "indicator_unresolved_affiliate_recovery_00_09.json",
        "indicator_unresolved_affiliate_recovery_10_19.json",
        "indicator_unresolved_affiliate_recovery_20_49.json",
    ]
    matched_sources = []
    exact_title_urls = []

    for filename in files:
        payload = _read_json(BASE_DIR / filename)
        if not isinstance(payload, dict):
            continue
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            if item.get("date") != target["reference_date"]:
                continue
            if (item.get("title") or "").strip() != target["reference_title"]:
                continue
            matched_sources.append(filename)
            for key in ("npr_story_urls", "npr_player_embeds"):
                for url in item.get(key, []):
                    if isinstance(url, str) and url.startswith("http"):
                        exact_title_urls.append(url)
            for page in item.get("qualified_pages", []):
                if not isinstance(page, dict):
                    continue
                for key in ("requested_url", "final_url", "canonical"):
                    url = page.get(key)
                    if isinstance(url, str) and url.startswith("http"):
                        exact_title_urls.append(url)

    return {
        "files_scanned": files,
        "matched_sources": matched_sources,
        "matched_source_count": len(matched_sources),
        "exact_title_urls": list(dict.fromkeys(exact_title_urls)),
    }


def build_capture_retry_plan(target: dict, prior_diag: dict, supplemental_exact_cdx_urls: list[str] | None = None):
    seeds = _capture_seed_from_prior_diag(prior_diag)
    plan = []
    exact_cdx_queries = []
    selected_exact_cdx_urls = set(_seeded_urls_for_exact_cdx(seeds))
    if supplemental_exact_cdx_urls:
        for url in supplemental_exact_cdx_urls:
            if len(selected_exact_cdx_urls) >= MAX_SEEDED_EXACT_CDX_URLS:
                break
            if isinstance(url, str) and url.startswith("http"):
                selected_exact_cdx_urls.add(url)

    by_url = {}
    for item in seeds:
        by_url.setdefault(item["url"], []).append(item)

    for original_url, url_items in by_url.items():
        url_items.sort(key=lambda x: x["timestamp"])
        for item in url_items:
            for variant in _archive_url_variants(item["timestamp"], original_url):
                plan.append({
                    "timestamp": item["timestamp"],
                    "url": original_url,
                    "archive_url": variant["archive_url"],
                    "archive_variant": variant["variant"],
                    "source": item["source"],
                })

    for original_url in sorted(selected_exact_cdx_urls):
        cdx = base.wayback_cdx_url_exact(original_url, limit=8)
        exact_cdx_queries.append({
            "url": original_url,
            "query_url": cdx.get("query_url"),
            "rows_returned": len(cdx.get("rows", [])),
            "error_type": cdx.get("error_type"),
            "error_message": cdx.get("error_message"),
        })
        source = "exact_cdx_alternate_timestamp" if original_url in by_url else "affiliate_exact_title_seed"
        for row in cdx.get("rows", []):
            ts = row.get("timestamp")
            if not ts:
                continue
            for variant in _archive_url_variants(ts, original_url):
                plan.append({
                    "timestamp": ts,
                    "url": original_url,
                    "archive_url": variant["archive_url"],
                    "archive_variant": variant["variant"],
                    "source": source,
                })

    uniq = []
    seen = set()
    for item in plan:
        key = item["archive_url"]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)

    return uniq[:MAX_CAPTURE_ATTEMPTS], exact_cdx_queries


def _build_bounded_patterns(target: dict) -> list:
    ref = datetime.date.fromisoformat(target["reference_date"])
    patterns = []
    for offset in target.get("date_offsets", [0]):
        d = ref + datetime.timedelta(days=offset)
        y, m, day = d.strftime("%Y"), d.strftime("%m"), d.strftime("%d")
        for section in target.get("section_paths", []):
            patterns.append(f"https://www.npr.org/{section}/{y}/{m}/{day}/")
            patterns.append(f"http://www.npr.org/{section}/{y}/{m}/{day}/")
    uniq = []
    seen = set()
    for p in patterns:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq[:MAX_DISCOVERY_CDX_QUERIES]


def _is_blocked_adjacent_story(target: dict, story_id: str, page_title: str) -> bool:
    if story_id and story_id in target.get("blocked_story_ids", set()):
        return True
    title = (page_title or "").lower().replace("\u2019", "'").replace("'", "")
    for term in target.get("blocked_title_terms", []):
        term_normalized = (term or "").lower().replace("\u2019", "'").replace("'", "")
        if term_normalized and term_normalized in title:
            return True
    return False


def _normalized_title(value: str) -> str:
    if not value:
        return ""
    text = value.lower()
    text = text.replace("&", " and ")
    text = text.replace("\u2019", "'")
    text = text.replace(":", " ")
    text = text.replace("'", "")
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def _exact_title_match(page_title: str, reference_title: str) -> bool:
    normalized_reference = _normalized_title(reference_title)
    if not normalized_reference:
        return False
    candidates = [page_title]
    for separator in (" : ", " | ", " - "):
        if separator in page_title:
            candidates.append(page_title.split(separator, 1)[0])
    for candidate in candidates:
        if _normalized_title(candidate) == normalized_reference:
            return True
    return False


def parse_capture_result(target: dict, plan_item: dict, page_text: str) -> dict:
    page_title = base.extract_page_title(page_text)
    pub_date = base.extract_publication_date(page_text)
    canonical = base.extract_canonical_url(page_text)
    program_ctx = base.extract_program_context(page_text)
    story_url = canonical or plan_item["url"]
    score = base.score_page_for_target(
        page_title,
        pub_date,
        story_url,
        program_ctx,
        target["reference_date"],
        target["reference_title"],
    )
    story_id = base._trusted_npr_story_id_from_url(story_url)
    blocked_adjacent = _is_blocked_adjacent_story(target, story_id, page_title)
    exact_title_ok = _exact_title_match(page_title, target["reference_title"])

    strict_identity = (
        score.get("verdict") == "strong_match"
        and score.get("date_match") is True
        and exact_title_ok
        and not blocked_adjacent
    )

    rejection_reasons = []
    if blocked_adjacent:
        rejection_reasons.append("adjacent_unrelated_story")
    if score.get("verdict") != "strong_match":
        rejection_reasons.append(f"score_verdict:{score.get('verdict')}")
    if score.get("date_match") is not True:
        rejection_reasons.append("date_not_exact")
    if not exact_title_ok:
        rejection_reasons.append("title_not_exact")

    story_evidence = base._make_story_evidence(story_url, target, plan_item["timestamp"], strict_identity)
    players = [
        base._make_player_evidence(url, target, story_url, plan_item["timestamp"], strict_identity)
        for url in base.extract_player_embeds(page_text)
    ]
    audios = [
        base._make_audio_evidence(url, target, story_url, plan_item["timestamp"], strict_identity)
        for url in base.extract_audio_urls(page_text)
    ]

    return {
        "timestamp": plan_item["timestamp"],
        "original_url": plan_item["url"],
        "archive_url": plan_item["archive_url"],
        "archive_variant": plan_item["archive_variant"],
        "source": plan_item["source"],
        "status": "fetched",
        "page_title": page_title,
        "pub_date": pub_date,
        "canonical_url": canonical,
        "match_score": score,
        "story_id": story_id,
        "blocked_adjacent_unrelated": blocked_adjacent,
        "exact_title_match": exact_title_ok,
        "episode_qualified": strict_identity,
        "rejection_reasons": rejection_reasons,
        "story_evidence": story_evidence,
        "player_embeds": players,
        "audio_candidates": audios,
        "program_context": program_ctx,
    }


def classify_result(diag: dict) -> tuple[str, str, list]:
    """Return final classification, summary text, and next-step avenues.

    Precedence is intentional: recovered > identity-only unresolved >
    unresolved failure/incomplete states > lower-priority unresolved.
    lower_priority_unresolved is reserved for conclusive bounded-search misses
    only (self-test passed, required stages ran, no unresolved retrieval errors).
    """
    if diag.get("confirmed_identity") and diag.get("validated_audio"):
        return (
            "recovered",
            f"Recovered: trusted identity and {len(diag['validated_audio'])} validated NPR Indicator audio file(s).",
            [],
        )
    if diag.get("confirmed_identity"):
        return (
            "identity_found_audio_unresolved",
            "Trusted identity recovered but no validated provenance-linked NPR Indicator audio.",
            ["Expand player/audio capture retrieval around confirmed story/page IDs."],
        )
    if diag.get("global_cdx_self_test_passed") is not True:
        return (
            "network_failure_identity_unresolved",
            "Global CDX self-test did not pass; investigation is inconclusive and cannot be demoted.",
            ["Re-run after CDX self-test succeeds."],
        )
    had_query_failure = any(q.get("error_type") for q in diag.get("exact_cdx_queries", [])) or any(
        q.get("error_type") for q in diag.get("discovery_cdx_queries", [])
    )
    if diag.get("archive_captures_failed", 0) > 0 or had_query_failure:
        return (
            "archive_fetch_failed_identity_unresolved",
            (
                "Archive/network retrieval failures occurred during bounded search; "
                "identity remains unresolved and cannot be demoted."
            ),
            ["Retry bounded retrieval when archive/network conditions improve."],
        )
    if not diag.get("bounded_search_completed"):
        return (
            "incomplete_bounded_search_identity_unresolved",
            "Bounded search did not complete required discovery/capture stages; unresolved status must remain non-demoted.",
            ["Complete bounded capture/discovery stages and re-run."],
        )
    return (
        "lower_priority_unresolved",
        (
            "No trusted identity with exact title + exact date + trusted story ID was found; "
            "classified lower-priority unresolved with no further automatic retries."
        ),
        [],
    )


def _should_run_broad_discovery(target: dict, diag: dict) -> bool:
    if target.get("broad_discovery_if_all_known_unreadable"):
        return diag["captures_successfully_parsed"] == 0
    if target.get("broad_discovery_if_no_identity"):
        return diag.get("confirmed_identity") is None
    return False


def _collect_discovery_candidates(target: dict, diag: dict):
    from_date, to_date = base._cdx_date_window(target["reference_date"], days_before=3, days_after=4)
    candidates = {}
    for pattern in _build_bounded_patterns(target):
        cdx = base.wayback_cdx_date_window(pattern, from_date, to_date, limit=base.CDX_DATE_WINDOW_LIMIT)
        diag["discovery_cdx_queries"].append({
            "pattern": pattern,
            "from": from_date,
            "to": to_date,
            "query_url": cdx.get("query_url"),
            "rows_returned": len(cdx.get("rows", [])),
            "error_type": cdx.get("error_type"),
            "error_message": cdx.get("error_message"),
        })
        for row in cdx.get("rows", []):
            url = row.get("original")
            ts = row.get("timestamp")
            if not url or not ts:
                continue
            score = base.score_url_for_target(url, target["reference_title"], target["slug_variants"])
            existing = candidates.get(url)
            entry = {"url": url, "timestamp": ts, "score": round(score, 3)}
            if existing is None or entry["score"] > existing["score"]:
                candidates[url] = entry

    ranked = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    plan = []
    for item in ranked[:5]:
        for variant in _archive_url_variants(item["timestamp"], item["url"]):
            plan.append({
                "timestamp": item["timestamp"],
                "url": item["url"],
                "archive_url": variant["archive_url"],
                "archive_variant": variant["variant"],
                "source": "bounded_discovery",
                "discovery_score": item["score"],
            })
    uniq = []
    seen = set()
    for item in plan:
        if item["archive_url"] in seen:
            continue
        seen.add(item["archive_url"])
        uniq.append(item)
    return uniq


def _run_player_audio_chain(target: dict, diag: dict, identity_capture: dict):
    trusted_audio = []
    trusted_players = []
    for audio in identity_capture.get("audio_candidates", []):
        trusted_audio.append(base._clone_with_trust(audio, "trusted", True))
    for player in identity_capture.get("player_embeds", []):
        trusted_players.append(base._clone_with_trust(player, "trusted", True))

    for player_evidence in trusted_players[:MAX_PLAYER_FETCHES]:
        player_url = player_evidence.get("player_url")
        probe_item = {"url": player_url, "source": "trusted_live_player", "status": None}
        try:
            resp = _with_backoff_fetch(player_url)
            probe_item["status"] = "fetched"
            probe_item["audio_candidates"] = []
            for url in base.extract_audio_urls(resp["text"]):
                evidence = base._make_audio_evidence(url, target, player_url, None, True)
                evidence["provenance"]["evidence_type"] = "audio_url_from_live_player"
                trusted_audio.append(evidence)
                probe_item["audio_candidates"].append(evidence)
        except Exception as exc:
            probe_item["status"] = "error"
            probe_item["error"] = str(exc)
        diag["player_probes"].append(probe_item)

    archived_fetches = 0
    archived_player_cdx_queries = 0
    for player_evidence in trusted_players:
        if archived_player_cdx_queries >= MAX_ARCHIVED_PLAYER_CDX_QUERIES:
            break
        if archived_fetches >= MAX_ARCHIVED_PLAYER_FETCHES:
            break
        player_url = player_evidence.get("player_url")
        cdx = base.wayback_cdx_url_exact(player_url, limit=4)
        archived_player_cdx_queries += 1
        for row in cdx.get("rows", []):
            if archived_fetches >= MAX_ARCHIVED_PLAYER_FETCHES:
                break
            ts = row.get("timestamp")
            orig = row.get("original") or player_url
            if not ts:
                continue
            arch = f"https://web.archive.org/web/{ts}id_/{orig}"
            probe_item = {
                "url": player_url,
                "archive_url": arch,
                "timestamp": ts,
                "source": "trusted_archived_player",
                "status": None,
                "audio_candidates": [],
            }
            try:
                resp = _with_backoff_fetch(arch)
                probe_item["status"] = "fetched"
                for url in base.extract_audio_urls(resp["text"]):
                    evidence = base._make_audio_evidence(url, target, player_url, ts, True)
                    evidence["provenance"]["evidence_type"] = "audio_url_from_archived_player"
                    trusted_audio.append(evidence)
                    probe_item["audio_candidates"].append(evidence)
            except Exception as exc:
                probe_item["status"] = "error"
                probe_item["error"] = str(exc)
            diag["player_probes"].append(probe_item)
            archived_fetches += 1

    deduped = []
    seen = set()
    for audio in trusted_audio:
        url = audio.get("audio_url")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(audio)

    tested = []
    validated = []
    for audio in deduped[:MAX_AUDIO_VALIDATIONS]:
        check = base.validate_audio_evidence_live(audio, target["reference_date"])
        tested.append(check)
        if check.get("trusted_for_recovery"):
            validated.append(check)

    diag["audio_candidates_tested"] = tested
    diag["validated_audio"] = validated
    diag["audio_candidates_total"] = len(deduped)


def investigate_target(target: dict) -> dict:
    prior = load_prior_evidence(target["reference_date"])
    prior_diag = prior.get("batch2_diag") or {}
    affiliate_evidence = _load_affiliate_exact_title_evidence(target)
    plan, exact_cdx_queries = build_capture_retry_plan(
        target,
        prior_diag,
        supplemental_exact_cdx_urls=affiliate_evidence.get("exact_title_urls", []),
    )

    diag = {
        "method": "batch2-final3-targeted-recovery",
        "placeholder": False,
        "run_complete": False,
        "run_state": "running",
        "run_id": _run_id(),
        "generated_at": _now_iso(),
        "reference_date": target["reference_date"],
        "reference_title": target["reference_title"],
        "reference_episode": target["reference_episode"],
        "request_budget": request_budget()["per_episode"],
        "prior_evidence": prior,
        "global_cdx_self_test_passed": prior.get("global_cdx_self_test_passed") is True,
        "exact_title_affiliate_archive_evidence": affiliate_evidence,
        "exact_cdx_queries": exact_cdx_queries,
        "discovery_cdx_queries": [],
        "archive_captures_tried": [],
        "captures_successfully_parsed": 0,
        "archive_captures_failed": 0,
        "identity_candidates": [],
        "additional_qualified_captures": [],
        "confirmed_identity": None,
        "player_probes": [],
        "audio_candidates_tested": [],
        "validated_audio": [],
        "audio_candidates_total": 0,
        "bounded_discovery_required": False,
        "bounded_discovery_ran": False,
        "bounded_search_completed": False,
    }

    best_identity = None

    def run_capture_attempts(attempt_plan):
        nonlocal best_identity
        for item in attempt_plan:
            if len(diag["archive_captures_tried"]) >= MAX_CAPTURE_ATTEMPTS:
                break
            capture_result = {
                "timestamp": item["timestamp"],
                "original_url": item["url"],
                "archive_url": item["archive_url"],
                "archive_variant": item.get("archive_variant"),
                "source": item.get("source"),
                "status": None,
            }
            try:
                resp = _with_backoff_fetch(item["archive_url"])
                parsed = parse_capture_result(target, item, resp["text"])
                capture_result.update(parsed)
                diag["captures_successfully_parsed"] += 1
                if parsed.get("episode_qualified") and best_identity is None:
                    best_identity = parsed
                elif parsed.get("episode_qualified"):
                    diag["additional_qualified_captures"].append(parsed)
                elif (
                    not parsed.get("blocked_adjacent_unrelated")
                    and parsed.get("match_score", {}).get("verdict") != "no_match"
                ):
                    diag["identity_candidates"].append(parsed)
            except Exception as exc:
                capture_result["status"] = "error"
                capture_result["error"] = str(exc)
                diag["archive_captures_failed"] += 1
            diag["archive_captures_tried"].append(capture_result)

    run_capture_attempts(plan)

    if _should_run_broad_discovery(target, diag):
        diag["bounded_discovery_required"] = True
        diag["bounded_discovery_ran"] = True
        discovery_plan = _collect_discovery_candidates(target, diag)
        run_capture_attempts(discovery_plan)

    capture_stage_ran = len(diag["archive_captures_tried"]) > 0
    required_stages_ran = capture_stage_ran and (
        not diag["bounded_discovery_required"] or diag["bounded_discovery_ran"]
    )
    diag["bounded_search_completed"] = required_stages_ran

    if best_identity is not None:
        diag["confirmed_identity"] = {
            "trusted": True,
            "episode_qualified": True,
            "archive_url": best_identity.get("archive_url"),
            "original_url": best_identity.get("original_url"),
            "story_id": best_identity.get("story_id"),
            "story_evidence": best_identity.get("story_evidence"),
            "page_title": best_identity.get("page_title"),
            "pub_date": best_identity.get("pub_date"),
            "canonical_url": best_identity.get("canonical_url"),
            "match_score": best_identity.get("match_score"),
            "player_embeds": best_identity.get("player_embeds", []),
        }
        _run_player_audio_chain(target, diag, best_identity)

    final_classification, validation_summary, avenues = classify_result(diag)
    diag["final_classification"] = final_classification
    diag["validation_summary"] = validation_summary
    diag["remaining_recovery_avenues"] = avenues

    diag["story_id"] = (diag.get("confirmed_identity") or {}).get("story_id")
    player_ids = []
    for pe in (diag.get("confirmed_identity") or {}).get("player_embeds", []):
        player_ids.append({
            "player_story_id": pe.get("player_story_id"),
            "player_audio_id": pe.get("player_audio_id"),
            "player_url": pe.get("player_url"),
        })
    diag["player_ids"] = player_ids
    diag["run_complete"] = True
    diag["run_state"] = "run_complete"

    return diag


def run(write_placeholders_only: bool = False):
    write_placeholders()
    if write_placeholders_only:
        return _placeholder_summary()

    summary = {
        "placeholder": False,
        "run_complete": True,
        "run_state": "run_complete",
        "run_id": _run_id(),
        "method": "batch2-final3-targeted-recovery",
        "generated_at": _now_iso(),
        "request_budget": request_budget(),
        "episodes": [],
        "counts": {"attempted": 0, "completed": 0, "failed": 0, "recovered": 0},
    }

    episode_diags = []
    for target in TARGETS:
        summary["counts"]["attempted"] += 1
        try:
            diag = investigate_target(target)
        except Exception as exc:
            diag = {
                "placeholder": False,
                "run_complete": False,
                "run_state": "failed",
                "run_id": _run_id(),
                "generated_at": _now_iso(),
                "reference_date": target["reference_date"],
                "reference_title": target["reference_title"],
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

        episode_diags.append((target["reference_date"], diag))
        summary["episodes"].append({
            "reference_date": target["reference_date"],
            "reference_title": target["reference_title"],
            "final_classification": diag.get("final_classification"),
            "story_id": diag.get("story_id"),
            "player_ids": diag.get("player_ids", []),
            "archive_captures_tried": len(diag.get("archive_captures_tried", [])),
            "captures_successfully_parsed": diag.get("captures_successfully_parsed", 0),
            "audio_candidates_tested": len(diag.get("audio_candidates_tested", [])),
            "validated_audio": len(diag.get("validated_audio", [])),
            "validation_summary": diag.get("validation_summary"),
            "remaining_recovery_avenues": diag.get("remaining_recovery_avenues", []),
        })

    if summary["counts"]["failed"]:
        summary["run_complete"] = False
        summary["run_state"] = "failed"

    for ref_date, diag in episode_diags:
        _write_json(BASE_DIR / output_filename(ref_date), diag)
    _write_json(BASE_DIR / SUMMARY_OUTPUT, summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-placeholders-only",
        action="store_true",
        help="Overwrite all targeted diagnostic outputs with placeholder sentinels and exit.",
    )
    args = parser.parse_args()
    run(write_placeholders_only=args.write_placeholders_only)
