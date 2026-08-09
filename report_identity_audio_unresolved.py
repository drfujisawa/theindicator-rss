#!/usr/bin/env python3

import json
import re
from datetime import datetime, timezone
from pathlib import Path


INPUT_LEDGER_FILE = "indicator_unresolved_consolidated_evidence_ledger.json"
INPUT_AUDIT_FILE = "indicator_unresolved_consolidated_audit.json"
OUTPUT_REPORT_FILE = "indicator_identity_audio_unresolved_ranked_report.json"
TARGET_STATUS = "identity_found_but_audio_unresolved"
BASE_DIR = Path(__file__).resolve().parent


MANUAL_REVIEW_NOTES = {
    ("2018-07-11", "Fed Accounts For All!"): {
        "rank": 1,
        "identity_confidence": "medium",
        "strongest_evidence": (
            "The only target with a transcript-style NPR URL, a matching NPR "
            "player embed, and a Wayback player capture for the same "
            "story/audio pair (129451895/129454071). The chain is still not "
            "episode-specific because the derived ondemand filename resolves "
            "to a 2010 ATC segment pattern."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "129451895",
                "reason": (
                    "Present in transcript/player URLs, but the derived "
                    "ondemand path points to 2010-08-26 ATC audio instead of "
                    "a 2018 Indicator episode."
                ),
            },
            {
                "id": "129454071",
                "reason": (
                    "Audio ID is tied to the same 2010-style ATC player chain "
                    "and was not validated as episode-specific NPR-hosted "
                    "Indicator audio."
                ),
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Transcript URL uses storyId=129451895 but does not verify the 2018 title/date.",
            "All tested ondemand candidates derived from the player chain were rejected as request errors.",
        ],
        "remaining_recovery_avenues": [
            "Search archived NPR section/theindicator or sections/money pages for 2018-07-11 with the exact title.",
            "Inspect older Wayback captures of the transcript/player HTML for bootstrap JSON that names the 2018 episode directly.",
        ],
    },
    (
        "2018-08-10",
        "Privacy Please: Why Public Companies Go Private (Or Vice Versa)",
    ): {
        "rank": 2,
        "identity_confidence": "low",
        "strongest_evidence": (
            "A specific NPR player pair (922262686/1197919391) plus a Wayback "
            "embed capture survives, which is stronger than pure keyword "
            "search noise even though the surviving story page is a 2020 "
            "privacy article rather than the 2018 Indicator episode."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "545963181",
                "reason": (
                    "Story URL is a 2017 Two-Way article about privacy rights "
                    "in India, not the target Indicator episode."
                ),
            },
            {
                "id": "922262686",
                "reason": (
                    "Story/player ID belongs to a 2020 NPR article about "
                    "online tracking and privacy, not a dated 2018 Indicator "
                    "episode match."
                ),
            },
            {
                "id": "1197919391",
                "reason": (
                    "Audio ID is only linked through the 2020 privacy player "
                    "embed and has no episode-specific 2018 title/date chain."
                ),
            },
        ],
        "rejected_or_dead_end_evidence": [
            "No candidate audio URL survived into validation even though the player/audio pair was preserved.",
            "No verified affiliate page or archive tied the surviving privacy IDs back to the exact episode title.",
        ],
        "remaining_recovery_avenues": [
            "Search Wayback for an NPR page dated 2018-08-10 with the exact title and then follow any archived player bootstrap JSON.",
            "Look for archived affiliate pages with hidden player config or canonical NPR links for this exact title.",
        ],
    },
    ("2018-09-24", "Saudi Arabia & The Paradox of Plenty"): {
        "rank": 3,
        "identity_confidence": "low",
        "strongest_evidence": (
            "Two NPR player/audio pairs (137065443/137065428 and "
            "136439885/136453311) plus Wayback captures preserve Saudi/oil "
            "topic evidence, but both source stories date to 2011 and predate "
            "The Indicator entirely."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "137065443",
                "reason": (
                    "Story page is a 2011 OPEC production article and cannot "
                    "be treated as the 2018 Indicator episode without a "
                    "separate corroborating chain."
                ),
            },
            {
                "id": "136439885",
                "reason": (
                    "Story page is a 2011 Saudi poverty article; thematically "
                    "related but years off the target episode."
                ),
            },
            {
                "id": "137065428",
                "reason": "Audio ID is only tied to the 2011 OPEC player embed.",
            },
            {
                "id": "136453311",
                "reason": "Audio ID is only tied to the 2011 Saudi poverty player embed.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Both ondemand candidates were rejected as request errors.",
            "The preserved player IDs look real but remain pre-Indicator sidebar or topic contamination until an exact 2018 title/date page is found.",
        ],
        "remaining_recovery_avenues": [
            "Search archived 2018 affiliate pages for the exact title and hidden `nprStoryId`/player config.",
            "Search archived NPR money/indicator pages around 2018-09-24 to see whether the Saudi-themed links were related-story contamination.",
        ],
    },
    ("2018-10-09", "China's Social Credit System"): {
        "rank": 4,
        "identity_confidence": "low",
        "strongest_evidence": (
            "One NPR player/audio pair (887239225/887239226) plus two Wayback "
            "captures survives, but the linked story is a 2020 Uighur "
            "genocide report rather than the target October 2018 title."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "887239225",
                "reason": (
                    "Story/player ID belongs to a 2020 NPR report on Uighur "
                    "suppression, not the 2018 Social Credit episode."
                ),
            },
            {
                "id": "887239226",
                "reason": (
                    "Audio ID is preserved only through the same 2020 player "
                    "chain and was not validated as the target episode audio."
                ),
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Archived player capture yielded another derived ondemand candidate, but validation still failed.",
            "The same surviving NPR player chain is also attached to the 2018-10-11 target, so it does not distinguish the two episodes.",
        ],
        "remaining_recovery_avenues": [
            "Locate distinct affiliate or archive evidence that separates the 2018-10-09 and 2018-10-11 China episodes.",
            "Search archived NPR pages from early October 2018 for an exact title/date chain before trusting this player ID.",
        ],
    },
    ("2018-10-11", "China's Brave New World"): {
        "rank": 5,
        "identity_confidence": "low",
        "strongest_evidence": (
            "It preserves the same NPR player/audio pair and Wayback captures "
            "as the 2018-10-09 episode, which at least proves a recoverable "
            "NPR-controlled audio chain existed somewhere, but not which China "
            "episode it belonged to."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "887239225",
                "reason": (
                    "The only surviving story/player ID resolves to a 2020 "
                    "Uighur report and is shared with the 2018-10-09 target."
                ),
            },
            {
                "id": "887239226",
                "reason": (
                    "Audio ID is tied to the same 2020 player chain and does "
                    "not provide an episode-specific match for the 2018-10-11 "
                    "title."
                ),
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Every tested audio candidate for this episode duplicated the same unresolved 2020 NPR chain.",
            "No separate affiliate or archived NPR evidence distinguished this title from the 2018-10-09 episode.",
        ],
        "remaining_recovery_avenues": [
            "Find archived affiliate pages or NPR pages for 2018-10-11 that include the exact title or a distinct player ID.",
            "Search Wayback for station pages carrying the episode transcript/snippet rather than relying on shared China keyword collisions.",
        ],
    },
    ("2018-07-23", "Google's Mobile Monopoly"): {
        "rank": 6,
        "identity_confidence": "low",
        "strongest_evidence": (
            "A single NPR player/audio pair (925895658/925895659) survived, "
            "which is stronger than title-only search noise, but the linked "
            "story is a 2020 antitrust article and all other preserved Google "
            "story URLs are unrelated topic matches."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "925895658",
                "reason": (
                    "Player/story ID belongs to a 2020 antitrust suit article, "
                    "not the 2018 target episode."
                ),
            },
            {
                "id": "925895659",
                "reason": (
                    "Audio ID is only linked through the 2020 antitrust player "
                    "embed and lacks an exact 2018 title/date chain."
                ),
            },
            {
                "id": "677450467",
                "reason": "2018 NPR story is about a Google campus purchase, not a monopoly episode.",
            },
            {
                "id": "920882893",
                "reason": "2020 NPR story is about House monopoly findings, not the target episode.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "The only tested ondemand candidate came from the 2020 antitrust embed and was rejected as a request error.",
            "No verified affiliate or archive capture tied the surviving player ID back to the exact episode title.",
        ],
        "remaining_recovery_avenues": [
            "Search archived 2018 NPR money/indicator pages for the exact title and any distinct 2018 player ID.",
            "Search archived affiliate pages for hidden player config rather than headline-only Google topic matches.",
        ],
    },
    ("2018-04-26", "California's Housing Conundrum"): {
        "rank": 7,
        "identity_confidence": "low",
        "strongest_evidence": (
            "This episode has the widest surviving NPR-controlled surface area "
            "in the ledger: multiple player embeds, multiple audio IDs, and "
            "nine Wayback captures. But every preserved story/player chain "
            "currently resolves to unrelated California topic pages from 2016, "
            "2017, 2019, or 2023."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "524744989",
                "reason": "2017 NPR story is about the Rodney King riots, not housing.",
            },
            {
                "id": "488428577",
                "reason": "2016 NPR story is about Cesar Chavez, not the target episode.",
            },
            {
                "id": "713616857",
                "reason": "2019 NPR story is about sanctuary cities, not housing.",
            },
            {
                "id": "499867678",
                "reason": "2016 NPR story is about bilingual education in California, not housing.",
            },
            {
                "id": "1148846720",
                "reason": "2023 NPR story is about dismantling death row, not housing.",
            },
            {
                "id": "525551088",
                "reason": "Audio ID is tied to the 2017 Rodney King player chain only.",
            },
            {
                "id": "713616858",
                "reason": "Audio ID is tied to the 2019 sanctuary cities player chain only.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Twelve candidate audio URLs were tested and all were rejected as request errors.",
            "The large number of captures preserves evidence of repeated false trails, but not a single exact 2018 housing-title match.",
        ],
        "remaining_recovery_avenues": [
            "Inspect the archived player HTML/JSON from the nine Wayback captures for leaked canonical URLs or related-story metadata.",
            "Search archived affiliate pages carrying the exact title for hidden `nprStoryId` or `audioId` fields.",
        ],
    },
    ("2018-08-17", "Donald Trump's Economic Strategy... Maybe?"): {
        "rank": 8,
        "identity_confidence": "low",
        "strongest_evidence": (
            "It retains three NPR player/audio pairs and multiple Wayback "
            "captures, but the evidence is heavily polluted by generic Trump "
            "topic matches across unrelated health, staffing, shutdown, and "
            "legal coverage."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "501203831",
                "reason": "2016 NPR health story is about Obamacare, not the target economic-strategy episode.",
            },
            {
                "id": "927859091",
                "reason": "2020 NPR story is about the opioid crisis response, not the target episode.",
            },
            {
                "id": "557122200",
                "reason": "2017 NPR story is about unfilled administration positions, not the target episode.",
            },
            {
                "id": "501477057",
                "reason": "Audio ID is only tied to the Obamacare-related player embed.",
            },
            {
                "id": "929609241",
                "reason": "Audio ID is only tied to the opioid-response player embed.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Twelve candidate audio URLs were tested and all failed validation.",
            "The broad Trump keyword produced the noisiest NPR URL set in the target group, so the remaining IDs are not trustworthy without an exact title/date chain.",
        ],
        "remaining_recovery_avenues": [
            "Search archived 2018 NPR business/economy pages for the exact title rather than generic Trump topic pages.",
            "Look for affiliate or archive captures that preserve canonical Indicator links or player bootstrap JSON.",
        ],
    },
    ("2018-10-05", "Who's Hiring?"): {
        "rank": 9,
        "identity_confidence": "very_low",
        "strongest_evidence": (
            "Two NPR player/audio pairs plus Wayback captures survive, but all "
            "source pages are unmistakable WHO (World Health Organization) "
            "keyword collisions rather than the contraction in the episode "
            "title."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "408289115",
                "reason": "2015 NPR Goats and Soda story is about the WHO emergency fund, not the target episode.",
            },
            {
                "id": "479228380",
                "reason": "2016 NPR Goats and Soda story is about WHO reform, not the target episode.",
            },
            {
                "id": "798894428",
                "reason": "2020 NPR story is about WHO and coronavirus, not the target episode.",
            },
            {
                "id": "835179442",
                "reason": "2020 NPR story is about WHO and coronavirus alerts, not the target episode.",
            },
            {
                "id": "408407230",
                "reason": "Audio ID belongs to the WHO emergency fund player chain only.",
            },
            {
                "id": "479349681",
                "reason": "Audio ID belongs to the WHO reform player chain only.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Every tested candidate audio URL came from WHO-topic embeds and was rejected.",
            "No verified affiliate or archived page converted the keyword collision into an episode-specific title/date match.",
        ],
        "remaining_recovery_avenues": [
            "Search archived affiliate pages or NPR episode indexes for the exact punctuation-bearing title `Who's Hiring?`.",
            "Search archived NPR player pages keyed from exact date/title rather than `who` keyword results.",
        ],
    },
    ("2018-04-24", "When China's Ships Come In"): {
        "rank": 10,
        "identity_confidence": "very_low",
        "strongest_evidence": (
            "Only two NPR story URLs survived, and both appear to have been "
            "scraped from unrelated dictionary pages rather than an NPR or "
            "affiliate chain tied to the episode."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "126086329",
                "reason": (
                    "Appears only through dictionary-page contamination and "
                    "was not corroborated by an NPR page, player, archive, or "
                    "affiliate capture."
                ),
            },
            {
                "id": "126309699",
                "reason": "Resolved NPR URL is a 2010 article on Islamic feminists, not the target episode.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "All candidate audio URLs came from non-NPR dictionary pronunciation pages.",
            "No player URL, archive capture, or verified affiliate page survived for this title.",
        ],
        "remaining_recovery_avenues": [
            "Search archived affiliate pages carrying the exact title for hidden canonical NPR links.",
            "Search Wayback for a 2018 NPR episode page using the exact title rather than the word `when`.",
        ],
    },
    ("2018-06-21", "Teenage (Employment) Wasteland"): {
        "rank": 11,
        "identity_confidence": "very_low",
        "strongest_evidence": (
            "The only surviving NPR URLs are generic teen-related stories, and "
            "there is no player embed, archive capture, or verified affiliate "
            "page to lift the chain beyond keyword coincidence."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "457517690",
                "reason": "2015 NPR story is about a police shooting of a teen, not youth employment.",
            },
            {
                "id": "258687578",
                "reason": "2014 NPR story is about teen drivers multitasking, not the target episode.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "Both candidate audio URLs came from non-NPR dictionary pronunciation audio.",
            "No archived NPR player or affiliate evidence survived to connect the title to a real episode page.",
        ],
        "remaining_recovery_avenues": [
            "Search archived affiliate pages for the exact title and any hidden player metadata.",
            "Search archived NPR business/labor pages around 2018-06-21 instead of generic `teen` keyword results.",
        ],
    },
    ("2018-06-13", "Dude, Where's My Trade War?"): {
        "rank": 12,
        "identity_confidence": "very_low",
        "strongest_evidence": (
            "The evidence chain is dominated by keyword collisions around "
            "`dude`, including a current NPR word-origin piece, music pages, "
            "and an unrelated KQED slang article. Nothing surviving is close "
            "to the target trade-war episode."
        ),
        "rejected_or_unverified_ids": [
            {
                "id": "90725993",
                "reason": (
                    "Legacy storyId template lacks any corroborating episode "
                    "title/date/player evidence."
                ),
            },
            {
                "id": "606254804",
                "reason": "NPR URL is a New Music Friday page, not the target episode.",
            },
            {
                "id": "770565791",
                "reason": "NPR URL is a music live sessions series, not the target episode.",
            },
        ],
        "rejected_or_dead_end_evidence": [
            "The preserved affiliate page is a KQED article about the history of the word `dude`, not trade policy.",
            "All tested audio candidates came from the 2025 `dude` etymology chain and were rejected.",
        ],
        "remaining_recovery_avenues": [
            "Search archived affiliate pages or NPR episode indexes for the exact title and trade-war phrasing.",
            "Search archived NPR economy pages from 2018-06-13 rather than `dude` keyword results.",
        ],
    },
}


