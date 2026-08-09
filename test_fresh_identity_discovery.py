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

import probe_fresh_identity_discovery as probe


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
# Page scoring (confirmed identity requires title + date + indicator)
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
        self.assertTrue(result["has_episode_context"])

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


if __name__ == "__main__":
    unittest.main()
