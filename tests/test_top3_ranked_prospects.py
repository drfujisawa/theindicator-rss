#!/usr/bin/env python3
"""
Unit tests for classification and parsing logic in
probe_top3_ranked_prospects.py.

These tests cover all pure functions (no network calls).
"""

import json
import unittest
from pathlib import Path

from scripts.recovery import probe_top3_ranked_prospects as probe
REPO_ROOT = Path(__file__).resolve().parents[1]



class TestIsGenericReject(unittest.TestCase):
    def test_streamtheworld_rejected(self):
        self.assertTrue(
            probe.is_generic_reject(
                "https://playerservices.streamtheworld.com/api/livestream"
            )
        )

    def test_live_npr_rejected(self):
        self.assertTrue(
            probe.is_generic_reject("https://live.npr.org/stream/kqed")
        )

    def test_valid_ondemand_not_rejected(self):
        self.assertFalse(
            probe.is_generic_reject(
                "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/"
                "2018/07/20180711_indicator_fed-accounts.mp3"
            )
        )

    def test_tunein_rejected(self):
        self.assertTrue(
            probe.is_generic_reject("https://tunein.com/radio/NPR-s100625/")
        )


class TestIsIndicatorPath(unittest.TestCase):
    def test_indicator_in_path(self):
        self.assertTrue(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/"
                "2018/07/20180711_indicator_fed.mp3"
            )
        )

    def test_atc_not_indicator(self):
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/atc/"
                "2010/08/20100826_atc_02.mp3"
            )
        )

    def test_me_not_indicator(self):
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/me/"
                "2011/05/20110519_me_02.mp3"
            )
        )


class TestIsEpisodeSpecific(unittest.TestCase):
    def test_slash_date_matches(self):
        self.assertTrue(
            probe.is_episode_specific(
                "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/"
                "2018/07/11/20180711_indicator_fed.mp3",
                "2018-07-11",
            )
        )

    def test_compact_date_matches(self):
        self.assertTrue(
            probe.is_episode_specific(
                "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/"
                "2018/07/20180711_indicator_fed.mp3",
                "2018-07-11",
            )
        )

    def test_wrong_date_rejected(self):
        self.assertFalse(
            probe.is_episode_specific(
                "https://ondemand.npr.org/anon.npr-mp3/npr/atc/"
                "2010/08/20100826_atc_02.mp3",
                "2018-07-11",
            )
        )

    def test_malformed_date_returns_false(self):
        self.assertFalse(
            probe.is_episode_specific(
                "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/x.mp3",
                "2018",
            )
        )


class TestCleanText(unittest.TestCase):
    def test_html_unescape(self):
        self.assertEqual(probe.clean_text("&amp;"), "&")

    def test_escaped_slash_normalised(self):
        self.assertIn(
            "https://example.com/path",
            probe.clean_text("https:\\/\\/example.com\\/path"),
        )


class TestUnique(unittest.TestCase):
    def test_preserves_order_deduplicates(self):
        self.assertEqual(probe.unique([3, 1, 2, 1, 3]), [3, 1, 2])

    def test_empty(self):
        self.assertEqual(probe.unique([]), [])


class TestExtractPlayerEmbeds(unittest.TestCase):
    def test_extracts_embed_pair(self):
        html = (
            '<iframe src="https://www.npr.org/player/embed/129451895/129454071">'
        )
        embeds = probe.extract_player_embeds(html)
        self.assertIn(
            "https://www.npr.org/player/embed/129451895/129454071", embeds
        )

    def test_deduplicates(self):
        html = (
            "https://www.npr.org/player/embed/111/222 "
            "https://www.npr.org/player/embed/111/222"
        )
        self.assertEqual(len(probe.extract_player_embeds(html)), 1)

    def test_no_false_positives(self):
        html = "https://www.npr.org/sections/theindicator/2018/07/11"
        self.assertEqual(probe.extract_player_embeds(html), [])


class TestExtractNprStoryUrls(unittest.TestCase):
    def test_extracts_dated_story_url(self):
        html = "https://www.npr.org/2018/07/11/629451895/fed-accounts-for-all"
        urls = probe.extract_npr_story_urls(html)
        self.assertTrue(
            any("629451895" in u for u in urls),
            f"Expected story URL not found in {urls}",
        )

    def test_extracts_transcript_url(self):
        html = (
            "https://www.npr.org/templates/transcript/"
            "transcript.php?storyId=129451895"
        )
        urls = probe.extract_npr_story_urls(html)
        self.assertTrue(any("transcript" in u for u in urls))


