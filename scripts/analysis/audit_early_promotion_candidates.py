#!/usr/bin/env python3
"""Build a non-destructive promotion manifest for the early Indicator catalog.

This script only reads existing recovery evidence.  It does not modify history,
the enclosure map, or the published RSS feed.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
EARLY_AUDIT = REPO_ROOT / "data" / "audits" / "indicator_early_audit.json"
COMPLETENESS_AUDIT = (
    REPO_ROOT / "data" / "audits" / "indicator_completeness_audit.json"
)
IDENTITIES = REPO_ROOT / "data" / "recovery" / "indicator_npr_identities.json"
AUDIO_VALIDATION = (
    REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_validation.json"
)
AUDIO_RECOVERY = (
    REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_recovery.json"
)
UNRESOLVED_LEDGER = (
    REPO_ROOT
    / "data"
    / "recovery"
    / "indicator_unresolved_consolidated_evidence_ledger.json"
)
HISTORY = REPO_ROOT / "indicator_history.json"
ENCLOSURE_MAP = REPO_ROOT / "indicator_enclosure_map.json"
FEED = REPO_ROOT / "theindicator_feed.xml"
OUTPUT = (
    REPO_ROOT / "data" / "audits" / "indicator_early_promotion_manifest.json"
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def episode_key(date: str | None, title: str | None) -> tuple[str, str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return ((date or "")[:10], normalized)


def story_id_from_feed_item(item: ET.Element) -> str | None:
    guid = (item.findtext("guid") or "").strip()
    if guid.isdigit():
        return guid

    link = item.findtext("link") or ""
    match = re.search(r"/(\d{7,})(?:/|$)", link)
    if match:
        return match.group(1)

    enclosure = item.find("enclosure")
    if enclosure is not None:
        query = parse_qs(urlparse(enclosure.get("url", "")).query)
        values = query.get("e", [])
        if values and values[0].isdigit():
            return values[0]
    return None


def feed_story_ids(path: Path) -> set[str]:
    return {
        story_id
        for item in ET.parse(path).getroot().findall(".//item")
        if (story_id := story_id_from_feed_item(item))
    }


def index_by_reference(records: list[dict]) -> dict[tuple[str, str], dict]:
    return {
        episode_key(record.get("reference_date"), record.get("reference_title")):
        record
        for record in records
    }


def query_parameter(url: str | None, name: str) -> str | None:
    values = parse_qs(urlparse(url or "").query).get(name, [])
    return values[0] if values else None


def classify_unresolved(record: dict | None) -> str:
    status = (record or {}).get("final_status")
    if status == "probable_duplicate_rebroadcast":
        return status
    if status == "identity_found_but_audio_unresolved":
        return status
    return "unresolved_candidate"


def build_manifest(repo_root: Path = REPO_ROOT) -> dict:
    early = load_json(repo_root / EARLY_AUDIT.relative_to(REPO_ROOT))
    completeness = load_json(
        repo_root / COMPLETENESS_AUDIT.relative_to(REPO_ROOT)
    )
    identities = load_json(repo_root / IDENTITIES.relative_to(REPO_ROOT))
    validation = load_json(repo_root / AUDIO_VALIDATION.relative_to(REPO_ROOT))
    audio_recovery = load_json(repo_root / AUDIO_RECOVERY.relative_to(REPO_ROOT))
    unresolved = load_json(repo_root / UNRESOLVED_LEDGER.relative_to(REPO_ROOT))
    history = load_json(repo_root / HISTORY.relative_to(REPO_ROOT))
    enclosure_map = load_json(repo_root / ENCLOSURE_MAP.relative_to(REPO_ROOT))

    references = (
        early.get("possible_missing", [])
        + early.get("matched", [])
        + early.get("reference_anomalies", [])
    )
    # The anomaly can repeat an entry already present in possible_missing.
    reference_by_key = {
        episode_key(record.get("date"), record.get("title")): record
        for record in references
    }

    identity_by_key = index_by_reference(identities.get("results", []))
    validated_by_key = index_by_reference(validation.get("validated_audio", []))
    audio_recovery_by_key = index_by_reference(audio_recovery.get("results", []))
    unresolved_by_key = index_by_reference(unresolved.get("episodes", []))
    special_by_key = {
        episode_key(record.get("date"), record.get("title")): record
        for record in completeness.get("special_recoveries", [])
    }

    history_by_key = {
        episode_key(record.get("date"), record.get("title")): record
        for record in history.get("episodes", [])
    }
    history_story_ids = {
        str(record["story_id"])
        for record in history.get("episodes", [])
        if record.get("story_id")
    }
    enclosure_records = enclosure_map.get("episodes", {})
    feed_ids = feed_story_ids(repo_root / FEED.relative_to(REPO_ROOT))

    entries = []
    for key, reference in sorted(reference_by_key.items()):
        identity = identity_by_key.get(key)
        validated = validated_by_key.get(key)
        recovered_audio = audio_recovery_by_key.get(key)
        special = special_by_key.get(key)
        unresolved_record = unresolved_by_key.get(key)
        history_record = history_by_key.get(key)

        story_id = str(
            (history_record or {}).get("story_id")
            or (identity or {}).get("npr_story_id")
            or (validated or {}).get("npr_story_id")
            or ""
        ) or None

        in_history = bool(history_record) or bool(
            story_id and story_id in history_story_ids
        )
        in_enclosure_map = bool(story_id and story_id in enclosure_records)
        in_feed = bool(story_id and story_id in feed_ids)

        if in_history or in_enclosure_map or in_feed:
            category = "already_in_production"
        elif special:
            category = "special_recovery_review"
        elif validated and validated.get("identity_status") == "strong_npr_identity":
            category = "strong_promotion_candidate"
        elif validated:
            category = "identity_review_candidate"
        else:
            category = classify_unresolved(unresolved_record)

        entries.append({
            "reference_date": reference.get("date"),
            "reference_title": reference.get("title"),
            "reference_year": reference.get("reference_year"),
            "reference_episode": reference.get("reference_episode"),
            "category": category,
            "npr_story_id": story_id,
            "npr_url": (
                (history_record or {}).get("npr_url")
                or (identity or {}).get("selected_npr_url")
                or (validated or {}).get("npr_url")
            ),
            "identity_status": (identity or validated or {}).get("status")
            or (validated or {}).get("identity_status"),
            "identity_score": (identity or {}).get("identity_score"),
            "audio_url": (validated or {}).get("audio_url")
            or (special or {}).get("audio_url"),
            "validated_final_audio_url": (validated or {}).get("final_url")
            or (special or {}).get("audio_url"),
            # These are retained as observations, not asserted as canonical IDs.
            "observed_player_ids": (recovered_audio or {}).get("player_ids", []),
            "audio_url_e_parameter": query_parameter(
                (validated or {}).get("final_url") or (special or {}).get("audio_url"),
                "e",
            ),
            "audio_validation_status": (validated or {}).get("validation_status"),
            "special_recovery_source": (special or {}).get("source"),
            "duplicate_reference_dates": (
                unresolved_record or {}
            ).get("duplicate_reference_dates", []),
            "unresolved_status": (unresolved_record or {}).get("final_status"),
            "unresolved_explanation": (
                unresolved_record or {}
            ).get("evidence_confidence_explanation"),
            "production_presence": {
                "indicator_history": in_history,
                "indicator_enclosure_map": in_enclosure_map,
                "theindicator_feed": in_feed,
            },
            "provenance": [
                name
                for name, present in (
                    ("indicator_early_audit.json", True),
                    ("indicator_npr_identities.json", bool(identity)),
                    ("indicator_npr_audio_validation.json", bool(validated)),
                    ("indicator_npr_audio_recovery.json", bool(recovered_audio)),
                    ("indicator_completeness_audit.json:special_recoveries", bool(special)),
                    ("indicator_unresolved_consolidated_evidence_ledger.json", bool(unresolved_record)),
                    ("indicator_history.json", in_history),
                    ("indicator_enclosure_map.json", in_enclosure_map),
                    ("theindicator_feed.xml", in_feed),
                )
                if present
            ],
        })

    counts = Counter(entry["category"] for entry in entries)
    return {
        "manifest_version": 1,
        "purpose": (
            "Read-only review manifest; no entry is approved for production "
            "merely by appearing here."
        ),
        "reference_period": early.get("audit_period"),
        "summary": {
            "reference_episode_count": len(entries),
            "category_counts": dict(sorted(counts.items())),
            "safe_automatic_mutations": 0,
        },
        "category_definitions": {
            "already_in_production": "Present in history, enclosure map, or RSS.",
            "strong_promotion_candidate": (
                "Validated NPR-hosted audio and a strong NPR identity; still requires "
                "duplicate/rebroadcast review before promotion."
            ),
            "identity_review_candidate": (
                "Validated NPR-hosted audio, but the NPR identity was marked for review."
            ),
            "special_recovery_review": (
                "NPR-hosted audio recovered through a special Wayback or affiliate path."
            ),
            "probable_duplicate_rebroadcast": (
                "Evidence indicates a likely rebroadcast of another reference release."
            ),
            "identity_found_but_audio_unresolved": (
                "Some NPR identity evidence exists, but no validated episode audio."
            ),
            "unresolved_candidate": "No production-ready identity/audio chain exists.",
        },
        "episodes": entries,
    }


def main() -> None:
    manifest = build_manifest()
    OUTPUT.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT}")
    print(json.dumps(manifest["summary"], indent=2))
    print("Production history, enclosure map, and RSS were not modified.")


if __name__ == "__main__":
    main()
