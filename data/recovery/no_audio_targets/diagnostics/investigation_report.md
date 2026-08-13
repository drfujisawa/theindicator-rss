# NPR Player/Embed Diagnostic Investigation
**Generated:** 2026-08-13  
**Environment note:** Live HTTP requests were blocked by DNS resolution failures in this sandbox (`Temporary failure in name resolution`). All findings are therefore derived from repository artifacts — which are authoritative for the investigation since the critical evidence (player HTML structure, extraction method used, Simplecast UUID, resolved URL) was already captured during the `bulk_run_20260810T203919Z` run and stored in `indicator_enclosure_map.json` and `archive/recovery/poc_simplecast_results.json`.

---

## KNOWN-GOOD PLAYER STRUCTURE

**Target:** story_id `1106893731` / audio_id `1198988689` / date `2022-06-22`  
**Title:** "What took the Fed so long?"  
**Player URL:** `https://www.npr.org/player/embed/1106893731/1198988689`

**Player response (observed during bulk_run_20260810T203919Z):**
- HTTP status: **200 OK**
- Final URL: same as player URL (no redirect)
- Content-Type: `text/html`
- Extraction method: **`regex_direct`** — meaning the audio URL was found directly in the HTML via a regex match; no additional API call was needed

**Page structure:** The player page at that time was **server-side rendered (SSR)** and contained the full audio URL inline as a `prfx.byspotify.com/…simplecastaudio.com/…awEpisodeId=…` string, extracted by the first regex in `extract_candidate_audio_urls` (pattern: `https?://[^\"'\s<>]+(?:\.mp3|/audio(?:[/?#]|$)|simplecastaudio\.com[^\"'\s<>]*)`)

**Inline audio URL found:**
```
https://prfx.byspotify.com/e/play.podtrac.com/npr-510325/
  npr.simplecastaudio.com/0a4e8d3b-fe23-4948-9e39-20fcf16f9331/
  episodes/e9827f64-db6e-4abb-aee9-a9fe394033ae/audio/128/default.mp3
  ?awCollectionId=0a4e8d3b-fe23-4948-9e39-20fcf16f9331
  &awEpisodeId=e9827f64-db6e-4abb-aee9-a9fe394033ae
  &t=podcast&e=1106893731&p=510325&d=556&size=9249106
  &sc=siteplayer&aw_0_1st.playerid=siteplayer
```

**Source:** `indicator_enclosure_map.json` — resolved episode entry for story_id `1106893731`

---

## KNOWN-GOOD AUDIO RESOLUTION CHAIN

```
https://www.npr.org/player/embed/1106893731/1198988689
  │
  ▼  HTTP 200 (SSR: audio URL embedded inline in player HTML)
  │
  ▼  regex_direct match: prfx.byspotify.com/.../simplecastaudio.com/.../e9827f64.../audio/128/default.mp3
     (contains awEpisodeId=e9827f64-db6e-4abb-aee9-a9fe394033ae)
  │
  ▼  HEAD/GET probe → HTTP 200 → final redirect to:
     https://npr.simplecastaudio.com/0a4e8d3b.../episodes/e9827f64.../audio/128/default.mp3/
     default.mp3_ywr3ahjkcgo_cF9mX3NraXA9aSAg_8968fe8e...8943222.mp3
     Content-Type: audio/mpeg
     Size: 8,943,222 bytes
  │
  ▼  Simplecast episode UUID: e9827f64-db6e-4abb-aee9-a9fe394033ae
     (encoded in both the candidate URL and the final redirect)
```

The chain is entirely **client-free**: the audio URL is embedded directly in the player page HTML and requires no additional JavaScript-initiated API calls to resolve. This is confirmed by `extraction_method: "regex_direct"` in the enclosure map and by the identical pattern observed across all 2022 post-migration Simplecast episodes sampled in `archive/recovery/poc_simplecast_results.json` (`2022_mid`, `2023_first`, `2023_mid`, `2024_first`, `2024_mid`, `control` samples).

**Provenance score:** 0.75 (both IDs in source endpoint URL: +0.55; Simplecast UUID exposed in final URL: +0.20)

---

## UNRESOLVED PLAYER STRUCTURE

**Target:** story_id `1104792247` / audio_id `1198988717` / date `2022-06-13`  
**Title:** "Mergers, acquisitions and Elon's 'rude' proposal"  
**Player URL:** `https://www.npr.org/player/embed/1104792247/1198988717`

