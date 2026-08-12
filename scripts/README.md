# Scripts layout

- `scripts/maintenance/`: reusable feed-maintenance utilities for the completed archive project.
- `scripts/recovery/`: historical recovery and identity/audio resolution helpers used during backfill work.
- `scripts/analysis/`: analytical, inspection, reporting, and validation helpers for auditing recovery outputs.

Root-level production entry points remain intentionally unchanged because existing GitHub Actions workflows invoke them directly:

- `theindicator_rss.py`
- `theindicator_history.py`
- `recover_enclosures_bulk.py`

Unit tests for the relocated scripts live in `tests/`.
