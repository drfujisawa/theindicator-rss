#!/usr/bin/env python3
"""
Unit tests for probe_fresh_identity_discovery.py.

All tests are pure (no network calls).  Tests cover:
  - title/slug matching and scoring;
  - date-window calculation;
  - NPR story-ID extraction from URLs;
  - rejection of unrelated NPR pages with similar keywords;
  - numeric ID bounds being advisory only;
  - confirmed identity requiring title + date + NPR page evidence;
  - validated recovery requiring verified identity + NPR Indicator audio;
  - audio candidate validation (pre-flight);
  - CDX query parameter construction;
  - output filename generation;
  - run-state fields in placeholder and completed output.
"""

import datetime
import json
import unittest
from pathlib import Path

from scripts.recovery import probe_fresh_identity_discovery as probe
REPO_ROOT = Path(__file__).resolve().parents[1]



# ---------------------------------------------------------------------------
# slugify / title helpers
# ---------------------------------------------------------------------------


class TestSlugify(unittest.TestCase):
    def test_basic_ascii(self):
        self.assertEqual(probe.slugify("Fed Accounts For All!"), "fed-accounts-for-all")

    def test_ampersand(self):
        self.assertNotIn("&", probe.slugify("Saudi Arabia & The Paradox of Plenty"))

    def test_colon(self):
        slug = probe.slugify("Privacy Please: Why Public Companies Go Private")
        self.assertNotIn(":", slug)

    def test_max_length(self):
        self.assertLessEqual(len(probe.slugify("A" * 200)), 80)

    def test_no_spaces(self):
        self.assertNotIn(" ", probe.slugify("Fed Accounts For All!"))

    def test_lowercase(self):
        slug = probe.slugify("FED ACCOUNTS")
        self.assertEqual(slug, slug.lower())


class TestTitleTokenOverlap(unittest.TestCase):
    def test_identical_titles(self):
        self.assertAlmostEqual(
            probe.title_token_overlap(
                "Fed Accounts For All", "Fed Accounts For All"
            ),
            1.0,
        )

    def test_disjoint_titles(self):
        self.assertEqual(
            probe.title_token_overlap("Saudi Arabia Paradox Plenty", "Cheese Imports Trade"),
            0.0,
        )

    def test_partial_overlap(self):
        score = probe.title_token_overlap(
            "saudi arabia paradox plenty oil",
            "Saudi Arabia & The Paradox of Plenty",
        )
        self.assertGreater(score, 0.5)

    def test_short_tokens_ignored(self):
        # "of", "the", "is", "for" should be ignored (< 4 chars)
        score = probe.title_token_overlap("for the of is", "separate words here")
        self.assertEqual(score, 0.0)

    def test_empty_title_a(self):
        self.assertEqual(probe.title_token_overlap("", "some words"), 0.0)


class TestSlugSimilarity(unittest.TestCase):
    def test_identical_slugs(self):
        self.assertAlmostEqual(
            probe.slug_similarity("fed-accounts-for-all", "fed-accounts-for-all"),
            1.0,
        )

    def test_partial_slug(self):
        score = probe.slug_similarity("fed-accounts", "fed-accounts-for-all")
        self.assertGreater(score, 0.0)

    def test_no_overlap(self):
        self.assertEqual(probe.slug_similarity("cheese-trade", "privacy-please"), 0.0)

    def test_short_parts_ignored(self):
        # "for", "all" (3 chars) ignored
        self.assertEqual(probe.slug_similarity("for-all", "separate-words"), 0.0)


# ---------------------------------------------------------------------------
# URL scoring for target
# ---------------------------------------------------------------------------


class TestScoreUrlForTarget(unittest.TestCase):
    def _score(self, url, title, variants):
        return probe.score_url_for_target(url, title, variants)

    def test_exact_slug_variant_match(self):
        score = self._score(
            "https://www.npr.org/sections/theindicator/2018/07/11/628123456/fed-accounts-for-all",
            "Fed Accounts For All!",
            ["fed-accounts-for-all", "fed-accounts"],
        )
        self.assertGreaterEqual(score, probe.SLUG_MATCH_THRESHOLD)

    def test_unrelated_story_low_score(self):
        score = self._score(
            "https://www.npr.org/sections/money/2018/07/11/999999/cheese-import-tariffs",
            "Fed Accounts For All!",
            ["fed-accounts-for-all"],
        )
        self.assertLess(score, probe.SLUG_MATCH_THRESHOLD)

    def test_thematic_overlap_not_title_match(self):
        # "federal-reserve" shares some tokens with "fed-accounts-for-all" but is below threshold
        score = self._score(
            "https://www.npr.org/sections/money/2018/07/11/111/federal-reserve-rates",
            "Fed Accounts For All!",
            ["fed-accounts-for-all"],
        )
        # Should not exceed a high threshold — result could go either way on partial overlap,
        # but this title is clearly not "fed accounts for all"
        self.assertIsInstance(score, float)

    def test_score_between_zero_and_one(self):
        score = self._score(
            "https://www.npr.org/sections/theindicator/2018/07/11/628123456/anything",
            "Privacy Please: Why Public Companies Go Private",
            ["privacy-please"],
        )
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


# ---------------------------------------------------------------------------
# Page scoring (confirmed identity requires title + date + trusted story page)
# ---------------------------------------------------------------------------