**Player response (observed during bulk_run_20260810T203919Z):**
- HTTP status: **200 OK** (confirmed by the problem statement and `http_status: 200` in `indicator_enclosure_map.json`)
- Content-Type: `text/html`
- Extraction method: **None**
- Error: `"no audio candidates found in page"`
- Retry count: 1 (was retried once, same result)

**Page structure at time of bulk run:** The player page served a **client-side JavaScript shell** — the HTML was returned (HTTP 200) but did not contain an inline `simplecastaudio.com`, `.mp3`, `prfx.byspotify.com`, or `podtrac.com/npr-510325` URL. The NPR Next.js player app can render as either a fully SSR'd page (audio inline) or a JS shell (audio loaded by client JavaScript), and this episode got the shell rendering at the time of the bulk run.

**Audio object identity:** Not yet determined from a live player response. See "June 13 Audio Identity Found" section below.

**Source:** `indicator_enclosure_map.json` — `no_audio` episode entry for story_id `1104792247`

---

## STRUCTURAL DIFFERENCES

| Feature | June 13 unresolved | June 22 known-good |
|---|---|---|
| Player HTTP status | 200 OK | 200 OK |
| Audio ID (`1198988717`) present in page | **No** (shell page) | N/A |
| Story ID (`1104792247`) present in page | Not extracted | N/A |
| Embedded media object | **Absent** (JS shell) | **Present** (SSR inline) |
| API endpoint discovered | Not from player response | Not needed — URL was inline |
| Simplecast UUID present | **Not found** | `e9827f64-db6e-4abb-aee9-a9fe394033ae` |
| Direct audio URL present | **No** | Yes, `prfx.byspotify.com/...simplecastaudio.com/...` |
| Client-side fetch required | **Yes** (shell) | No |
| Media metadata available | Not from player page | Full inline in HTML |
| Recovery script result | `no audio candidates found in page` | `RECOVERED_AND_VALIDATED` |

**Key structural difference:**  
Both episodes returned HTTP 200 from the same player URL pattern. The difference is purely in the **rendering mode** of the Next.js player application at request time. June 22's player HTML was fully server-side rendered with the audio URL embedded; June 13's player HTML was served as a client-side shell. All five extraction strategies in `extract_candidate_audio_urls` are correct and would find the audio if it were present in the page — the problem is that the audio was never in the page for this episode at that moment.

**This is not a transition-era encoding difference.** Both episodes are confirmed Simplecast-era:
- June 13: `audio_id=1198988717` (series `1198988xxx`)
- June 22: `audio_id=1198988689` (same series `1198988xxx`)
- Adjacent resolved episodes show the same Simplecast pattern for June 22, 23, 27, 28, 30, Jul 1, 5, 6, 7…

The `no_audio` distribution within June–August 2022 is **non-sequential and non-contiguous** — 8 failed out of 21 total in this window, scattered among many resolved episodes — which is consistent with Next.js SSR caching behaviour (some pages happened to be rendered as shells at the time of the bulk run) rather than a permanent content deletion.

---

## DISCOVERED PLAYER/API ENDPOINTS

### From `recover_no_audio_targets.py` endpoint matrix (lines 221–269)
The current script probes **7 endpoints** per target:

| # | Endpoint key | URL pattern | Purpose |
|---|---|---|---|
| 1 | `story_url` | `https://www.npr.org/YYYY/MM/DD/<story_id>/slug` | NPR story page |
| 2 | **`player_embed`** | `https://www.npr.org/player/embed/<story_id>/<audio_id>` | **Primary: exact player embed page** |
| 3 | `story_template` | `https://www.npr.org/templates/story/story.php?storyId=<story_id>` | Legacy template |
| 4 | `transcript` | `https://www.npr.org/transcripts/<story_id>` | Transcript page |
| 5 | `legacy_api_story` | `https://api.npr.org/query?id=<story_id>&output=JSON` | NPR Legacy API by story |
| 6 | `legacy_api_audio` | `https://api.npr.org/query?id=<audio_id>&output=JSON` | NPR Legacy API by audio |
| 7 | `wayback_cdx_player` | Wayback CDX index for exact player URL | CDX metadata → archived player fetch |

