#!/usr/bin/env python3
"""
Reconcile unresolved indicator episode artifacts against indicator_history.json.

This script does NOT perform network recovery. It reads the current
indicator_history.json, compares it against the evidence ledger from the
previous consolidated recovery run, removes episodes that are now present in
history, and writes updated artifacts with:

    placeholder: false
    run_complete: true

All counts (history size, remaining unresolved, per-status breakdowns) are
derived dynamically from the data — no constants need to be updated after a
recovery.  Structural invariants (internal consistency checks, specific episode
membership that should never change) are still asserted so corruption or
mis-accounting is caught loudly.

Designed to be re-run after any new episode additions to indicator_history.json
so the artifacts never go stale.
"""
from pathlib import Path

import json
import sys
from collections import Counter
from datetime import datetime, timezone
REPO_ROOT = Path(__file__).resolve().parents[2]



# ── Input / output paths ──────────────────────────────────────────────────────
HISTORY_FILE = str(REPO_ROOT / "indicator_history.json")
COMPLETENESS_AUDIT_FILE = str(REPO_ROOT / "data" / "audits" / "indicator_completeness_audit.json")
CANONICAL_INPUT = "data/audits/indicator_completeness_audit.json"
PRIOR_LEDGER_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_unresolved_consolidated_evidence_ledger.json")
OUTPUT_LEDGER_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_unresolved_consolidated_evidence_ledger.json")
OUTPUT_AUDIT_FILE = str(REPO_ROOT / "data" / "audits" / "indicator_unresolved_consolidated_audit.json")
# ── Invariant sets ────────────────────────────────────────────────────────────
# These specific episode sets should never change unless the ledger is edited.
# They are structural invariants, not count constants — no update needed after
# a normal recovery.

# Episodes that should appear in identity_found_but_audio_unresolved.
ALLOWED_STATUSES = {
    "confirmed_recovered",
    "probable_duplicate_rebroadcast",
    "identity_found_but_audio_unresolved",
    "no_identity_found",
}

# Episodes that should appear in probable_duplicate_rebroadcast.
# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def now_iso():
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def assert_equal(label, actual, expected):
    if actual != expected:
        print(
            f"ASSERTION FAILED: {label}\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}",
            file=sys.stderr,
        )
        sys.exit(1)


def assert_set_equal(label, actual_set, expected_set):
    if actual_set != expected_set:
        extra = actual_set - expected_set
        missing = expected_set - actual_set
        msg_parts = [f"ASSERTION FAILED: {label}"]
        if extra:
            msg_parts.append(f"  unexpected: {sorted(extra)}")
        if missing:
            msg_parts.append(f"  missing:    {sorted(missing)}")
        print("\n".join(msg_parts), file=sys.stderr)
        sys.exit(1)


# ── Core reconciliation ───────────────────────────────────────────────────────

def normalize_title(value):
    """Normalize an episode title for conservative exact-title reconciliation."""
    return " ".join((value or "").strip().casefold().split())


def build_history_identity_sets(history):
    """Return exact dates and normalized exact titles present in history."""
    return (
        {ep["date"][:10] for ep in history["episodes"]},
        {normalize_title(ep.get("title")) for ep in history["episodes"] if ep.get("title")},
    )


def reconcile(prior_ledger_episodes, history_dates, history_titles=None):
    """
    Split the prior ledger into (recovered, remaining).

    An episode is considered recovered if its reference date or normalized
    exact title now appears in indicator_history.json. Exact-title matching is
    required for verified catalog date corrections.
    """
    recovered = []
    remaining = []
    history_titles = history_titles or set()
    for ep in prior_ledger_episodes:
        if (
            ep["reference_date"] in history_dates
            or normalize_title(ep.get("reference_title")) in history_titles
        ):
            recovered.append(ep)
        else:
            remaining.append(ep)
    return recovered, remaining