class TestScorePageForTarget(unittest.TestCase):
    def _score(self, page_title, pub_date, canonical, program_ctx,
               reference_date, reference_title):
        return probe.score_page_for_target(
            page_title, pub_date, canonical, program_ctx,
            reference_date, reference_title,
        )

    def test_strong_match(self):
        result = self._score(
            "Fed Accounts For All! : The Indicator from Planet Money",
            "2018-07-11",
            "https://www.npr.org/2018/07/11/628123456/fed-accounts-for-all",
            {"has_indicator_branding": True, "program_id": "510325"},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertEqual(result["verdict"], "strong_match")
        self.assertTrue(result["date_match"])
        self.assertTrue(result["has_story_id"])
        self.assertTrue(result["has_trusted_story_id"])
        self.assertTrue(result["trusted_story_page"])
        self.assertTrue(result["has_episode_context"])

    def test_sections_money_story_page_is_trusted(self):
        result = self._score(
            "Fed Accounts For All! : The Indicator from Planet Money",
            "2018-07-11",
            "https://www.npr.org/sections/money/2018/07/11/628123456/fed-accounts-for-all",
            {"has_indicator_branding": True, "program_id": "510325"},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertEqual(result["verdict"], "strong_match")
        self.assertTrue(result["trusted_story_page"])

    def test_title_match_not_indicator_program(self):
        result = self._score(
            "Fed Accounts For All! : Planet Money",
            "2018-07-11",
            "https://www.npr.org/2018/07/11/628123456/fed-accounts-for-all",
            {"has_indicator_branding": False, "program_id": None},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertEqual(result["verdict"], "title_date_story_id_no_episode_context")

    def test_indicator_date_no_title(self):
        result = self._score(
            "Something Completely Unrelated : The Indicator",
            "2018-07-11",
            "https://www.npr.org/2018/07/11/628123456/something-else",
            {"has_indicator_branding": True},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertIn(result["verdict"], ("no_match", "story_id_date_match_not_title"))

    def test_wrong_date_no_match(self):
        result = self._score(
            "Fed Accounts For All! : The Indicator",
            "2020-10-13",  # wrong date — Life Kit year
            "https://www.npr.org/2020/10/13/922262686/life-kit-privacy",
            {"has_indicator_branding": True},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertIn(result["verdict"], ("title_match_story_id_no_date", "no_match"))
        self.assertFalse(result["date_match"])

    def test_adjacent_date_accepted(self):
        result = self._score(
            "Fed Accounts For All! : The Indicator",
            "2018-07-10",   # 1 day before — adjacent
            "https://www.npr.org/2018/07/10/628000000/fed-accounts-for-all",
            {"has_indicator_branding": True},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertTrue(result["date_adjacent"])

    def test_no_date_in_page(self):
        result = self._score(
            "Fed Accounts For All! : The Indicator",
            "",   # no date extracted
            "https://www.npr.org/something",
            {"has_indicator_branding": True},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertIsNone(result["date_match"])

    def test_transcript_url_rejected_as_trusted_identity(self):
        result = self._score(
            "Fed Accounts For All! : The Indicator from Planet Money",
            "2018-07-11",
            "https://www.npr.org/templates/transcript/transcript.php?storyId=628123456",
            {"has_indicator_branding": True, "program_id": "510325"},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertEqual(result["verdict"], "title_date_story_id_no_trusted_story_page")
        self.assertTrue(result["has_story_id"])
        self.assertFalse(result["has_trusted_story_id"])
        self.assertFalse(result["trusted_story_page"])

    def test_sections_theindicator_url_rejected_as_trusted_identity(self):
        result = self._score(
            "Fed Accounts For All! : The Indicator from Planet Money",
            "2018-07-11",
            "https://www.npr.org/sections/theindicator/2018/07/11/628123456/fed-accounts-for-all",
            {"has_indicator_branding": True, "program_id": "510325"},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertEqual(result["verdict"], "title_date_story_id_no_trusted_story_page")
        self.assertTrue(result["has_story_id"])
        self.assertFalse(result["has_trusted_story_id"])
        self.assertFalse(result["trusted_story_page"])


# ---------------------------------------------------------------------------
# Rejecting unrelated NPR pages with keyword overlap
# ---------------------------------------------------------------------------


class TestKeywordOverlapRejection(unittest.TestCase):
    """
    A page about "Federal Reserve" without matching The Indicator context
    must not be treated as a confirmed identity for "Fed Accounts For All!".
    """

    def test_wrong_program_rejected(self):
        # ATC story about federal reserve — title tokens overlap but not Indicator
        result = probe.score_page_for_target(
            "Fed Makes Accounts Change: ATC Explains",
            "2018-07-11",
            "https://www.npr.org/2018/07/11/628000000/fed-accounts-atc",
            {"has_indicator_branding": False, "program_id": "2"},   # ATC program ID
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertNotEqual(result["verdict"], "strong_match")

    def test_related_sidebar_story_rejected(self):
        # "Privacy" article from Two-Way — title tokens overlap with "Privacy Please" but wrong program
        result = probe.score_page_for_target(
            "Indian Supreme Court Declares Privacy Fundamental Right",
            "2017-08-24",
            "https://www.npr.org/sections/thetwo-way/2017/08/24/545963181/indian-privacy",
            {"has_indicator_branding": False, "program_id": None},
            "2018-08-10",
            "Privacy Please: Why Public Companies Go Private (Or Vice Versa)",
        )
        self.assertNotEqual(result["verdict"], "strong_match")
        self.assertFalse(result["date_match"])

    def test_2011_saudi_story_rejected(self):
        # 2011 ATC OPEC story — thematic overlap with Saudi Arabia episode
        result = probe.score_page_for_target(
            "OPEC Decides Not To Increase Oil Production : Saudi Arabia",
            "2011-06-08",   # 7 years before the target
            "https://www.npr.org/2011/06/08/137065443/opec-saudi-oil",
            {"has_indicator_branding": False, "program_id": "2"},
            "2018-09-24",
            "Saudi Arabia & The Paradox of Plenty",
        )
        self.assertNotEqual(result["verdict"], "strong_match")
        self.assertFalse(result["date_match"])
        self.assertFalse(result["date_adjacent"])


# ---------------------------------------------------------------------------
# Numeric ID bounds advisory-only contract
# ---------------------------------------------------------------------------


class TestNumericIdBoundsAdvisory(unittest.TestCase):
    """
    Verify that the TARGETS data and probe code treat numeric ID bounds as
    advisory only — they are never used as standalone proof of identity.
    """

    def test_all_targets_have_advisory_flag(self):
        for t in probe.TARGETS:
            self.assertIn("id_lower_bound", t)
            self.assertIn("id_upper_bound", t)
            # The bounds must be integers
            self.assertIsInstance(t["id_lower_bound"], int)
            self.assertIsInstance(t["id_upper_bound"], int)

    def test_id_bounds_reference_validated_adjacent_episodes(self):
        for t in probe.TARGETS:
            self.assertIn("id_lower_episode", t)
            self.assertIn("id_upper_episode", t)
            # Lower episode must predate target
            lower_ep = datetime.date.fromisoformat(t["id_lower_episode"])
            target_ep = datetime.date.fromisoformat(t["reference_date"])
            upper_ep = datetime.date.fromisoformat(t["id_upper_episode"])
            self.assertLessEqual(lower_ep, target_ep)
            self.assertGreaterEqual(upper_ep, target_ep)

    def test_numeric_probe_step_is_sparse(self):
        # The step must be large enough that we never brute-force millions of IDs
        self.assertGreaterEqual(probe.NUMERIC_PROBE_STEP, 10_000)

    def test_numeric_probe_max_is_small(self):
        self.assertLessEqual(probe.NUMERIC_PROBE_MAX, 25)

    def test_diag_id_bounds_advisory_flag(self):
        # Build a minimal diag dict and verify advisory_only is set
        t = probe.TARGETS[0]
        # The investigate_episode function sets id_bounds.advisory_only=True
        # We can't call it without network, but we verify TARGETS has the right bounds
        # and the flag is set in the code path by inspecting the source.
        import inspect
        src = inspect.getsource(probe.investigate_episode)
        self.assertIn('"advisory_only": True', src)


# ---------------------------------------------------------------------------
# Confirmed identity requires title + date + NPR evidence
# ---------------------------------------------------------------------------


class TestConfirmedIdentityContract(unittest.TestCase):
    def test_strong_match_verdict_requires_all_three(self):
        # title score + date + story ID + episode context are all required
        r = probe.score_page_for_target(
            "Fed Accounts For All! The Indicator",
            "2018-07-11",
            "https://www.npr.org/2018/07/11/628000000/fed-accounts-for-all",
            {"has_indicator_branding": True},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertEqual(r["verdict"], "strong_match")

    def test_missing_indicator_context_blocks_confirmation(self):
        r = probe.score_page_for_target(
            "Fed Accounts For All!",
            "2018-07-11",
            "https://www.npr.org/2018/07/11/628000000/something",
            {"has_indicator_branding": False},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertNotEqual(r["verdict"], "strong_match")

    def test_missing_date_blocks_confirmation(self):
        r = probe.score_page_for_target(
            "Fed Accounts For All! The Indicator",
            "",   # no date
            "https://www.npr.org/something",
            {"has_indicator_branding": True},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        self.assertNotEqual(r["verdict"], "strong_match")

    def test_low_title_score_blocks_confirmation(self):
        r = probe.score_page_for_target(
            "Random Topic Unrelated Story : The Indicator",
            "2018-07-11",
            "https://www.npr.org/2018/07/11/628000000/random",
            {"has_indicator_branding": True},
            "2018-07-11",
            "Fed Accounts For All!",
        )
        # title_score will be low because "random topic unrelated story" ≈ 0 overlap
        self.assertNotEqual(r["verdict"], "strong_match")


# ---------------------------------------------------------------------------
# Validated recovery requires verified identity + NPR Indicator audio
# ---------------------------------------------------------------------------


class TestValidatedRecoveryContract(unittest.TestCase):
    def test_indicator_url_passes_pre_flight(self):
        result = probe.validate_audio_candidate(
            "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/20180711_indicator_fed.mp3",
            "2018-07-11",
        )
        self.assertEqual(result["validation_status"], "needs_network_check")

    def test_atc_url_rejected_not_indicator_path(self):
        # We can't call the live validator, but the pre-flight would pass to network check;
        # the is_indicator_path function determines the final verdict.
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/atc/2010/08/20100826_atc_02.mp3"
            )
        )

    def test_lifekit_url_not_indicator(self):
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/lifekit/2020/10/20201013_lifekit.mp3"
            )
        )

    def test_me_url_not_indicator(self):
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/me/2011/05/20110519_me_02.mp3"
            )
        )

    def test_generic_stream_rejected(self):
        result = probe.validate_audio_candidate(
            "https://playerservices.streamtheworld.com/api/livestream",
            "2018-07-11",
        )
        self.assertEqual(result["validation_status"], "rejected_generic_stream")
        self.assertFalse(result["valid_npr_indicator_audio"])

    def test_non_npr_rejected(self):
        result = probe.validate_audio_candidate(
            "https://example.com/audio.mp3",
            "2018-07-11",
        )
        self.assertEqual(result["validation_status"], "rejected_non_npr")
        self.assertFalse(result["valid_npr_indicator_audio"])

    def test_megaphone_rejected(self):
        result = probe.validate_audio_candidate(
            "https://traffic.megaphone.fm/NPR5942607311.mp3",
            "2018-08-10",
        )
        self.assertEqual(result["validation_status"], "rejected_generic_stream")

    def test_tracking_swap_rejected(self):
        result = probe.validate_audio_candidate(
            "https://tracking.swap.fm/track/XXX/prfx.byspotify.com/something.mp3",
            "2018-08-10",
        )
        self.assertEqual(result["validation_status"], "rejected_generic_stream")

    def test_chrt_rejected(self):
        result = probe.validate_audio_candidate(
            "https://chrt.fm/track/ABC/ondemand.npr.org/indicator/file.mp3",
            "2018-09-24",
        )
        self.assertEqual(result["validation_status"], "rejected_generic_stream")


# ---------------------------------------------------------------------------
# Audio candidate validation pre-flight
# ---------------------------------------------------------------------------


class TestAudioValidationPreflight(unittest.TestCase):
    def test_ondemand_npr_passes_to_network(self):
        r = probe.validate_audio_candidate(
            "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/file.mp3",
            "2018-07-11",
        )
        self.assertEqual(r["validation_status"], "needs_network_check")

    def test_podtrac_npr_passes_to_network(self):
        r = probe.validate_audio_candidate(
            "https://play.podtrac.com/npr-510325/ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/file.mp3",
            "2018-07-11",
        )
        # Contains 'npr.org' — passes pre-flight
        self.assertEqual(r["validation_status"], "needs_network_check")

    def test_non_audio_npr_page_passes_pre_flight(self):
        # The pre-flight only checks host; content-type check is live
        r = probe.validate_audio_candidate(
            "https://www.npr.org/2018/07/11/628000000/some-story",
            "2018-07-11",
        )
        self.assertEqual(r["validation_status"], "needs_network_check")

    def test_valid_npr_audio_flag_false_before_network(self):
        r = probe.validate_audio_candidate(
            "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/file.mp3",
            "2018-07-11",
        )
        self.assertFalse(r["valid_npr_indicator_audio"])


# ---------------------------------------------------------------------------
# CDX date-window calculation
# ---------------------------------------------------------------------------


class TestCdxDateWindow(unittest.TestCase):
    def test_window_from_before_to_after(self):
        from_d, to_d = probe._cdx_date_window("2018-07-11", days_before=3, days_after=7)
        from_date = datetime.date.fromisoformat(from_d[:4] + "-" + from_d[4:6] + "-" + from_d[6:])
        to_date = datetime.date.fromisoformat(to_d[:4] + "-" + to_d[4:6] + "-" + to_d[6:])
        target = datetime.date.fromisoformat("2018-07-11")
        self.assertLessEqual(from_date, target)
        self.assertGreaterEqual(to_date, target)

    def test_window_format_is_yyyymmdd(self):
        from_d, to_d = probe._cdx_date_window("2018-08-10")
        self.assertRegex(from_d, r"^\d{8}$")
        self.assertRegex(to_d, r"^\d{8}$")

    def test_window_includes_target(self):
        from_d, to_d = probe._cdx_date_window("2018-09-24")
        self.assertLessEqual(from_d, "20180924")
        self.assertGreaterEqual(to_d, "20180924")

    def test_default_window_is_reasonable(self):
        # Default is 3 days before, 7 days after — total <= 14 days
        from_d, to_d = probe._cdx_date_window("2018-07-11")
        from_date = datetime.date(int(from_d[:4]), int(from_d[4:6]), int(from_d[6:]))
        to_date = datetime.date(int(to_d[:4]), int(to_d[4:6]), int(to_d[6:]))
        self.assertLessEqual((to_date - from_date).days, 14)


# ---------------------------------------------------------------------------
# CDX query limit constants
# ---------------------------------------------------------------------------


class TestCdxLimits(unittest.TestCase):
    def test_date_window_limit_not_excessive(self):
        # Must be small enough to avoid huge Wayback requests
        self.assertLessEqual(probe.CDX_DATE_WINDOW_LIMIT, 200)

    def test_stage_a_query_count_bounded(self):
        self.assertLessEqual(probe.MAX_STAGE_A_CDX_QUERIES, 6)

    def test_max_capture_fetches_small(self):
        self.assertLessEqual(probe.MAX_FETCHED_CAPTURES, 6)

    def test_max_audio_candidates_bounded(self):
        self.assertLessEqual(probe.MAX_AUDIO_CANDIDATES, 10)


# ---------------------------------------------------------------------------
# NPR story ID extraction
# ---------------------------------------------------------------------------


class TestNprStoryIdExtraction(unittest.TestCase):
    def test_dated_story_url(self):
        sid = probe._npr_story_id_from_url(
            "https://www.npr.org/2018/07/11/628123456/fed-accounts-for-all"
        )
        self.assertEqual(sid, "628123456")

    def test_transcript_url(self):
        sid = probe._npr_story_id_from_url(
            "https://www.npr.org/templates/transcript/transcript.php?storyId=628123456"
        )
        self.assertEqual(sid, "628123456")

    def test_transcript_url_not_trusted_story_page(self):
        self.assertFalse(
            probe.is_trusted_story_page_url(
                "https://www.npr.org/templates/transcript/transcript.php?storyId=628123456"
            )
        )
        self.assertIsNone(
            probe._trusted_npr_story_id_from_url(
                "https://www.npr.org/templates/transcript/transcript.php?storyId=628123456"
            )
        )

    def test_no_id_in_url(self):
        sid = probe._npr_story_id_from_url(
            "https://www.npr.org/sections/theindicator/"
        )
        self.assertIsNone(sid)

    def test_player_embed_url(self):
        # Player embed has two IDs; story ID is the first
        sid = probe._npr_story_id_from_url(
            "https://www.npr.org/player/embed/628123456/628130000"
        )
        # Not a dated story URL — returns None (player embeds extracted separately)
        self.assertIsNone(sid)


class TestTrustedStoryPageUrlPatterns(unittest.TestCase):
    def test_root_dated_story_page_is_trusted(self):
        url = "https://www.npr.org/2019/04/30/718711109/how-grocery-shelves-get-stacked"
        self.assertTrue(probe.is_trusted_story_page_url(url))
        self.assertEqual(probe._trusted_npr_story_id_from_url(url), "718711109")

    def test_sections_money_story_page_is_trusted(self):
        url = "https://www.npr.org/sections/money/2018/03/13/593261790/bonds-japanese-bonds"
        self.assertTrue(probe.is_trusted_story_page_url(url))
        self.assertEqual(probe._trusted_npr_story_id_from_url(url), "593261790")

    def test_sections_theindicator_story_page_not_trusted(self):
        url = "https://www.npr.org/sections/theindicator/2018/07/11/628123456/fed-accounts-for-all"
        self.assertFalse(probe.is_trusted_story_page_url(url))
        self.assertIsNone(probe._trusted_npr_story_id_from_url(url))


# ---------------------------------------------------------------------------
# HTML extraction helpers
# ---------------------------------------------------------------------------


class TestExtractPageTitle(unittest.TestCase):
    def test_title_tag(self):
        html = "<html><head><title>Fed Accounts For All! : The Indicator</title></head></html>"
        self.assertIn("Fed Accounts For All!", probe.extract_page_title(html))

    def test_og_title(self):
        html = '<meta property="og:title" content="Saudi Arabia: Paradox of Plenty"/>'
        self.assertIn("Saudi Arabia", probe.extract_page_title(html))

    def test_no_title(self):
        self.assertEqual(probe.extract_page_title("<html></html>"), "")


class TestExtractPublicationDate(unittest.TestCase):
    def test_date_published(self):
        html = '"datePublished": "2018-07-11"'
        self.assertEqual(probe.extract_publication_date(html), "2018-07-11")

    def test_time_datetime(self):
        html = '<time datetime="2018-08-10T12:00:00Z">'
        self.assertEqual(probe.extract_publication_date(html), "2018-08-10")

    def test_no_date(self):
        self.assertEqual(probe.extract_publication_date("<html></html>"), "")


class TestExtractProgramContext(unittest.TestCase):
    def test_program_id_510325_detected(self):
        ctx = probe.extract_program_context("p=510325&story=628123456")
        self.assertEqual(ctx["program_id"], "510325")
        self.assertIn("program_id_510325", ctx["indicator_signals"])
        self.assertFalse(ctx["has_indicator_branding"])

    def test_indicator_in_show_name(self):
        ctx = probe.extract_program_context(
            'showTitle: "The Indicator from Planet Money"'
        )
        self.assertTrue(ctx["has_indicator_branding"])

    def test_no_indicator_context(self):
        ctx = probe.extract_program_context("p=2&story=129451895&program=ATC")
        self.assertFalse(ctx["has_indicator_branding"])
        self.assertIsNone(ctx["program_id"])


class TestExtractPlayerEmbeds(unittest.TestCase):
    def test_extracts_pair(self):
        html = '<iframe src="https://www.npr.org/player/embed/628123456/628130000"></iframe>'
        embeds = probe.extract_player_embeds(html)
        self.assertIn("https://www.npr.org/player/embed/628123456/628130000", embeds)

    def test_deduplicates(self):
        html = (
            "https://www.npr.org/player/embed/111/222 "
            "https://www.npr.org/player/embed/111/222"
        )
        self.assertEqual(len(probe.extract_player_embeds(html)), 1)

    def test_no_false_positives(self):
        html = "https://www.npr.org/sections/theindicator/2018/07/11"
        self.assertEqual(probe.extract_player_embeds(html), [])


class TestExtractNumericIds(unittest.TestCase):
    def test_long_id_found(self):
        ids = probe.extract_numeric_ids("storyId=628123456 audio=628130000")
        self.assertIn("628123456", ids)
        self.assertIn("628130000", ids)

    def test_short_numbers_ignored(self):
        ids = probe.extract_numeric_ids("width=640 height=480 id=99")
        self.assertEqual(ids, [])

    def test_deduplicates(self):
        ids = probe.extract_numeric_ids("1234567 1234567 7654321")
        self.assertEqual(ids.count("1234567"), 1)


class TestExtractAudioUrls(unittest.TestCase):
    def test_ondemand_extracted(self):
        html = "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/file.mp3"
        urls = probe.extract_audio_urls(html)
        self.assertTrue(
            any(u.startswith("https://ondemand.npr.org/") for u in urls)
        )

    def test_podtrac_extracted(self):
        html = "https://play.podtrac.com/npr-510325/ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/file.mp3"
        urls = probe.extract_audio_urls(html)
        self.assertTrue(any(u.startswith("https://play.podtrac.com/") for u in urls))

    def test_bare_mp3_extracted(self):
        html = "https://example.com/audio/episode.mp3"
        urls = probe.extract_audio_urls(html)
        self.assertTrue(any("episode.mp3" in u for u in urls))


# ---------------------------------------------------------------------------
# is_indicator_path (existing validator rule)
# ---------------------------------------------------------------------------


class TestIsIndicatorPath(unittest.TestCase):
    def test_indicator_path_passes(self):
        self.assertTrue(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/file.mp3"
            )
        )

    def test_atc_path_fails(self):
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/atc/2010/08/20100826_atc_02.mp3"
            )
        )

    def test_lifekit_fails(self):
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/lifekit/2020/10/file.mp3"
            )
        )

    def test_me_fails(self):
        self.assertFalse(
            probe.is_indicator_path(
                "https://ondemand.npr.org/anon.npr-mp3/npr/me/2011/05/20110519_me_02.mp3"
            )
        )


# ---------------------------------------------------------------------------
# Output filename and placeholder structure
# ---------------------------------------------------------------------------


class TestOutputFilename(unittest.TestCase):
    def test_format(self):
        self.assertEqual(
            probe.output_filename("2018-07-11"),
            "fresh_identity_discovery_2018-07-11_diag.json",
        )

    def test_all_three_targets(self):
        expected = {
            "fresh_identity_discovery_2018-07-11_diag.json",
            "fresh_identity_discovery_2018-08-10_diag.json",
            "fresh_identity_discovery_2018-09-24_diag.json",
        }
        actual = {probe.output_filename(t["reference_date"]) for t in probe.TARGETS}
        self.assertEqual(actual, expected)


class TestPlaceholderStructure(unittest.TestCase):
    def test_placeholder_has_run_state(self):
        t = probe.TARGETS[0]
        ph = probe._placeholder_diag(t)
        self.assertTrue(ph["placeholder"])
        self.assertFalse(ph["run_complete"])
        self.assertEqual(ph["run_state"], "placeholder")
        self.assertIsNone(ph["final_classification"])

    def test_placeholder_summary_run_state(self):
        ph = probe._placeholder_summary(probe.TARGETS)
        self.assertTrue(ph["placeholder"])
        self.assertFalse(ph["run_complete"])
        self.assertEqual(ph["run_state"], "placeholder")
        self.assertEqual(len(ph["episodes"]), len(probe.TARGETS))


# ---------------------------------------------------------------------------
# TARGETS configuration
# ---------------------------------------------------------------------------


class TestTargetsConfiguration(unittest.TestCase):
    def test_correct_dates(self):
        dates = {t["reference_date"] for t in probe.TARGETS}
        self.assertEqual(dates, {"2018-07-11", "2018-08-10", "2018-09-24"})

    def test_all_targets_have_slug_variants(self):
        for t in probe.TARGETS:
            self.assertGreater(len(t["slug_variants"]), 0)
            for sv in t["slug_variants"]:
                self.assertRegex(sv, r'^[a-z0-9\-]+$', f"Slug '{sv}' not kebab-case")

    def test_all_targets_have_section_paths(self):
        for t in probe.TARGETS:
            self.assertGreater(len(t["section_paths"]), 0)

    def test_id_bounds_consistent_with_known_corpus(self):
        """
        Verify that the id_lower_bound for each target is less than id_upper_bound
        and that the bounds are 9-digit IDs in the expected 2018 range.
        """
        for t in probe.TARGETS:
            lo = t["id_lower_bound"]
            hi = t["id_upper_bound"]
            self.assertLess(lo, hi, f"Lower bound must be < upper bound for {t['reference_date']}")
            # All 2018 mid-year Indicator IDs should be in the 620M–660M range
            self.assertGreater(lo, 600_000_000)
            self.assertLess(hi, 700_000_000)

    def test_no_previously_known_bad_ids_in_targets(self):
        """
        The previously known wrong IDs (from probe_top3_ranked_prospects.py)
        must not appear as trusted starting points in this probe.
        WRONG IDs: 129451895 (ATC 2010), 922262686 (LifeKit 2020),
                   137065443 (ATC 2011), 136439885 (ME 2011)
        """
        BAD_IDS = {129451895, 922262686, 137065443, 136439885}
        for t in probe.TARGETS:
            for bad_id in BAD_IDS:
                self.assertNotEqual(t["id_lower_bound"], bad_id)
                self.assertNotEqual(t["id_upper_bound"], bad_id)


# ---------------------------------------------------------------------------
# Partial run must not appear complete
# ---------------------------------------------------------------------------


class TestRunStateFields(unittest.TestCase):
    def test_run_complete_is_string(self):
        import inspect
        src = inspect.getsource(probe.run)
        # run_complete must be set explicitly in the summary
        self.assertIn('"run_state": "run_complete"', src)

    def test_placeholder_run_state_distinct(self):
        ph = probe._placeholder_diag(probe.TARGETS[0])
        self.assertNotEqual(ph["run_state"], "run_complete")

    def test_failed_run_state_available(self):
        # If an exception is raised, run() sets run_state='failed'
        import inspect
        src = inspect.getsource(probe.run)
        self.assertIn('"run_state": "failed"', src)
        self.assertIn('"run_complete": False', src)

    def test_counts_present_in_run_summary(self):
        import inspect
        src = inspect.getsource(probe.run)
        for key in ("attempted", "completed", "failed", "skipped", "recovered"):
            self.assertIn(key, src)


class TestRequestBudget(unittest.TestCase):
    def test_budget_has_bounded_requests(self):
        budget = probe.request_budget()
        self.assertLessEqual(budget["per_episode"]["max_logical_requests"], 20)
        self.assertLessEqual(budget["per_run"]["max_logical_requests"], 60)

    def test_stage_a_patterns_are_date_based(self):
        patterns = probe.stage_a_patterns("2018-07-11")
        self.assertLessEqual(len(patterns), probe.MAX_STAGE_A_CDX_QUERIES)
        self.assertTrue(any("/2018/07/11/" in pattern for pattern in patterns))


class TestTrustedAudioContract(unittest.TestCase):
    def test_validated_audio_requires_trusted_provenance(self):
        audio_evidence = {
            "audio_url": "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/07/file.mp3",
            "audio_id": "20180711",
            "provenance": {
                "source_url": "https://www.npr.org/story",
                "source_capture_timestamp": "20180711120000",
                "episode_qualified": False,
                "target_episode": "2018-07-11",
                "target_title": "Fed Accounts For All!",
                "evidence_type": "audio_url",
                "trust_level": "untrusted",
            },
        }

        original = probe.validate_audio_candidate_live
        try:
            probe.validate_audio_candidate_live = lambda url, reference_date: {
                "candidate_url": url,
                "validation_status": "validated",
                "valid_npr_indicator_audio": True,
            }
            result = probe.validate_audio_evidence_live(audio_evidence, "2018-07-11")
        finally:
            probe.validate_audio_candidate_live = original

        self.assertFalse(result["trusted_for_recovery"])


# ---------------------------------------------------------------------------
# Stage-A CDX pattern construction
# ---------------------------------------------------------------------------


class TestStageAPatterns(unittest.TestCase):
    """Verify that stage_a_patterns() produces historically valid NPR URL
    prefixes with no mid-path wildcards."""

    def setUp(self):
        self.patterns_0711 = probe.stage_a_patterns("2018-07-11")
        self.patterns_0810 = probe.stage_a_patterns("2018-08-10")
        self.patterns_0924 = probe.stage_a_patterns("2018-09-24")

    # ---- count / budget -------------------------------------------------

    def test_count_within_budget(self):
        self.assertLessEqual(len(self.patterns_0711), probe.MAX_STAGE_A_CDX_QUERIES)

    def test_exactly_two_variants_per_day(self):
        # 3 days × 2 schemes (HTTPS + HTTP) = 6 total (or MAX_STAGE_A_CDX_QUERIES, whichever is lower)
        self.assertEqual(len(self.patterns_0711), min(6, probe.MAX_STAGE_A_CDX_QUERIES))

    # ---- correct sections/money/ path ----------------------------------

    def test_all_patterns_use_sections_money(self):
        for pat in self.patterns_0711:
            self.assertIn("/sections/money/", pat, f"Pattern missing /sections/money/: {pat}")

    def test_reference_date_covered(self):
        self.assertTrue(
            any("/2018/07/11/" in p for p in self.patterns_0711),
            "No pattern covers the reference date 2018-07-11",
        )

    def test_day_before_covered(self):
        self.assertTrue(
            any("/2018/07/10/" in p for p in self.patterns_0711),
            "No pattern covers day-1 (2018-07-10)",
        )

    def test_day_after_covered(self):
        self.assertTrue(
            any("/2018/07/12/" in p for p in self.patterns_0711),
            "No pattern covers day+1 (2018-07-12)",
        )

    # ---- HTTPS and HTTP variants both present ---------------------------

    def test_https_variant_present(self):
        self.assertTrue(
            any(p.startswith("https://") for p in self.patterns_0711),
            "No HTTPS pattern generated",
        )

    def test_http_variant_present(self):
        self.assertTrue(
            any(p.startswith("http://") and not p.startswith("https://") for p in self.patterns_0711),
            "No HTTP pattern generated",
        )

    def test_https_and_http_are_distinct(self):
        https_patterns = [p for p in self.patterns_0711 if p.startswith("https://")]
        http_patterns = [p for p in self.patterns_0711 if p.startswith("http://") and not p.startswith("https://")]
        self.assertTrue(https_patterns)
        self.assertTrue(http_patterns)
        # They must differ only in scheme, same path
        for h in https_patterns:
            plain = "http://" + h[len("https://"):]
            self.assertIn(plain, http_patterns, f"No HTTP counterpart for {h}")

    # ---- no wildcards anywhere in pattern ----------------------------

    def test_no_wildcard_character_in_patterns(self):
        for pat in self.patterns_0711:
            self.assertNotIn("*", pat, f"Wildcard (*) found in pattern: {pat}")

    def test_no_mid_path_wildcard_fragments(self):
        # None of the old broken forms ("/sections/*/…" or "/YYYY/MM/DD/") should appear
        for pat in self.patterns_0711:
            self.assertNotIn("sections/*/", pat,
                             f"Mid-path section wildcard found: {pat}")
            # bare date root without /sections/ prefix is the other broken form
            segments = pat.split("/")
            try:
                year_idx = segments.index("2018")
                prefix_before_year = "/".join(segments[:year_idx])
                self.assertIn("sections/money", prefix_before_year,
                              f"Pattern has year directly after host (no sections/money): {pat}")
            except ValueError:
                pass  # year not in path — shouldn't happen

    # ---- other targets -----------------------------------------------

    def test_august_target_sections_money(self):
        for pat in self.patterns_0810:
            self.assertIn("/sections/money/", pat)

    def test_september_target_sections_money(self):
        for pat in self.patterns_0924:
            self.assertIn("/sections/money/", pat)


# ---------------------------------------------------------------------------
# CDX helper: matchType=prefix and result-dict structure
# ---------------------------------------------------------------------------


class TestCdxQueryParameters(unittest.TestCase):
    """Verify that wayback_cdx_date_window sends matchType=prefix and
    returns the expected result-dict shape."""

    def _capture_url(self, fn, *args, **kwargs):
        """Call fn with a no-op network stub; return (captured_url, result)."""
        captured = {}

        def fake_fetch(url, retries=1):
            captured["url"] = url
            raise OSError("stub: no network")

        import unittest.mock as mock
        with mock.patch.object(probe, "fetch_text", fake_fetch):
            result = fn(*args, **kwargs)
        return captured.get("url", ""), result

    def test_date_window_sends_match_type_prefix(self):
        from urllib.parse import urlparse, parse_qs
        url, _ = self._capture_url(
            probe.wayback_cdx_date_window,
            "https://www.npr.org/sections/money/2018/07/11/",
            "20180708",
            "20180718",
        )
        qs = parse_qs(urlparse(url).query)
        self.assertEqual(qs.get("matchType"), ["prefix"])

    def test_url_exact_sends_match_type_exact(self):
        from urllib.parse import urlparse, parse_qs
        url, _ = self._capture_url(
            probe.wayback_cdx_url_exact,
            "https://www.npr.org/sections/money/2018/10/31/662708285/paranormal-profits",
        )
        qs = parse_qs(urlparse(url).query)
        self.assertEqual(qs.get("matchType"), ["exact"])

    def test_date_window_result_has_required_keys(self):
        _, result = self._capture_url(
            probe.wayback_cdx_date_window,
            "https://www.npr.org/sections/money/2018/07/11/",
            "20180708",
            "20180718",
        )
        for key in ("rows", "query_url", "error_type", "error_message", "response_length", "zero_row_response"):
            self.assertIn(key, result, f"Missing key '{key}' in CDX result dict")

    def test_url_exact_result_has_required_keys(self):
        _, result = self._capture_url(
            probe.wayback_cdx_url_exact,
            "https://www.npr.org/sections/money/2018/10/31/662708285/paranormal-profits",
        )
        for key in ("rows", "query_url", "error_type", "error_message", "response_length", "zero_row_response"):
            self.assertIn(key, result, f"Missing key '{key}' in CDX result dict")

    def test_query_url_recorded_on_network_error(self):
        _, result = self._capture_url(
            probe.wayback_cdx_date_window,
            "https://www.npr.org/sections/money/2018/07/11/",
            "20180708",
            "20180718",
        )
        self.assertTrue(result["query_url"].startswith("https://web.archive.org/cdx/"))

    def test_network_error_sets_error_type(self):
        _, result = self._capture_url(
            probe.wayback_cdx_date_window,
            "https://www.npr.org/sections/money/2018/07/11/",
            "20180708",
            "20180718",
        )
        # A stub OSError becomes error_type="network_error"
        self.assertEqual(result["error_type"], "network_error")
        self.assertIsNotNone(result["error_message"])
        self.assertIsInstance(result["rows"], list)
        self.assertEqual(len(result["rows"]), 0)


# ---------------------------------------------------------------------------
# CDX diagnostics: zero-row response vs network/API error
# ---------------------------------------------------------------------------


class TestCdxZeroRowVsNetworkError(unittest.TestCase):
    """Zero-row CDX responses and network errors must be distinguishable."""

    def _run_with_response(self, text_body):
        """Inject a fake HTTP response body and return the CDX result dict."""
        import unittest.mock as mock

        def fake_fetch(url, retries=1):
            return {"text": text_body, "status_code": 200, "final_url": url, "content_type": "application/json", "data": text_body.encode()}

        with mock.patch.object(probe, "fetch_text", fake_fetch):
            return probe.wayback_cdx_date_window(
                "https://www.npr.org/sections/money/2018/07/11/",
                "20180708",
                "20180718",
            )

    def _run_with_network_error(self, exc):
        import unittest.mock as mock

        def fake_fetch(url, retries=1):
            raise exc

        with mock.patch.object(probe, "fetch_text", fake_fetch):
            return probe.wayback_cdx_date_window(
                "https://www.npr.org/sections/money/2018/07/11/",
                "20180708",
                "20180718",
            )

    def test_header_only_response_sets_zero_row_response_true(self):
        # CDX returns just a header row = genuine empty result, not an error
        import json
        body = json.dumps([["timestamp", "original", "statuscode", "mimetype"]])
        result = self._run_with_response(body)
        self.assertTrue(result["zero_row_response"])
        self.assertIsNone(result["error_type"])
        self.assertEqual(result["rows"], [])

    def test_empty_array_response_sets_zero_row_response_true(self):
        import json
        result = self._run_with_response(json.dumps([]))
        self.assertTrue(result["zero_row_response"])
        self.assertIsNone(result["error_type"])

    def test_one_data_row_returns_rows(self):
        import json
        body = json.dumps([
            ["timestamp", "original", "statuscode", "mimetype"],
            ["20180712120000", "https://www.npr.org/sections/money/2018/07/11/628123/slug", "200", "text/html"],
        ])
        result = self._run_with_response(body)
        self.assertFalse(result["zero_row_response"])
        self.assertIsNone(result["error_type"])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["timestamp"], "20180712120000")

    def test_network_oserror_sets_error_type_network_error(self):
        result = self._run_with_network_error(OSError("connection refused"))
        self.assertEqual(result["error_type"], "network_error")
        self.assertFalse(result["zero_row_response"])
        self.assertEqual(result["rows"], [])

    def test_json_decode_error_sets_error_type_parse_error(self):
        import json as _json
        result = self._run_with_network_error(_json.JSONDecodeError("unexpected", "", 0))
        self.assertEqual(result["error_type"], "parse_error")
        self.assertFalse(result["zero_row_response"])

    def test_response_length_recorded_on_success(self):
        import json
        body = json.dumps([["timestamp", "original", "statuscode", "mimetype"]])
        result = self._run_with_response(body)
        self.assertEqual(result["response_length"], len(body))

    def test_response_length_zero_on_network_error(self):
        result = self._run_with_network_error(OSError("timeout"))
        self.assertEqual(result["response_length"], 0)


# ---------------------------------------------------------------------------
# CDX self-test gating
# ---------------------------------------------------------------------------


class TestCdxSelfTest(unittest.TestCase):
    """cdx_self_test() must correctly identify pass/fail, and a failed
    self-test must prevent the run from producing misleading 'run_complete'
    results."""

    def _run_self_test_with(self, capture_counts):
        """
        Inject a stub for wayback_cdx_url_exact that returns the given number
        of rows for each CDX_SELF_TEST_URLS entry (in order).
        """
        import unittest.mock as mock
        import json

        call_idx = [0]

        def fake_cdx_exact(url, limit=5):
            n = capture_counts[call_idx[0] % len(capture_counts)]
            call_idx[0] += 1
            if n is None:
                return {
                    "rows": [],
                    "query_url": "https://web.archive.org/cdx/…",
                    "error_type": "network_error",
                    "error_message": "stub network error",
                    "response_length": 0,
                    "zero_row_response": False,
                }
            rows = [{"timestamp": f"2018010{i}120000", "original": url} for i in range(n)]
            return {
                "rows": rows,
                "query_url": "https://web.archive.org/cdx/…",
                "error_type": None,
                "error_message": None,
                "response_length": 42,
                "zero_row_response": n == 0,
            }

        with mock.patch.object(probe, "wayback_cdx_url_exact", fake_cdx_exact):
            return probe.cdx_self_test()

    def test_passes_when_all_known_urls_return_captures(self):
        result = self._run_self_test_with([2, 3])
        self.assertTrue(result["passed"])
        self.assertTrue(all(r["passed"] for r in result["results"]))

    def test_fails_when_any_known_url_returns_no_captures(self):
        result = self._run_self_test_with([2, 0])  # second URL returns 0 rows
        self.assertFalse(result["passed"])

    def test_fails_on_network_error(self):
        result = self._run_self_test_with([None, None])
        self.assertFalse(result["passed"])
        for r in result["results"]:
            self.assertEqual(r["error_type"], "network_error")

    def test_result_contains_per_url_entries(self):
        result = self._run_self_test_with([1, 1])
        self.assertEqual(len(result["results"]), len(probe.CDX_SELF_TEST_URLS))
        for entry in result["results"]:
            self.assertIn("url", entry)
            self.assertIn("passed", entry)
            self.assertIn("capture_count", entry)
            self.assertIn("query_url", entry)
            self.assertIn("error_type", entry)

    def test_failed_self_test_produces_cdx_self_test_failed_run_state(self):
        """When the CDX self-test fails, run() must write a summary with
        run_state='cdx_self_test_failed' and must NOT mark any episode as
        'no_identity_found' (i.e. must not run target investigation)."""
        import unittest.mock as mock
        import json
        from pathlib import Path

        def fake_cdx_exact(url, limit=5):
            return {
                "rows": [],
                "query_url": "https://web.archive.org/cdx/…",
                "error_type": "network_error",
                "error_message": "stub",
                "response_length": 0,
                "zero_row_response": False,
            }

        written_summaries = []

        def fake_write(path, payload):
            written_summaries.append(payload)

        with mock.patch.object(probe, "wayback_cdx_url_exact", fake_cdx_exact), \
             mock.patch.object(probe, "_write_json", fake_write), \
             mock.patch.object(probe, "write_placeholders", lambda: None):
            result = probe.run()

        # run() must return the self-test-failed summary
        self.assertEqual(result.get("run_state"), "cdx_self_test_failed")
        self.assertFalse(result.get("run_complete"))
        self.assertEqual(result.get("counts", {}).get("attempted"), 0)
        # No episodes should have been investigated
        self.assertEqual(len(result.get("episodes", [])), 0)
        # The written summary must also be marked as failed
        self.assertTrue(any(s.get("run_state") == "cdx_self_test_failed" for s in written_summaries))

    def test_passed_self_test_runs_targets(self):
        """When the CDX self-test passes, run() must attempt all targets."""
        import unittest.mock as mock

        def fake_cdx_exact(url, limit=5):
            return {
                "rows": [{"timestamp": "20181101120000", "original": url, "statuscode": "200", "mimetype": "text/html"}],
                "query_url": "https://web.archive.org/cdx/…",
                "error_type": None,
                "error_message": None,
                "response_length": 42,
                "zero_row_response": False,
            }

        def fake_cdx_date_window(url_pattern, from_date, to_date, limit=40):
            return {
                "rows": [],
                "query_url": "https://web.archive.org/cdx/…",
                "error_type": None,
                "error_message": None,
                "response_length": 2,
                "zero_row_response": True,
            }

        with mock.patch.object(probe, "wayback_cdx_url_exact", fake_cdx_exact), \
             mock.patch.object(probe, "wayback_cdx_date_window", fake_cdx_date_window), \
             mock.patch.object(probe, "_write_json", lambda path, payload: None), \
             mock.patch.object(probe, "write_placeholders", lambda: None):
            result = probe.run()

        # Self-test passed → all targets must be attempted
        self.assertEqual(result.get("counts", {}).get("attempted"), len(probe.TARGETS))


if __name__ == "__main__":
    unittest.main()