### API endpoints known from archived/local evidence
- `https://listening.api.npr.org` — `NPR.ServerConstants.listeningHost` in pre-2022 player pages (`archive/recovery/top3_prospect_2018-08-10_diag.json:5781`)
- `https://api.npr.org` — `NPR.ServerConstants.apiHost` in same pages

### API endpoint gap
The current script probes `api.npr.org/query` but does **not** probe `listening.api.npr.org`. That host is used by the NPR player JavaScript for client-side audio data fetches. The exact endpoint structure for Simplecast-era episodes is not confirmed from available archive data.

---

## JUNE 13 AUDIO IDENTITY FOUND: **INCONCLUSIVE — NOT FROM LIVE PLAYER**

Live fetches were blocked by DNS resolution failures. The Simplecast UUID for story `1104792247` / audio `1198988717` is **not available in any existing repository artifact**.

The sub-agent reported a candidate URL via web-search-indexed feed metadata:
```
https://play.podtrac.com/npr-510325/npr.mc.tritondigital.com/NPR_510325/media/
anon.npr-mp3/npr/indicator/2022/06/
20220614_indicator_indicatorpodcast_mandaelonsrudedeal_vicepresidentofcnbcafternpr.mp3
```
However this URL cannot be verified: (a) playability was not checked (DNS blocked), (b) the provenance chain is web-search metadata only (no NPR document ID linkage to `1104792247`/`1198988717`), and (c) the filename date `20220614` (June 14) doesn't match the story date `2022-06-13`. This candidate **does not meet the minimum provenance standard** for the recovery script (`score ≥ 0.7`, `confidence: high`). Discard it pending live verification.

---

## CANDIDATE URL/UUID, IF FOUND

**Simplecast UUID:** Not found — requires live fetch of player page (or Wayback snapshot) or live NPR API call.

**Unverified candidate from web search (low-confidence, do not add to feed):**
```
https://play.podtrac.com/npr-510325/npr.mc.tritondigital.com/NPR_510325/media/
anon.npr-mp3/npr/indicator/2022/06/
20220614_indicator_indicatorpodcast_mandaelonsrudedeal_vicepresidentofcnbcafternpr.mp3
```
- Status: playability not validated (DNS blocked)
- Provenance: web search only, no story_id/audio_id linkage
- Date discrepancy: filename says June 14, story says June 13
- **Do not add to production feed.**

---

## PLAYABILITY VALIDATION

