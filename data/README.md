# data/

Active recovery and audit datasets used by maintained scripts in `scripts/`.

## data/recovery/

Active recovery artefacts — files that recovery, analysis, and maintenance scripts
still read or write as part of ongoing work. Reusable intermediate recovery inputs
belong here, even when they were first produced by a historical probe campaign.

| File | Description | Primary consumers |
|------|-------------|-------------------|
| `indicator_audio_inspection.json` | Recovered-episode audio inspection results | `scripts/analysis/inspect_recovered_audio.py` |
| `batch2_fresh_identity_discovery_summary.json` | Summary of reusable batch-2 fresh identity discovery evidence | `scripts/recovery/probe_batch2_fresh_identity_discovery.py`, `scripts/recovery/probe_batch2_final3_identity_recovery.py` |
| `batch2_fresh_identity_discovery_2018-04-24_diag.json` | Batch-2 fresh identity diagnostic reused by final-round recovery for 2018-04-24 | `scripts/recovery/probe_batch2_fresh_identity_discovery.py`, `scripts/recovery/probe_batch2_final3_identity_recovery.py` |
| `batch2_fresh_identity_discovery_2018-10-09_diag.json` | Batch-2 fresh identity diagnostic for 2018-10-09 | `scripts/recovery/probe_batch2_fresh_identity_discovery.py` |
| `batch2_fresh_identity_discovery_2018-10-11_diag.json` | Batch-2 fresh identity diagnostic reused by final-round recovery for 2018-10-11 | `scripts/recovery/probe_batch2_fresh_identity_discovery.py`, `scripts/recovery/probe_batch2_final3_identity_recovery.py` |
| `indicator_identity_audio_unresolved_ranked_report.json` | Ranked report of unresolved identity–audio episodes | `scripts/analysis/report_identity_audio_unresolved.py`, `scripts/recovery/probe_top3_ranked_prospects.py`, tests |
| `indicator_multi_archive_player_probe.json` | Multi-archive NPR player probe results | `scripts/recovery/probe_multi_archive_npr_player.py`, `scripts/maintenance/audit_indicator_completeness.py` |
| `indicator_npr_audio_mapping.json` | NPR audio URL-to-episode mapping | `scripts/analysis/analyze_npr_audio_mapping.py` |
| `indicator_npr_audio_recovery.json` | NPR audio recovery candidates | `scripts/recovery/recover_npr_audio.py`, `scripts/recovery/validate_npr_audio.py` |
| `indicator_npr_audio_review.json` | NPR audio review results | `scripts/analysis/inspect_npr_audio_review.py`, `scripts/recovery/resolve_npr_player_audio.py` |
| `indicator_npr_audio_validation.json` | Validated NPR audio entries | many analysis/recovery scripts |
| `indicator_npr_feed_probe.json` | NPR feed archive probe results | `scripts/recovery/probe_npr_feed_archive.py` |
| `indicator_npr_filename_neighbors.json` | Neighboring NPR filename inspection | `scripts/analysis/inspect_neighboring_npr_filenames.py` |
| `indicator_npr_filename_resolution.json` | NPR filename-to-audio resolution | `scripts/recovery/resolve_npr_filename_audio.py` |
| `indicator_npr_filename_slug_analysis.json` | NPR filename slug analysis | `scripts/analysis/analyze_npr_filename_slugs.py` |
| `indicator_npr_identities.json` | Recovered NPR episode identities | `scripts/recovery/recover_npr_audio.py`, `scripts/recovery/recover_npr_identities.py` |
| `indicator_npr_identity_probe.json` | NPR identity probe results | `scripts/recovery/probe_npr_identity.py` |
| `indicator_npr_legacy_feed_probe.json` | NPR legacy feed probe results | `scripts/recovery/probe_npr_legacy_feed.py` |
| `indicator_npr_player_resolution.json` | NPR player audio resolution | `scripts/recovery/resolve_npr_player_audio.py`, `scripts/recovery/resolve_npr_filename_audio.py` |
| `indicator_npr_redirect_mapping.json` | NPR redirect URL mapping | `scripts/analysis/analyze_npr_redirect_mapping.py` |
| `indicator_npr_story_found_recovery.json` | NPR story-found recovery candidates | `scripts/recovery/recover_npr_story_found_indicator.py` |
| `indicator_npr_story_identity_strict.json` | Strict NPR story identity resolution | `scripts/recovery/resolve_npr_story_identity_strict.py` |
| `indicator_recovered_episodes.json` | Episodes with recovered audio | `scripts/recovery/recover_npr_identities.py`, `scripts/analysis/inspect_recovered_audio.py` |
| `indicator_recovery_test.json` | Early recovery test results | `scripts/recovery/recover_missing_episodes.py` |
| `indicator_recovery_validation.json` | Validated recovery data | `scripts/analysis/validate_recovered_episodes.py` |
| `indicator_unresolved_affiliate_recovery_00_09.json` | Affiliate recovery — entries 00–09 | `scripts/recovery/probe_batch2_final3_identity_recovery.py`, consolidation scripts |
| `indicator_unresolved_affiliate_recovery_10_19.json` | Affiliate recovery — entries 10–19 | same |
| `indicator_unresolved_affiliate_recovery_20_49.json` | Affiliate recovery — entries 20–49 | same |
| `indicator_unresolved_batch_recovery.json` | Batch unresolved recovery | `scripts/recovery/recover_unresolved_indicator_batch.py`, `scripts/recovery/recover_unresolved_indicator_strict.py` |
| `indicator_unresolved_consolidated_evidence_ledger.json` | Consolidated evidence ledger for unresolved episodes | `scripts/recovery/recover_unresolved_indicator_consolidated.py`, `scripts/maintenance/reconcile_unresolved_consolidated.py` |
| `indicator_unresolved_strict_review.json` | Strict unresolved review | `scripts/recovery/discover_unresolved_indicator_web.py`, `scripts/recovery/recover_unresolved_indicator_strict.py` |
| `indicator_unresolved_web_audio_strict_validation.json` | Strict validation of web-discovered audio | `scripts/recovery/strict_validate_discovered_indicator_audio.py` |
| `indicator_unresolved_web_discovery.json` | Web-discovered unresolved episodes | `scripts/recovery/discover_unresolved_indicator_web.py`, consolidation scripts |
| `indicator_wayback_npr_probe.json` | Wayback Machine NPR audio probe | `scripts/recovery/probe_wayback_npr_audio.py`, multiple probe scripts |
| `indicator_wayback_player_probe.json` | Wayback Machine NPR player probe | `scripts/recovery/probe_wayback_npr_player.py` |
| `indicator_wbur_traffic_tariff_probe.json` | WBUR traffic/tariff episode probe | `scripts/recovery/probe_wbur_traffic_tariff.py`, `scripts/maintenance/audit_indicator_completeness.py` |

## data/audits/

Active audit datasets — produced and consumed by completeness/reconciliation scripts.

| File | Description | Primary consumers |
|------|-------------|-------------------|
| `indicator_completeness_audit.json` | Full episode completeness audit | `scripts/maintenance/audit_indicator_completeness.py` (writes), unresolved recovery scripts (reads) |
| `indicator_early_audit.json` | Early episode history audit | `scripts/recovery/indicator_early_history.py` (writes), `scripts/recovery/recover_missing_episodes.py` (reads) |
| `indicator_history_date_audit.json` | Publication-date audit of episode history | `scripts/analysis/audit_history_dates.py` |
| `indicator_unresolved_consolidated_audit.json` | Consolidated unresolved-episode audit | `scripts/recovery/recover_unresolved_indicator_consolidated.py`, `scripts/maintenance/reconcile_unresolved_consolidated.py` |

---

All scripts reference these paths via:

```python
REPO_ROOT = Path(__file__).resolve().parents[N]
SOME_FILE = REPO_ROOT / "data" / "recovery" / "filename.json"   # or "data/audits/"
```
