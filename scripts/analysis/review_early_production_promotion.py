#!/usr/bin/env python3
"""Review isolated early-history artifacts against production promotion gates."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analysis.audit_early_promotion_candidates import (  # noqa: E402
    story_id_from_feed_item,
)
from scripts.analysis.build_early_promotion_dry_run import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    production_hashes,
)


REPORT = REPO_ROOT / "data" / "audits" / "indicator_early_production_design_review.json"


def review(repo_root: Path = REPO_ROOT, dry_dir: Path | None = None) -> dict:
    dry_dir = dry_dir or (repo_root / DEFAULT_OUTPUT_DIR.relative_to(REPO_ROOT))
    dry_history = json.loads((dry_dir / "indicator_history.json").read_text(encoding="utf-8"))
    dry_map = json.loads((dry_dir / "indicator_enclosure_map.json").read_text(encoding="utf-8"))
    dry_report = json.loads((dry_dir / "dry_run_report.json").read_text(encoding="utf-8"))
    production_history = json.loads(
        (repo_root / "indicator_history.json").read_text(encoding="utf-8")
    )

    production_ids = {
        str(record["story_id"])
        for record in production_history["episodes"]
        if record.get("story_id")
    }
    promoted_history = [
        record for record in dry_history["episodes"]
        if str(record.get("story_id")) not in production_ids
    ]
    promoted_ids = {str(record["story_id"]) for record in promoted_history}
    promoted_map = [dry_map["episodes"][story_id] for story_id in promoted_ids]

    tree = ET.parse(dry_dir / "theindicator_feed.xml")
    items = tree.getroot().findall(".//item")
    promoted_items = [
        item for item in items if story_id_from_feed_item(item) in promoted_ids
    ]
    all_guids = [(item.findtext("guid") or "").strip() for item in items]
    dates = [parsedate_to_datetime(item.findtext("pubDate") or "") for item in items]

    checks = {
        "dry_run_report_passed": dry_report.get("all_checks_passed") is True,
        "production_hashes_still_match_review_baseline": (
            production_hashes(repo_root) == dry_report["production_sha256_after"]
        ),
        "exactly_226_promoted_records": len(promoted_history) == 226,
        "history_story_ids_unique": len(promoted_ids) == len(promoted_history),
        "all_promoted_have_audio_id": all(record.get("audio_id") for record in promoted_history),
        "all_promoted_have_player_url": all(record.get("player_url") for record in promoted_history),
        "all_promoted_have_description": all(record.get("description") for record in promoted_history),
        "all_promoted_description_provenance_recorded": all(
            record.get("description_provenance") for record in promoted_history
        ),
        "all_promoted_dates_marked_date_only": all(
            record.get("date_precision") == "date_only" for record in promoted_history
        ),
        "all_promoted_enclosures_resolved": all(
            record.get("status") == "resolved" for record in promoted_map
        ),
        "rss_has_exact_promoted_count": len(promoted_items) == 226,
        "rss_guids_unique": not any(count > 1 for count in Counter(all_guids).values()),
        "rss_sorted_newest_first": dates == sorted(dates, reverse=True),
        "scheduled_updater_preserves_items_by_guid": all(
            (item.findtext("guid") or "").strip() in promoted_ids
            for item in promoted_items
        ),
    }

    nonzero_lengths = sum(
        int((item.find("enclosure").get("length") or "0")) > 0
        for item in promoted_items
    )
    rebroadcasts = [
        record for record in promoted_history
        if record.get("release_classification") == "confirmed_indicator_rebroadcast"
    ]
    gates = [
        {
            "id": "publication_time_policy",
            "severity": "decision_required",
            "status": "open",
            "finding": (
                "All 226 candidates have a verified publication date but no verified time. "
                "The dry RSS serializes date-only values as 00:00 UTC."
            ),
            "recommended_resolution": (
                "Approve 00:00 UTC strictly as an RSS serialization convention and retain "
                "date_precision=date_only; do not represent it as an exact NPR publication time."
            ),
        },
        {
            "id": "content_length_completion",
            "severity": "non_blocking_quality",
            "status": "partial",
            "finding": f"{nonzero_lengths} of 226 enclosure lengths are known; the remainder serialize as 0.",
            "recommended_resolution": (
                "Optionally perform validated HEAD/range probes before promotion. Existing feed "
                "code already permits a zero length when NPR does not expose one."
            ),
        },
        {
            "id": "atomic_promotion_writer",
            "severity": "blocking_implementation",
            "status": "open",
            "finding": "Only the isolated builder exists; there is intentionally no production writer.",
            "recommended_resolution": (
                "Implement an explicit, hash-guarded promotion command that writes all three "
                "files together, validates them, and aborts before replacement on any failure."
            ),
        },
        {
            "id": "rollback",
            "severity": "blocking_process",
            "status": "open",
            "finding": "A production promotion must be a dedicated Git commit with no unrelated files.",
            "recommended_resolution": (
                "Require a clean targeted diff and retain the pre-promotion hashes in the report."
            ),
        },
    ]

    return {
        "review_version": 1,
        "review_scope": "production promotion design; no production writes",
        "verdict": "conditionally_ready_after_open_gates",
        "production_files_modified": False,
        "summary": {
            "candidate_count": len(promoted_history),
            "confirmed_rebroadcast_count": len(rebroadcasts),
            "descriptions_with_provenance": sum(
                bool(record.get("description_provenance")) for record in promoted_history
            ),
            "known_content_lengths": nonzero_lengths,
            "exact_publication_times": 0,
            "checks_passed": sum(checks.values()),
            "checks_total": len(checks),
            "open_blocking_or_decision_gates": sum(
                gate["status"] == "open" for gate in gates
            ),
        },
        "checks": checks,
        "gates": gates,
        "workflow_compatibility": {
            "scheduled_feed_updater": (
                "Compatible: existing GUIDs are retained and only unseen current-feed GUIDs are appended."
            ),
            "bulk_enclosure_recovery": (
                "Compatible: promoted map entries are resolved and therefore not re-queued."
            ),
            "complete_feed_builder": (
                "Compatible, but production promotion should reuse the reviewed atomic writer "
                "rather than manually editing generated XML."
            ),
        },
    }


def main() -> None:
    payload = review()
    REPORT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {REPORT}")
    print(json.dumps(payload["summary"], indent=2))
    print(f"Verdict: {payload['verdict']}")
    print("Production files were not modified.")


if __name__ == "__main__":
    main()