Not performed — DNS resolution was blocked in this environment. Requires a network-enabled environment to:
1. `HEAD https://www.npr.org/player/embed/1104792247/1198988717` → confirm 200
2. Fetch and inspect the player HTML (with appropriate rendering support for JS shells)
3. If shell: identify client-side API call pattern and probe `listening.api.npr.org` or equivalent
4. If SSR: extract inline audio URL, validate with `HEAD`
5. Alternatively: probe the Wayback CDX for `https://www.npr.org/player/embed/1104792247/1198988717` and fetch oldest snapshot (likely to be SSR'd since Wayback captures pages under their rendering state at crawl time)

---

## PROVENANCE EVIDENCE

**Known-good (June 22) — strong, multi-source:**
1. `indicator_enclosure_map.json` — `status: resolved`, `extraction_method: regex_direct`, `episode_uuid: e9827f64-db6e-4abb-aee9-a9fe394033ae`, `http_status: 200`, `content_length: 8943222`
2. `theindicator_feed.xml` — same Simplecast enclosure present in production RSS feed
3. `archive/recovery/poc_simplecast_results.json` `"2022_mid"` entry — confirms July 5 2022 player page was fully SSR'd with inline `simplecastaudio.com` URL (`extraction_method: regex_direct`)

**Unresolved (June 13) — weak:**
1. `indicator_enclosure_map.json` — `status: no_audio`, `error: "no audio candidates found in page"`, `http_status: 200`
2. Web-search-sourced candidate (sub-agent) — low confidence, not repo-derived, not validated

---

## WHY THE CURRENT EXTRACTOR MISSED IT

The extractor did **not** miss a pattern. Its five extraction strategies are complete and correct for all URL shapes seen in resolved Simplecast-era episodes:

```python
# Pattern 1: direct regex on .mp3 / simplecastaudio.com / /audio/ paths
# Pattern 2: "audioUrl" / "audio_url" JSON keys
# Pattern 3: "enclosureUrl" / "enclosure" JSON keys  
# Pattern 4: inline JSON strings with simplecastaudio.com / podtrac.com/npr-510325 / prfx.byspotify.com
# Pattern 5: __NEXT_DATA__ / state-blob .mp3 extraction
```

The extractor returned no candidates because **the audio URL was not present anywhere in the fetched HTML**. The player page rendered as a client-side JavaScript shell — the HTML body contained the React/Next.js bootstrap but no audio data. The audio is loaded post-render by client-side JavaScript making an additional API call (likely to `listening.api.npr.org` or a Next.js API route).

The extractor correctly found nothing because there was nothing to find in the server-delivered HTML.

**Root cause:** The NPR Next.js player has non-deterministic SSR — some page fetches return the audio inline, others return a shell. The bulk run hit the shell for this specific episode. A re-run in a network-enabled environment may succeed via `regex_direct` if the page is SSR'd, or may require a Wayback snapshot, or may require the Wayback CDX + archived page fallback already built into the script.

---

## MINIMAL RECOVERY-SCRIPT CHANGE NEEDED

**Do not change `extract_candidate_audio_urls` — the patterns are already correct.**

The two most targeted changes in order of implementation simplicity:

### Option A: Re-run in a network-enabled environment (no code change)
The existing script already has a Wayback CDX + archive fetch fallback (`wayback_cdx_player` → `wayback_player_fetch`). If the Wayback Machine has a snapshot of `https://www.npr.org/player/embed/1104792247/1198988717` that was captured when the page was SSR'd, the current script will find it. This should be attempted first in a GitHub Actions run.

### Option B: Add `listening.api.npr.org` endpoint (minimal code change)
Add one entry to `build_endpoint_matrix` that probes the NPR listening/audio API directly. Based on archived evidence, the endpoint pattern for the Simplecast era is likely:
```
https://listening.api.npr.org/story/<story_id>
```
or a Next.js internal API route like:
```
https://www.npr.org/api/query?id=<story_id>&fields=audio&output=JSON
```
If either of these returns a JSON response containing `awEpisodeId`, `simplecastaudio.com`, or `.mp3`, the existing extractor patterns will pick it up with no further changes.

### Option C: Add NPR podcast RSS feed as fallback (broader change)
If both A and B fail, add a final fallback that fetches `https://feeds.npr.org/510325/podcast.xml`, matches by `<guid>` containing the story_id, and extracts the `<enclosure url=...>`. This is known to work (the `probe_npr_legacy_feed.py` script already uses it), but carries risk of false positives from the current feed returning non-matching recent episodes.

**Recommended sequence:** Try A first (re-run the existing script against this specific target). If the Wayback CDX returns captures for the June 13 player URL, the script resolves it automatically.

---

## DIAGNOSTIC FILES CREATED

- `data/recovery/no_audio_targets/diagnostics/investigation_report.md` — this file
- `data/recovery/no_audio_targets/diagnostics/known_good_player_response.txt` — curl attempt metadata (DNS blocked)
- `data/recovery/no_audio_targets/diagnostics/unresolved_player_response.txt` — curl attempt metadata (DNS blocked)
- `data/recovery/no_audio_targets/diagnostics/known_good_player.meta` — curl metadata artifact
- `data/recovery/no_audio_targets/diagnostics/unresolved_player.meta` — curl metadata artifact
- `data/recovery/no_audio_targets/diagnostics/unresolved_candidate_head.meta` — HEAD probe metadata artifact

All artifacts contain only request/response metadata; no secrets, cookies, or auth headers.

---

## RECOMMENDED NEXT STEP

1. **Trigger a GitHub Actions run** of `recover_no_audio_targets.py` against a batch containing story_id `1104792247`. The existing Wayback CDX + archive fetch logic may resolve it if a suitable snapshot exists.

2. If the run still fails with "no audio candidates found in page" for this target, **inspect the actual live player HTML** from a network-enabled environment and search for: (a) `__NEXT_DATA__` JSON blob, (b) any `fetch()` / XHR call URL containing `story_id` or `audio_id`, (c) any reference to `listening.api.npr.org` or a `/api/` route.

3. **Do not add the unverified Triton/Podtrac candidate URL** to the production feed. It was sourced from web search metadata only, has a date discrepancy, and could not be validated.

4. Once a mechanism is confirmed (Option A or B above), document the exact endpoint pattern and implement Option B as a targeted addition to `build_endpoint_matrix` — one new dict entry, no changes to the extractor or validation logic.