def load_json(filename):
    with open(BASE_DIR / filename, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(filename, payload):
    with open(BASE_DIR / filename, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)


def unique(values):
    output = []
    seen = set()

    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)

    return output


def extract_story_id(url):
    match = re.search(r"storyId=(\d{5,})", url or "")
    if match:
        return match.group(1)

    match = re.search(r"/(?:19|20)\d{2}/\d{2}/\d{2}/(\d{5,})/", url or "")
    if match:
        return match.group(1)

    match = re.search(r"/transcripts/(\d{5,})/?$", url or "")
    if match:
        return match.group(1)

    return None


def extract_player_ids(url):
    match = re.search(r"/player/embed/(\d{5,})/(\d{5,})", url or "")
    if not match:
        return None, None

    return match.group(1), match.group(2)


def collect_discovered_ids(ledger):
    story_ids = list(ledger.get("npr_story_ids", []))
    player_story_ids = list(ledger.get("npr_player_story_ids", []))
    audio_ids = list(ledger.get("npr_audio_ids", []))

    for url in ledger.get("npr_story_urls", []):
        story_id = extract_story_id(url)
        if story_id:
            story_ids.append(story_id)

    for url in ledger.get("player_urls", []):
        player_story_id, audio_id = extract_player_ids(url)
        if player_story_id:
            player_story_ids.append(player_story_id)
            story_ids.append(player_story_id)
        if audio_id:
            audio_ids.append(audio_id)

    return {
        "discovered_story_ids": unique(story_ids),
        "discovered_player_story_ids": unique(player_story_ids),
        "discovered_audio_ids": unique(audio_ids),
    }


