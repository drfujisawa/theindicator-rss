# Final catalog completeness audit

Audit date: 2026-08-13

## Verdict

The archive cannot currently be called complete. The prior `0 unresolved` result was
correct only relative to the project's existing recovery reference set. A new,
independent catalog comparison found additional records outside that set.

Production was not modified by this audit.

## Local baseline

- RSS items: 2,057
- Unique RSS GUIDs: 2,057
- History records: 1,756 (all unique by NPR story ID)
- Enclosure-map records: 1,756 (all unique)
- Yearly RSS counts: 2018: 205; 2019: 248; 2020: 254; 2021: 248;
  2022: 246; 2023: 246; 2024: 208; 2025: 242; 2026: 160

Four history/map records are absent from the RSS itself. Follow-up review found
that all four have enclosure status `no_audio` and are Planet Money "Two
Indicators" compilation pages describing reused Indicator segments. They are
not currently classified as missing standalone Indicator feed episodes:

- 1013954358 — 2021-07-07 — Two Indicators: Clogged Ports And Corporate Vets
- 1029846068 — 2021-08-20 — Two Indicators: Will Remote Work Kill The Office?
- 1034085667 — 2021-09-03 — Two Indicators: Water Pressure
- 1038307729 — 2021-09-17 — Two Indicators: Women And Work

## Independent sources

### NPR official feed

The official program 510325 feed currently exposes 300 episodes (2025-06-17
through 2026-08-13). Every one matches the local RSS by normalized publication
date and title. NPR-only records: 0.

Source: https://feeds.npr.org/510325/podcast.xml

### Apple Podcasts catalog

Apple's public lookup API exposes the latest 200 episodes. All 200 match both
the NPR feed and local RSS by normalized publication date and title. Apple-only
records: 0.

Source: https://podcasts.apple.com/us/podcast/the-indicator-from-planet-money/id1320118593

### TheTVDB historical seasons

TheTVDB exposes 2,105 dated records across its 2018–2026 season pages. After
matching against the local RSS by exact normalized title, or fuzzy title within
a two-day date tolerance (to absorb timezone and punctuation differences), 84
records have no plausible local counterpart:

- 2019: 3
- 2020: 3
- 2021: 5
- 2022: 5
- 2023: 6
- 2024: 47
- 2025: 15

The largest suspicious clusters are January–February and November–December
2024, plus January 2–17, 2025. These are not explainable as ordinary weekend or
holiday gaps. They require first-party identity and audio validation before any
production change.

### First resolved cluster: January 29–31, 2024

First-party follow-up changed the interpretation of three comparison candidates:

- `1197961492`, January 29, is already in the archive with the correct enclosure
  and GUID, but its title/link were replaced by the collection metadata "The
  Military Industry ... It's Complex." It needs a metadata correction, not a
  second RSS item.
- `1197961507`, January 30, and `1197961524`, January 31, are genuine omitted
  Indicator episodes. Each now has a canonical NPR page, distinct Apple episode
  ID, Simplecast episode UUID, live NPR-program audio response, duration, and
  measured byte length.

An isolated stage at `work/2024-defense-cluster-staging` adds the two omitted
episodes and corrects the January 29 title/link. All staging checks pass and the
three production artifacts remain byte-for-byte unchanged. Consequently, the
raw comparison list remains reproducibly 84 records, but only 83 remain
possible omissions after identifying the January 29 metadata alias; two of
those 83 are now fully validated and staged.

Source: https://thetvdb.com/series/the-indicator-from-planet-money-podcast

### Other catalog signals

- Podchaser reported 2,086 episodes as of 2026-05-03, while the local archive
  contained 1,983 items through 2026-05-01. The raw 103-record difference is
  not directly actionable because Podchaser may count trailers, bonuses, or
  duplicates, but it independently supports further investigation.
- SensCritique's yearly counts are internally inconsistent with both NPR and
  TheTVDB (including undercounts and an impossible 297-record 2020 season), so
  it was used as discovery support only, not as an authority.
- NPR documentation confirms the brand launched in December 2017, while the
  program-510325 catalogs examined here begin March 12, 2018. Whether the
  December 2017–March 2018 material belongs in this particular feed remains a
  scope question and is not counted among the 84 candidates.

## Reproduction

Run:

```powershell
python scripts/analysis/audit_final_catalog_completeness.py
```

The script prints the exact 84-candidate list with dates, titles, season/episode
numbers, and TheTVDB evidence URLs, along with fresh NPR and Apple comparisons.

## Recommended next gate

Treat the remaining comparison records as candidates, not accepted episodes. Resolve them in
date-clustered batches using the established strict standard: canonical NPR
identity, matching player identity, validated NPR-hosted audio, collision
checks, isolated staging, and hash-guarded application. Keep the four
`no_audio` compilation pages out of the RSS unless new evidence proves they are
standalone Indicator releases with their own valid enclosures.
