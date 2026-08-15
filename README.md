# The Indicator — Full History RSS Feed

An unofficial archival RSS feed for NPR's **The Indicator from Planet Money**, covering
the recovered playable archive from the **December 2017 launch** to the present.

As of August 14, 2026, the published feed contains **2,195 items**—2,194 full
episodes and NPR's original one-minute launch trailer. All have
unique GUIDs, playable enclosure URLs, and positive enclosure byte lengths.

## Subscribe

```
https://drfujisawa.github.io/theindicator-rss/theindicator_feed.xml
```

[Open in browser / copy RSS link](https://drfujisawa.github.io/theindicator-rss/theindicator_feed.xml)

Overcast users can subscribe separately to the older portion of the archive:

```
https://drfujisawa.github.io/theindicator-rss/theindicator_overcast_archive.xml
```

Paste the URL above into any podcast app that accepts a custom RSS feed
(Overcast, Pocket Casts, AntennaPod, Castro, etc.).

## Why this exists

NPR's official Indicator feed only exposes a limited window of recent episodes:

```
https://feeds.npr.org/510325/podcast.xml
```

Episodes fall off that feed after a while. This project captures new episodes
as they appear and retains them permanently, so the full back-catalogue remains
accessible in a single feed.

## How the automatic updater works

A GitHub Actions workflow (`update-feed.yml`) runs every six hours. It:

1. Downloads NPR's current RSS feed.
2. Loads the existing `theindicator_feed.xml` from the repository.
3. Finds any episodes in NPR's feed that are not yet in the archive (matched by GUID).
4. If no new GUIDs are found, exits without rewriting the archive or changing
   show-level metadata.
5. Otherwise, appends the new episodes, sorts all episodes newest-first, and
   writes the file back.
6. Validates XML structure, unique GUIDs, reverse-chronological ordering,
   enclosure metadata, and non-decreasing episode and known-length counts before
   committing anything.
7. Generates `theindicator_overcast_archive.xml` from older items, including a
   250-episode overlap before Overcast's nominal 2,000-episode boundary. The
   overlap accommodates episodes retained in Overcast's cache but no longer
   present in the current feed.

Historical episodes already in the archive are **never removed**. The updater
must pass the integrity gate before it can commit a changed feed.

The same checks run in the read-only `feed-integrity.yml` workflow on relevant
pushes and pull requests. Run the unit tests and validate the current feed locally
with:

```shell
python -m unittest tests.test_feed_integrity
python scripts/validate_feed_integrity.py
```

When reviewing a proposed feed change, compare it with the committed feed to
reject episode-count or enclosure-length regressions:

```shell
python scripts/validate_feed_integrity.py --baseline-ref HEAD
```

The validator checks that the XML is readable; every item has a title, valid
publication date, unique non-empty GUID, HTTP(S) MP3 enclosure, and non-negative
byte length; items are newest-first; episode count does not decrease; and the
number of unknown enclosure lengths does not increase. The current feed has
**zero** unknown enclosure lengths.

A separate read-only `catalog-completeness.yml` workflow runs on the first day
of every month and can also be started manually. It compares the archive with
NPR, Apple Podcasts, TheTVDB, and the preserved original launch feed; fails if
new unresolved catalog omissions appear; and uploads its machine-readable report
for 30 days. It has no write permission and never commits production changes.

Run the same strict completeness gate locally with:

```shell
python scripts/analysis/audit_final_catalog_completeness.py --require-complete
```

## How the historical archive was recovered

NPR's official feed only covered roughly the most recent 300 episodes. To extend
coverage back to 2018, a multi-stage recovery process was run:

1. **History crawl** — `theindicator_history.py` scraped NPR's website archive
   to build `indicator_history.json`: a catalogue of episode metadata (title, date,
   story ID, NPR URL) for known Indicator episodes, supplemented by a provenance-
   preserving recovery audit that reaches the December 2017 launch.

2. **Enclosure recovery** — `recover_enclosures_bulk.py` resolved a playable audio
   URL for each history entry by probing NPR's audio APIs, Simplecast CDN, and
   affiliate station archives. Results are stored in `indicator_enclosure_map.json`.

3. **Feed build** — `scripts/maintenance/build_complete_feed.py` merged the current
   NPR feed with all resolved history entries into a single
   `theindicator_feed.xml`.

4. **Independent completeness audit** — the local archive was compared with NPR,
   Apple Podcasts, TheTVDB, and other catalog evidence. Candidate omissions were
   admitted only after first-party identity and audio validation, collision checks,
   isolated staging, and hash-guarded application. The final audit trail is in
   `data/audits/indicator_final_catalog_completeness_audit.md` and the associated
   staging/application reports.

5. **Original launch-catalog recovery** — a preserved March 2018 snapshot of NPR's
   original program feed established that 58 episodes plus the launch trailer were
   missing before the former March 12 cutoff. All 59 canonical NPR identities and
   audio files were verified, collision-reviewed, staged, and applied. The trailer
   is retained as the feed's first item with `itunes:episodeType="trailer"`. The
   audit and staging evidence are under `data/audits/indicator_pre_march_2018_*`.

6. **Enclosure-length repair** — 155 legacy entries whose byte length was unknown
   were measured with one-byte HTTP range probes. All 155 were repaired; no
   `length="0"` enclosures remain. The measurements and before/after hashes are in
   `data/audits/indicator_zero_length_repair_report.json`.

Four catalog records remain intentionally outside the RSS. They are Planet Money
“Two Indicators” compilation pages that reuse existing Indicator segments, not
missing standalone releases, and have status `no_audio` in
`indicator_enclosure_map.json`. Two cross-feed/bonus comparison records were also
excluded because the evidence did not establish them as standalone Indicator feed
episodes. These exclusions are documented in the audit data rather than silently
treated as missing episodes.

## Project structure

### Root (production files only)

| File / script | Purpose |
|---|---|
| `theindicator_feed.xml` | The published RSS feed (2,195 items as of August 14, 2026) |
| `theindicator_overcast_archive.xml` | Older episodes with a 250-item Overcast safety overlap |
| `theindicator_rss.py` | Automated feed updater (runs every 6 hours) |
| `theindicator_history.py` | Crawls NPR's archive to build episode history |
| `recover_enclosures_bulk.py` | Bulk audio-URL recovery for history entries |
| `indicator_history.json` | Catalogue of all known episodes (metadata) |
| `indicator_enclosure_map.json` | Resolved audio URLs for history episodes |
| `index.html` | GitHub Pages landing page |
| `.github/workflows/update-feed.yml` | Scheduled feed update workflow |
| `.github/workflows/feed-integrity.yml` | Read-only feed integrity checks |
| `.github/workflows/catalog-completeness.yml` | Monthly read-only external catalog audit |
| `.github/workflows/crawl-history.yml` | Manual history-crawl workflow |
| `.github/workflows/recover-enclosures-bulk.yml` | Manual enclosure-recovery workflow |

### scripts/

Non-production scripts organized by function (see `scripts/README.md`):

| Directory | Contents |
|---|---|
| `scripts/maintenance/` | Feed build, completeness audit, reconciliation |
| `scripts/recovery/` | Audio recovery, probing, and identity-resolution tools |
| `scripts/analysis/` | Analysis, inspection, and reporting tools |
| `scripts/validate_feed_integrity.py` | Production feed invariant and regression checks |

### data/

Active datasets read and written by maintained scripts (see `data/README.md`):

| Directory | Contents |
|---|---|
| `data/recovery/` | Active recovery artefacts (audio mappings, resolution results, unresolved-episode sets) |
| `data/audits/` | Completeness and reconciliation audit datasets |

### archive/

Retained historical evidence (see `archive/recovery/README.md`):

| Directory | Contents |
|---|---|
| `archive/diagnostics/` | Per-date diagnostic outputs from earlier probe campaigns |
| `archive/recovery/` | Completed one-shot probe summaries and identity-discovery evidence |

### tests/

Unit and integration tests: `tests/`.

## Credits

This repository was adapted from
[xjcl/planetmoney-rss](https://github.com/xjcl/planetmoney-rss), which applies
the same archive-preservation approach to NPR's Planet Money podcast.

## Disclaimer

This is an **independent, unofficial project**. It is not affiliated with, endorsed by,
or connected to NPR, WBUR, or any other rights holder.

Podcast audio and programme content remain the property of NPR and their respective
rights holders. This project does **not** host any audio. All enclosure URLs link
directly to audio files distributed by NPR, Simplecast, or Spotify-operated CDNs.

## Licensing

The upstream project [xjcl/planetmoney-rss](https://github.com/xjcl/planetmoney-rss)
carries **no explicit software license**. Under default copyright law this means all
rights in that original code are reserved by the author.

The Indicator-specific code in this repository (the recovery scripts, build tools, and
feed updater) was written independently for this project. No license file has been
added at this time pending clarification of the upstream situation. If you wish to
reuse this code, please open an issue to discuss it.