class TestExtractNumericIds(unittest.TestCase):
    def test_finds_seven_plus_digit_ids(self):
        ids = probe.extract_numeric_ids("storyId=129451895 audio=129454071")
        self.assertIn("129451895", ids)
        self.assertIn("129454071", ids)

    def test_short_numbers_ignored(self):
        ids = probe.extract_numeric_ids("width=640 height=480")
        self.assertEqual(ids, [])


class TestExtractAudioUrls(unittest.TestCase):
    def test_ondemand_extracted(self):
        html = (
            "https://ondemand.npr.org/anon.npr-mp3/npr/atc/"
            "2010/08/20100826_atc_02.mp3?d=310&e=129451895"
        )
        urls = probe.extract_audio_urls(html)
        self.assertTrue(any("ondemand.npr.org" in u for u in urls))

    def test_bare_mp3_extracted(self):
        html = "https://example.com/audio/clip.mp3"
        urls = probe.extract_audio_urls(html)
        self.assertTrue(any("clip.mp3" in u for u in urls))


class TestDateSlug(unittest.TestCase):
    def test_basic_slug(self):
        slug = probe._date_slug("Fed Accounts For All!")
        self.assertRegex(slug, r'^[a-z0-9\-]+$')
        self.assertNotIn(" ", slug)

    def test_slug_truncated(self):
        long_title = "A" * 200
        self.assertLessEqual(len(probe._date_slug(long_title)), 60)

    def test_ampersand_replaced(self):
        slug = probe._date_slug("Saudi Arabia & The Paradox of Plenty")
        self.assertNotIn("&", slug)


class TestValidateAudioCandidatePreFlight(unittest.TestCase):
    """Test the validation rules that do not require network access."""

    def test_generic_stream_rejected(self):
        result = probe.validate_audio_candidate(
            "https://playerservices.streamtheworld.com/api/livestream",
            "2018-07-11",
        )
        self.assertEqual(result["validation_status"], "rejected_generic_stream")
        self.assertFalse(result["valid_npr_indicator_audio"])

    def test_non_npr_url_rejected(self):
        result = probe.validate_audio_candidate(
            "https://example.com/audio.mp3", "2018-07-11"
        )
        self.assertEqual(result["validation_status"], "rejected_non_npr")
        self.assertFalse(result["valid_npr_indicator_audio"])


class TestBuildEpisodeOutputFilename(unittest.TestCase):
    def test_filename_format(self):
        ep = {"reference_date": "2018-07-11"}
        self.assertEqual(
            probe.build_episode_output_filename(ep),
            "top3_prospect_2018-07-11_diag.json",
        )


class TestRankedReportIntegration(unittest.TestCase):
    """
    Load the checked-in ranked report and verify the top-3 entries
    are structured as expected by the investigation script.
    """

    RANKED_REPORT = (
        REPO_ROOT / "indicator_identity_audio_unresolved_ranked_report.json"
    )

    def setUp(self):
        with open(self.RANKED_REPORT, encoding="utf-8") as fh:
            self.report = json.load(fh)
        self.episodes = self.report.get("episodes", [])
        self.top3 = [e for e in self.episodes if e.get("rank") in {1, 2, 3}]

    def test_top3_present(self):
        self.assertEqual(len(self.top3), 3)

    def test_ranks_are_1_2_3(self):
        ranks = {e["rank"] for e in self.top3}
        self.assertEqual(ranks, {1, 2, 3})

    def test_top3_dates(self):
        dates = {e["reference_date"] for e in self.top3}
        self.assertIn("2018-07-11", dates)
        self.assertIn("2018-08-10", dates)
        self.assertIn("2018-09-24", dates)

    def test_top3_have_required_fields(self):
        required = {
            "rank", "reference_date", "reference_title",
            "identity_confidence", "final_classification",
            "npr_ids_found",
        }
        for ep in self.top3:
            for field in required:
                self.assertIn(
                    field, ep,
                    f"Field '{field}' missing from rank {ep.get('rank')} entry",
                )

    def test_top3_all_unresolved(self):
        for ep in self.top3:
            self.assertEqual(
                ep["final_classification"],
                "identity_found_but_audio_unresolved",
                f"Rank {ep['rank']} should be identity_found_but_audio_unresolved",
            )

    def test_rank1_player_url_present(self):
        rank1 = next(e for e in self.top3 if e["rank"] == 1)
        self.assertTrue(
            any("player/embed" in u for u in rank1.get("player_urls", [])),
            "Rank 1 should have at least one player embed URL",
        )

    def test_rejected_ids_have_reasons(self):
        for ep in self.top3:
            for item in ep["npr_ids_found"].get("rejected_or_unverified_ids", []):
                self.assertIn("id", item)
                self.assertIn("reason", item)
                self.assertTrue(item["reason"], "Rejection reason must be non-empty")


if __name__ == "__main__":
    unittest.main()