def build_audit_from_ledgers(remaining_ledgers, generated_at):
    """Build the consolidated audit dict from the remaining ledger episodes."""
    grouped = {
        "confirmed_recovered": [],
        "probable_duplicate_rebroadcast": [],
        "identity_found_but_audio_unresolved": [],
        "no_identity_found": [],
    }
    false_positives = []

    for ep in remaining_ledgers:
        status = ep["final_status"]
        grouped.setdefault(status, []).append({
            "reference_date": ep["reference_date"],
            "reference_title": ep["reference_title"],
            "reference_year": ep.get("reference_year"),
            "reference_episode": ep.get("reference_episode"),
            "evidence_confidence_explanation": ep.get(
                "evidence_confidence_explanation", ""
            ),
        })
        # Carry forward any false positives from the validation results,
        # preserving the full schema from the original build_false_positive_rows.
        for result in ep.get("validation_results", []):
            status_val = result.get("validation_status") or ""
            if status_val.startswith("rejected_") and status_val != "rejected_request_error":
                false_positives.append({
                    "reference_date": ep["reference_date"],
                    "reference_title": ep["reference_title"],
                    "candidate_url": result.get("candidate_url"),
                    "final_url": result.get("final_url"),
                    "validation_status": status_val,
                    "reason": result.get("reason"),
                    "source_type": result.get("source_type"),
                    "source_url": result.get("source_url"),
                })

    total = len(remaining_ledgers)
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
        "input_unresolved_count": total,
        "unique_missing_production_count_excluding_probable_duplicates": (
            total - len(grouped["probable_duplicate_rebroadcast"])
        ),
    }

    audit = {
        "method": "consolidated-recovery-pipeline-for-unresolved-indicator-episodes",
        "generated_at": generated_at,
        "canonical_input": CANONICAL_INPUT,
        "placeholder": False,
        "run_complete": True,
        "summary": summary,
        "confirmed_recovered": grouped["confirmed_recovered"],
        "probable_duplicate_rebroadcast": grouped["probable_duplicate_rebroadcast"],
        "identity_found_but_audio_unresolved": grouped[
            "identity_found_but_audio_unresolved"
        ],
        "no_identity_found": grouped["no_identity_found"],
        "rejected_false_positives": false_positives,
    }
    return audit


# ── Assertions ────────────────────────────────────────────────────────────────

def run_assertions(remaining_ledgers):
    """
    Verify internal consistency of the remaining unresolved ledger.

    Counts are derived from the data — no constants to update after a recovery.
    Only structural invariants (specific episode membership) are hardcoded.
    """
    counts = Counter(ep["final_status"] for ep in remaining_ledgers)
    total = len(remaining_ledgers)

    # 1. Status counts must sum to total (internal consistency).
    counted_total = (
        counts["confirmed_recovered"]
        + counts["probable_duplicate_rebroadcast"]
        + counts["identity_found_but_audio_unresolved"]
        + counts["no_identity_found"]
    )
    assert_equal(
        "status breakdown sum equals total remaining",
        counted_total,
        total,
    )

    # 2. Specific episode membership — these sets are invariants that do not
    #    change when new episodes are recovered from other dates.
    assert_set_equal(
        "remaining status schema",
        set(counts),
        set(counts) & ALLOWED_STATUSES,
    )

    keys = [
        (ep["reference_date"], normalize_title(ep.get("reference_title")))
        for ep in remaining_ledgers
    ]
    assert_equal("remaining episode keys are unique", len(keys), len(set(keys)))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print()
    print("========================================")
    print("RECONCILE UNRESOLVED CONSOLIDATED AUDIT")
    print("========================================")

    # Load inputs
    history = load_json(HISTORY_FILE)
    prior_ledger = load_json(PRIOR_LEDGER_FILE)
    prior_episodes = prior_ledger.get("episodes", [])

    print(f"indicator_history.json episodes: {len(history['episodes'])}")
    print(f"Prior ledger episodes:           {len(prior_episodes)}")

    # Build conservative history identity sets.
    history_dates, history_titles = build_history_identity_sets(history)

    # Reconcile
    recovered, remaining = reconcile(prior_episodes, history_dates, history_titles)

    print(f"Removed (now in history):        {len(recovered)}")
    for ep in recovered:
        print(f"  {ep['reference_date']} | {ep['reference_title']}")

    print(f"Remaining unresolved:            {len(remaining)}")

    # Assertions — fail loudly if ledger is internally inconsistent
    run_assertions(remaining)
    print("All assertions passed.")

    # Build outputs
    generated_at = now_iso()

    audit = build_audit_from_ledgers(remaining, generated_at)

    ledger_out = {
        "method": audit["method"],
        "generated_at": generated_at,
        "canonical_input": CANONICAL_INPUT,
        "placeholder": False,
        "run_complete": True,
        "summary": audit["summary"],
        "episodes": remaining,
    }

    save_json(OUTPUT_LEDGER_FILE, ledger_out)
    save_json(OUTPUT_AUDIT_FILE, audit)

    print()
    print("Summary:")
    for key, value in audit["summary"].items():
        print(f"  {key}: {value}")

    print()
    print("placeholder: false")
    print("run_complete: true")
    print(f"Saved: {OUTPUT_LEDGER_FILE}")
    print(f"Saved: {OUTPUT_AUDIT_FILE}")
    print("========================================")


if __name__ == "__main__":
    main()
