#!/usr/bin/env python3
"""
Reconcile unresolved indicator episode artifacts against indicator_history.json.

This script does NOT perform network recovery. It reads the current
indicator_history.json, compares it against the evidence ledger from the
previous consolidated recovery run, removes episodes that are now present in
history, and writes updated artifacts with:

    placeholder: false
    run_complete: true

It asserts the expected counts and fails loudly if the repository state
disagrees with the assertion constants defined below. Update those constants
after each new batch of recoveries before re-running this script.

Designed to be re-run after any new episode additions to indicator_history.json
so the artifacts never go stale.
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone


# ── Input / output paths ──────────────────────────────────────────────────────
HISTORY_FILE = "indicator_history.json"
COMPLETENESS_AUDIT_FILE = "indicator_completeness_audit.json"
PRIOR_LEDGER_FILE = "indicator_unresolved_consolidated_evidence_ledger.json"
OUTPUT_LEDGER_FILE = "indicator_unresolved_consolidated_evidence_ledger.json"
OUTPUT_AUDIT_FILE = "indicator_unresolved_consolidated_audit.json"

# ── Assertion constants ───────────────────────────────────────────────────────
# Update these after each new batch of recoveries.
EXPECTED_HISTORY_COUNT = 1480
EXPECTED_REMAINING_UNRESOLVED = 42
EXPECTED_PROBABLE_DUPLICATE = 2
EXPECTED_IDENTITY_FOUND_AUDIO_UNRESOLVED = 2
EXPECTED_NO_IDENTITY_FOUND = 38

# Episodes that should appear in identity_found_but_audio_unresolved.
EXPECTED_IDENTITY_FOUND_DATES = {
    "2018-04-24",  # When China's Ships Come In
    "2018-10-11",  # China's Brave New World
}

# Episodes that should appear in probable_duplicate_rebroadcast.
EXPECTED_PROBABLE_DUPLICATE_DATES = {
    "2018-03-12",  # Hurricane Joseph & The Calculator That Time Forgot
    "2018-08-28",  # Hurricane Joseph & The Calculator That Time Forgot (rebroadcast)
}


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

def build_history_date_set(history):
    """Return a set of YYYY-MM-DD date strings present in indicator_history."""
    return {ep["date"][:10] for ep in history["episodes"]}


def reconcile(prior_ledger_episodes, history_dates):
    """
    Split the prior ledger into (recovered, remaining).

    An episode is considered recovered if its reference_date now appears in
    indicator_history.json.
    """
    recovered = []
    remaining = []
    for ep in prior_ledger_episodes:
        if ep["reference_date"] in history_dates:
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
        # Carry forward any false positives from the validation results.
        for result in ep.get("validation_results", []):
            status_val = result.get("validation_status") or ""
            if status_val.startswith("rejected_") and status_val != "rejected_request_error":
                false_positives.append({
                    "reference_date": ep["reference_date"],
                    "reference_title": ep["reference_title"],
                    "candidate_url": result.get("candidate_url"),
                    "validation_status": status_val,
                    "reason": result.get("reason"),
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
        "canonical_input": COMPLETENESS_AUDIT_FILE,
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

def run_assertions(history, remaining_ledgers):
    # 1. History count
    assert_equal(
        "indicator_history.json episode count",
        len(history["episodes"]),
        EXPECTED_HISTORY_COUNT,
    )

    # 2. Total remaining
    assert_equal(
        "remaining unresolved episode count",
        len(remaining_ledgers),
        EXPECTED_REMAINING_UNRESOLVED,
    )

    # 3. Status breakdown
    counts = Counter(ep["final_status"] for ep in remaining_ledgers)
    assert_equal(
        "probable_duplicate_rebroadcast count",
        counts["probable_duplicate_rebroadcast"],
        EXPECTED_PROBABLE_DUPLICATE,
    )
    assert_equal(
        "identity_found_but_audio_unresolved count",
        counts["identity_found_but_audio_unresolved"],
        EXPECTED_IDENTITY_FOUND_AUDIO_UNRESOLVED,
    )
    assert_equal(
        "no_identity_found count",
        counts["no_identity_found"],
        EXPECTED_NO_IDENTITY_FOUND,
    )

    # 4. Specific episode membership
    identity_dates = {
        ep["reference_date"]
        for ep in remaining_ledgers
        if ep["final_status"] == "identity_found_but_audio_unresolved"
    }
    assert_set_equal(
        "identity_found_but_audio_unresolved dates",
        identity_dates,
        EXPECTED_IDENTITY_FOUND_DATES,
    )

    dupe_dates = {
        ep["reference_date"]
        for ep in remaining_ledgers
        if ep["final_status"] == "probable_duplicate_rebroadcast"
    }
    assert_set_equal(
        "probable_duplicate_rebroadcast dates",
        dupe_dates,
        EXPECTED_PROBABLE_DUPLICATE_DATES,
    )


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

    # Build history date set
    history_dates = build_history_date_set(history)

    # Reconcile
    recovered, remaining = reconcile(prior_episodes, history_dates)

    print(f"Removed (now in history):        {len(recovered)}")
    for ep in recovered:
        print(f"  {ep['reference_date']} | {ep['reference_title']}")

    print(f"Remaining unresolved:            {len(remaining)}")

    # Assertions — fail loudly if repository state doesn't match expectations
    run_assertions(history, remaining)
    print("All assertions passed.")

    # Build outputs
    generated_at = now_iso()

    audit = build_audit_from_ledgers(remaining, generated_at)

    ledger_out = {
        "method": audit["method"],
        "generated_at": generated_at,
        "canonical_input": COMPLETENESS_AUDIT_FILE,
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
