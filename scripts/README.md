# Scripts layout

- `scripts/maintenance/`: reusable feed-maintenance utilities for the completed archive project.
- `scripts/recovery/`: historical recovery and identity/audio resolution helpers used during backfill work.
- `scripts/analysis/`: analytical, inspection, reporting, and validation helpers for auditing recovery outputs.

Root-level production entry points remain intentionally unchanged because existing GitHub Actions workflows invoke them directly:

- `theindicator_rss.py`
- `theindicator_history.py`
- `recover_enclosures_bulk.py`

Unit tests for the relocated scripts live in `tests/`.

## Data file paths

All scripts reference data files via root-anchored constants using `REPO_ROOT`:

```python
REPO_ROOT = Path(__file__).resolve().parents[N]   # anchors to repo root
SOME_FILE  = REPO_ROOT / "data" / "recovery" / "filename.json"  # active recovery data
AUDIT_FILE = REPO_ROOT / "data" / "audits"   / "filename.json"  # audit datasets
ARCH_FILE  = REPO_ROOT / "archive" / "recovery" / "filename.json"  # historical evidence
```

See `data/README.md` for the full list of active data files and their consumers.
See `archive/recovery/README.md` for preserved historical evidence files.