def episode_key(episode):
    return (episode["reference_date"], episode["reference_title"])


def normalize_validation_results(ledger):
    rows = []

    for item in ledger.get("validation_results", []):
        rows.append({
            "candidate_url": item.get("candidate_url"),
            "validation_status": item.get("validation_status"),
            "reason": item.get("reason"),
            "final_url": item.get("final_url"),
            "status_code": item.get("status_code"),
            "content_type": item.get("content_type"),
            "source_url": item.get("source_url"),
            "source_type": item.get("source_type"),
            "discovered_from": item.get("discovered_from"),
        })

    return rows


def build_episode_report(ledger):
    notes = MANUAL_REVIEW_NOTES[episode_key(ledger)]
    ids = collect_discovered_ids(ledger)

    return {
        "rank": notes["rank"],
        "reference_date": ledger["reference_date"],
        "reference_title": ledger["reference_title"],
        "reference_year": ledger.get("reference_year"),
        "reference_episode": ledger.get("reference_episode"),
        "identity_confidence": notes["identity_confidence"],
        "strongest_evidence": notes["strongest_evidence"],
        "npr_ids_found": {
            **ids,
            "verified_episode_specific_story_ids": [],
            "verified_episode_specific_audio_ids": [],
            "rejected_or_unverified_ids": notes["rejected_or_unverified_ids"],
        },
        "npr_story_urls": ledger.get("npr_story_urls", []),
        "player_urls": ledger.get("player_urls", []),
        "affiliate_pages": [item.get("url") for item in ledger.get("affiliate_pages", [])],
        "archived_captures": ledger.get("archive_captures", []),
        "known_audio_ids": ledger.get("npr_audio_ids", []),
        "candidate_audio_urls": ledger.get("candidate_audio_urls", []),
        "audio_candidates_tested": normalize_validation_results(ledger),
        "validation_result": ledger.get("evidence_confidence_explanation"),
        "rejected_or_dead_end_evidence": notes["rejected_or_dead_end_evidence"],
        "remaining_recovery_avenues": notes["remaining_recovery_avenues"],
        "final_classification": ledger.get("final_status"),
    }


