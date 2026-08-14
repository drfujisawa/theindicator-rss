# Final catalog completeness audit

Audit date: 2026-08-14

## Verdict

The archive is **complete for its defined scope**: the original NPR program
510325 feed from its December 1, 2017 launch trailer through the present.

This is an evidence-based catalog verdict, not a claim that every page labeled
with Indicator-related words belongs in the standalone feed. The audit requires
first-party program identity, rejects cross-feed promotions and compilation
pages, and retains the evidence for every deliberate exclusion.

## Current production baseline

- RSS items: 2,195
- Full episodes: 2,194
- Launch trailers: 1
- Unique RSS GUIDs: 2,195
- History records: 1,893 (all unique by NPR story ID)
- Enclosure-map records: 1,893 (all unique)
- Unknown or zero enclosure lengths: 0
- Oldest item: December 1, 2017 — `Coming Soon`
- Newest item at audit time: August 14, 2026
- Yearly RSS counts: 2017: 12; 2018: 252; 2019: 250; 2020: 257;
  2021: 251; 2022: 251; 2023: 251; 2024: 254; 2025: 256;
  2026: 161

The trailer is the first item in NPR's original feed and is published with
`itunes:episodeType="trailer"`. All 59 restored launch-period items have unique
story IDs and GUIDs, live NPR-hosted audio, and exact positive byte lengths.

## Independent catalog checks

### NPR official feed

The current NPR feed exposes 300 items. Every one matches the local RSS by
normalized publication date and title. NPR-only records: 0.

Source: https://feeds.npr.org/510325/podcast.xml

### Apple Podcasts

Apple's public lookup catalog exposes the latest 200 episodes. Every one matches
the local RSS by normalized publication date and title. Apple-only records: 0.

Source: https://podcasts.apple.com/us/podcast/the-indicator-from-planet-money/id1320118593

### TheTVDB historical seasons

TheTVDB exposes 2,105 dated records for 2018–2026. The original comparison found
84 apparent omissions; strict first-party recovery resolved and added the genuine
omissions in reviewed batches. Only two raw catalog mismatches remain, and neither
is an unresolved missing episode:

1. **Are you afraid of inflation?** (October 29, 2021) is an alternate catalog
   title for the existing Indicator release `1050665635`, **Night of the living
   inflation**, on the same date. It is not a second episode.
2. **BONUS: Wisdom From The Top** is a 59-minute cross-promotion published in the
   *Consider This from NPR* feed. It is not an episode of program 510325.

Sources:

- https://thetvdb.com/series/the-indicator-from-planet-money-podcast
- https://www.npr.org/2021/10/29/1050665635/night-of-the-living-inflation
- https://podcasts.apple.com/us/podcast/bonus-wisdom-from-the-top/id1503226625?i=1000539563884

Unresolved TheTVDB candidate omissions: 0.

## Original December 2017 launch feed

A preserved March 10, 2018 snapshot of NPR's original program feed contains 59
items before the former March 12 local cutoff:

- 58 full episodes
- 1 launch trailer
- 59 unique source GUIDs
- 59 unique NPR story IDs
- 59 exact live `206 audio/mpeg` byte-range responses

All 59 are now present in production. The isolated updater compatibility test
also demonstrated that the scheduled updater preserves every restored item and
all trailer metadata without changing any existing episode.

Source snapshot:
https://web.archive.org/web/20180310110945id_/https://www.npr.org/rss/podcast.php?id=510325

Supporting reports:

- `indicator_pre_march_2018_catalog_audit.json`
- `indicator_pre_march_2018_staging_report.json`
- `indicator_pre_march_2018_application_report.json`
- `indicator_updater_compatibility_report.json`

## Deliberate non-feed records

Four records in the history/enclosure map remain outside the RSS. All are Planet
Money **Two Indicators** compilation pages that reuse existing Indicator segments;
they are not missing standalone releases and do not have distinct Indicator
enclosures:

- `1013954358` — Two Indicators: Clogged Ports And Corporate Vets
- `1029846068` — Two Indicators: Will Remote Work Kill The Office?
- `1034085667` — Two Indicators: Water Pressure
- `1038307729` — Two Indicators: Women And Work

These records remain explicitly classified rather than silently discarded.

## Reproduction

Run:

```powershell
python scripts/analysis/audit_final_catalog_completeness.py
```

The command refreshes `indicator_final_catalog_completeness_audit.json` and
prints the same current counts, external comparisons, classified mismatches,
launch-feed verification, deliberate exclusions, and verdict.

The scheduled feed updater is separately tested with:

```powershell
python scripts/analysis/test_updater_compatibility.py
```

## Ongoing completeness gate

The six-hour updater and read-only integrity workflow protect the established
archive from episode-count, GUID, ordering, XML, enclosure, and known-length
regressions. A future external catalog mismatch should be treated as a candidate,
not automatically admitted: require canonical NPR identity, matching audio,
collision checks, isolated staging, and hash-guarded application.
