# archive/recovery/

Completed historical evidence from one-shot probe and identity-recovery campaigns.
These files are retained for provenance and audit trail only and are **not required
by any current maintained script or test**.

## Contents

### batch2 final-round identity recovery (probe_batch2_final3_identity_recovery.py)

| File | Description |
|------|-------------|
| `batch2_final3_identity_recovery_summary.json` | Summary of all final-round batch-2 probes |

### Active batch2 fresh identity discovery inputs moved out

`batch2_fresh_identity_discovery_summary.json` and the actively reused
`batch2_fresh_identity_discovery_*_diag.json` inputs now live in
`data/recovery/` because maintained recovery tooling reads them there.

### fresh identity discovery (probe_fresh_identity_discovery.py)

| File | Description |
|------|-------------|
| `fresh_identity_discovery_summary.json` | Summary of all initial fresh-discovery probes |
| `fresh_identity_discovery_2018-07-11_diag.json` | Per-date diagnostic for 2018-07-11 |
| `fresh_identity_discovery_2018-08-10_diag.json` | Per-date diagnostic for 2018-08-10 |
| `fresh_identity_discovery_2018-09-24_diag.json` | Per-date diagnostic for 2018-09-24 |

### top-3 ranked prospect probes (probe_top3_ranked_prospects.py)

| File | Description |
|------|-------------|
| `top3_prospects_summary.json` | Summary of top-3 ranked prospect probes |
| `top3_prospect_2018-07-11_diag.json` | Per-episode diagnostic for 2018-07-11 |
| `top3_prospect_2018-08-10_diag.json` | Per-episode diagnostic for 2018-08-10 |
| `top3_prospect_2018-09-24_diag.json` | Per-episode diagnostic for 2018-09-24 |

### Wayback Machine / NPR one-shot probes

| File | Probe script |
|------|-------------|
| `indicator_april2019_wayback_mp3s.json` | `probe_wayback_april2019_indicator_mp3s.py` |
| `indicator_traffic_tariff_wayback_mp3.json` | `probe_wayback_traffic_tariff_mp3.py` |
| `indicator_judgement_bonds_probe.json` | `probe_judgement_bonds_deep.py` |
| `indicator_judgement_bonds_npr_story_id_probe.json` | `probe_judgement_bonds_npr_story_id.py` |

### Simplecast proof-of-concept

| File | Script |
|------|--------|
| `poc_simplecast_results.json` | `scripts/analysis/poc_simplecast_enclosure_recovery.py` |

### NPR e-parameter test artefact

| File | Script |
|------|--------|
| `indicator_npr_e_parameter_test.json` | `tests/test_npr_e_parameter.py` |

---

**Do not delete these files** — they provide an audit trail for the source-resolution
decisions baked into `indicator_history.json` and `indicator_enclosure_map.json`.

Future deletion candidates should be discussed and tracked in a separate issue.