def build_report(generated_at=None):
    ledger = load_json(INPUT_LEDGER_FILE)
    episodes = [
        episode
        for episode in ledger.get("episodes", [])
        if episode.get("final_status") == TARGET_STATUS
    ]

    if len(episodes) != len(MANUAL_REVIEW_NOTES):
        raise ValueError(
            "Expected %d target episodes from the ledger, found %d manual-note entries"
            % (len(episodes), len(MANUAL_REVIEW_NOTES))
        )

    missing_notes = [
        episode_key(episode)
        for episode in episodes
        if episode_key(episode) not in MANUAL_REVIEW_NOTES
    ]
    if missing_notes:
        raise ValueError(f"Missing manual notes for episodes: {missing_notes}")

    report_episodes = sorted(
        [build_episode_report(episode) for episode in episodes],
        key=lambda item: item["rank"],
    )

    return {
        "method": "ranked-investigation-report-for-identity-found-but-audio-unresolved-indicator-episodes",
        "generated_at": generated_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_artifacts": [
            INPUT_LEDGER_FILE,
            INPUT_AUDIT_FILE,
            "indicator_npr_story_found_recovery.json",
            "indicator_npr_story_identity_strict.json",
            "indicator_unresolved_web_discovery.json",
            "indicator_unresolved_affiliate_recovery_00_09.json",
            "indicator_unresolved_affiliate_recovery_10_19.json",
            "indicator_unresolved_affiliate_recovery_20_49.json",
        ],
        "target_status": TARGET_STATUS,
        "target_episode_count": len(report_episodes),
        "validated_npr_hosted_episode_audio_discoveries": [],
        "investigation_constraints": [
            "Used PR #2 consolidated ledger/audit as source of truth.",
            "Did not modify indicator_history.json or theindicator_feed.xml.",
            "Preserved rejected/dead-end evidence paths instead of promoting weak matches.",
            "No NPR-hosted candidate in this sandbox was newly validated as a live episode-specific audio response.",
        ],
        "environment_limitations": [
            "Direct fetches to npr.org, ondemand.npr.org, and web.archive.org were not resolvable from this sandbox, so the report relies on already-captured repository evidence and previously preserved archive URLs.",
        ],
        "current_summary": load_json(INPUT_AUDIT_FILE).get("summary", {}),
        "episodes": report_episodes,
    }


def main():
    save_json(OUTPUT_REPORT_FILE, build_report())
    print("Saved:", OUTPUT_REPORT_FILE)


if __name__ == "__main__":
    main()
