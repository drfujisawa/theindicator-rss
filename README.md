# The Indicator — Full History RSS Feed

An unofficial archival RSS feed for NPR's **The Indicator from Planet Money**, covering
the complete playable archive from **April 2018** to the present — over **1,760 episodes**.

## Subscribe

```
https://drfujisawa.github.io/theindicator-rss/theindicator_feed.xml
```

[Open in browser / copy RSS link](https://drfujisawa.github.io/theindicator-rss/theindicator_feed.xml)

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
4. Appends those new episodes to the archive.
5. Sorts all episodes newest-first and writes the file back.

Historical episodes already in the archive are **never removed**.

## How the historical archive was recovered

NPR's official feed only covered roughly the most recent 300 episodes. To extend
coverage back to 2018, a multi-stage recovery process was run:

1. **History crawl** — `theindicator_history.py` scraped NPR's website archive
   to build `indicator_history.json`: a catalogue of episode metadata (title, date,
   story ID, NPR URL) for every known Indicator episode back to April 2018.

2. **Enclosure recovery** — `recover_enclosures_bulk.py` resolved a playable audio
   URL for each history entry by probing NPR's audio APIs, Simplecast CDN, and
   affiliate station archives. Results are stored in `indicator_enclosure_map.json`.

3. **Feed build** — `build_complete_feed.py` merged the current NPR feed with all
   resolved history entries into a single complete `theindicator_feed.xml`.

A small number of very early episodes could not be matched to a still-accessible
audio file and are excluded from the feed. These are recorded with status `no_audio`
in `indicator_enclosure_map.json`.

## Project structure

| File / script | Purpose |
|---|---|
| `theindicator_feed.xml` | The published RSS feed (1,760+ episodes) |
| `theindicator_rss.py` | Automated feed updater (runs every 6 hours) |
| `theindicator_history.py` | Crawls NPR's archive to build episode history |
| `scripts/maintenance/build_complete_feed.py` | One-time build: merges history + enclosure map into the feed |
| `recover_enclosures_bulk.py` | Bulk audio-URL recovery for history entries |
| `indicator_history.json` | Catalogue of all known episodes (metadata) |
| `indicator_enclosure_map.json` | Resolved audio URLs for history episodes |
| `index.html` | GitHub Pages landing page |
| `.github/workflows/update-feed.yml` | Scheduled feed update workflow |
| `.github/workflows/crawl-history.yml` | Manual history-crawl workflow |
| `.github/workflows/recover-enclosures-bulk.yml` | Manual enclosure-recovery workflow |

Non-production scripts are organized under `scripts/` (`maintenance`, `recovery`, `analysis`) and tests live under `tests/`. Historical `.json` artifacts remain at repository root as retained evidence/data outputs.

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
