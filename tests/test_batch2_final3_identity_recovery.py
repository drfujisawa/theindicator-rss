#!/usr/bin/env python3
"""Unit tests for probe_batch2_final3_identity_recovery.py (network-free)."""

import unittest
from unittest import mock
from pathlib import Path
import re

from scripts.recovery import probe_batch2_final3_identity_recovery as probe
REPO_ROOT = Path(__file__).resolve().parents[1]



class TestRetryPlanFromKnownCaptures(unittest.TestCase):
    def test_retry_plan_keeps_known_timestamp_and_adds_alternates(self):
        prior_diag = {
            "date_window_captures": [
                {
                    "timestamp": "20180426234034",
                    "original_url": "https://www.npr.org/sections/money/2018/04/26/606151956/california-s-housing-conundrum",
                }
            ],
            "identity_candidates": [],
            "cdx_queries": [],
        }

        def fake_cdx_exact(url, limit=8):
            return {
                "rows": [
                    {"timestamp": "20180426234034", "original": url},
                    {"timestamp": "20180427195754", "original": url},
                ],
                "query_url": "https://web.archive.org/cdx/...",
                "error_type": None,
                "error_message": None,
            }

        with mock.patch.object(probe.base, "wayback_cdx_url_exact", fake_cdx_exact):
            plan, exact_queries = probe.build_capture_retry_plan(probe.TARGETS[1], prior_diag)

        urls = [p["archive_url"] for p in plan]
        self.assertTrue(any("20180426234034id_" in u for u in urls))
        self.assertTrue(any("20180426234034/https://www.npr.org" in u for u in urls))
        self.assertTrue(any("20180427195754id_" in u for u in urls))
        self.assertEqual(len(exact_queries), 1)


class TestPriorEvidencePaths(unittest.TestCase):
    def test_batch2_prior_evidence_reads_from_active_data_directory(self):
        evidence = probe.load_prior_evidence("2018-10-11")
        self.assertEqual(
            evidence["sources"]["batch2_diag"],
            "batch2_fresh_identity_discovery_2018-10-11_diag.json",
        )
        self.assertEqual(
            evidence["sources"]["batch2_summary"],
            "batch2_fresh_identity_discovery_summary.json",
        )
        self.assertIn("/data/recovery/", str(probe.ACTIVE_INPUT_DIR / evidence["sources"]["batch2_diag"]))


class TestAdjacentDateContaminationRejection(unittest.TestCase):
    def test_student_loan_story_rejected_for_oct11_target(self):
        target = probe.TARGETS[0]
        plan_item = {
            "timestamp": "20181013013833",
            "url": "https://www.npr.org/sections/money/2018/10/12/656978022/episode-869-the-student-loan-whistleblower",
            "archive_url": "https://web.archive.org/web/20181013013833id_/https://www.npr.org/sections/money/2018/10/12/656978022/episode-869-the-student-loan-whistleblower",
            "archive_variant": "id_",
            "source": "prior_stage_c",
        }
        page = """
        <html><head>
        <title>Episode 869: The Student Loan Whistleblower : Planet Money : NPR</title>
        <meta property='article:published_time' content='2018-10-12T12:00:00Z'>
        <link rel='canonical' href='https://www.npr.org/sections/money/2018/10/12/656978022/episode-869-the-student-loan-whistleblower'>
        </head><body>showTitle: \"The Indicator from Planet Money\" p=510325</body></html>
        """

        parsed = probe.parse_capture_result(target, plan_item, page)
        self.assertFalse(parsed["episode_qualified"])
        self.assertTrue(parsed["blocked_adjacent_unrelated"])
        self.assertIn("adjacent_unrelated_story", parsed["rejection_reasons"])


class TestStrictTitleDateStoryIdGate(unittest.TestCase):
    def test_adjacent_date_not_accepted_for_trusted_identity(self):
        target = probe.TARGETS[1]
        plan_item = {
            "timestamp": "20180425010101",
            "url": "https://www.npr.org/sections/money/2018/04/25/605819103/when-chinas-ships-come-in",
            "archive_url": "https://web.archive.org/web/20180425010101id_/https://www.npr.org/sections/money/2018/04/25/605819103/when-chinas-ships-come-in",
            "archive_variant": "id_",
            "source": "bounded_discovery",
        }
        page = """
        <html><head>
        <title>When China's Ships Come In : The Indicator from Planet Money : NPR</title>
        <meta property='article:published_time' content='2018-04-25T11:00:00Z'>
        <link rel='canonical' href='https://www.npr.org/sections/money/2018/04/25/605819103/when-chinas-ships-come-in'>
        </head><body>showTitle: \"The Indicator from Planet Money\" p=510325</body></html>
        """

        parsed = probe.parse_capture_result(target, plan_item, page)
        self.assertFalse(parsed["episode_qualified"])  # exact date required
        self.assertIn("date_not_exact", parsed["rejection_reasons"])

    def test_exact_title_required(self):
        target = probe.TARGETS[1]
        plan_item = {
            "timestamp": "20180424010101",
            "url": "https://www.npr.org/sections/money/2018/04/24/605500000/wrong-title",
            "archive_url": "https://web.archive.org/web/20180424010101id_/https://www.npr.org/sections/money/2018/04/24/605500000/wrong-title",
            "archive_variant": "id_",
            "source": "bounded_discovery",
        }
        page = """
        <html><head>
        <title>China's Shipping Logjam : The Indicator from Planet Money : NPR</title>
        <meta property='article:published_time' content='2018-04-24T11:00:00Z'>
        <link rel='canonical' href='https://www.npr.org/sections/money/2018/04/24/605500000/wrong-title'>
        </head><body>showTitle: \"The Indicator from Planet Money\" p=510325</body></html>
        """
        parsed = probe.parse_capture_result(target, plan_item, page)
        self.assertFalse(parsed["episode_qualified"])
        self.assertFalse(parsed["exact_title_match"])
        self.assertIn("title_not_exact", parsed["rejection_reasons"])


class TestAlternateCaptureAfterTimeout(unittest.TestCase):
    def test_second_capture_attempt_used_after_first_timeout(self):
        target = probe.TARGETS[0]
        first = {
            "timestamp": "20181011180101",
            "url": "https://www.npr.org/sections/money/2018/10/11/656700000/chinas-brave-new-world",
            "archive_url": "https://web.archive.org/web/20181011180101id_/https://www.npr.org/sections/money/2018/10/11/656700000/chinas-brave-new-world",
            "archive_variant": "id_",
            "source": "prior_stage_c",
        }
        second = {
            "timestamp": "20181011235959",
            "url": "https://www.npr.org/sections/money/2018/10/11/656700000/chinas-brave-new-world",
            "archive_url": "https://web.archive.org/web/20181011235959id_/https://www.npr.org/sections/money/2018/10/11/656700000/chinas-brave-new-world",
            "archive_variant": "id_",
            "source": "exact_cdx_alternate_timestamp",
        }

        html_ok = """
        <html><head>
        <title>China's Brave New World : The Indicator from Planet Money : NPR</title>
        <meta property='article:published_time' content='2018-10-11T16:00:00Z'>
        <link rel='canonical' href='https://www.npr.org/sections/money/2018/10/11/656700000/chinas-brave-new-world'>
        </head><body>showTitle: \"The Indicator from Planet Money\" p=510325</body></html>
        """

        call_count = {"n": 0}

        def fake_fetch(url):
            call_count["n"] += 1
            if "20181011180101" in url:
                raise OSError("timed out")
            return {"text": html_ok}

        with mock.patch.object(probe, "load_prior_evidence", return_value={"batch2_diag": {}, "sources": {}}), \
             mock.patch.object(probe, "build_capture_retry_plan", return_value=([first, second], [])), \
             mock.patch.object(probe, "_should_run_broad_discovery", return_value=False), \
             mock.patch.object(probe, "_with_backoff_fetch", side_effect=fake_fetch), \
             mock.patch.object(probe, "_run_player_audio_chain", lambda *args, **kwargs: None):
            diag = probe.investigate_target(target)

        self.assertEqual(call_count["n"], 2)
        self.assertEqual(diag["captures_successfully_parsed"], 1)
        self.assertEqual(diag["archive_captures_failed"], 1)
        self.assertIsNotNone(diag["confirmed_identity"])


class TestArchiveFailureClassificationSafety(unittest.TestCase):
    def test_archive_failures_not_demoted_to_lower_priority(self):
        diag = {
            "confirmed_identity": None,
            "validated_audio": [],
            "captures_successfully_parsed": 0,
            "archive_captures_failed": 4,
            "archive_captures_tried": [{}],
            "identity_candidates": [],
            "global_cdx_self_test_passed": True,
            "bounded_search_completed": True,
            "exact_cdx_queries": [],
            "discovery_cdx_queries": [],
        }
        classification, _, _ = probe.classify_result(diag)
        self.assertEqual(classification, "archive_fetch_failed_identity_unresolved")

    def test_no_capture_attempts_not_demoted_to_lower_priority(self):
        diag = {
            "confirmed_identity": None,
            "validated_audio": [],
            "captures_successfully_parsed": 0,
            "archive_captures_failed": 0,
            "archive_captures_tried": [],
            "identity_candidates": [],
            "global_cdx_self_test_passed": True,
            "bounded_search_completed": False,
            "exact_cdx_queries": [],
            "discovery_cdx_queries": [],
        }
        classification, _, _ = probe.classify_result(diag)
        self.assertEqual(classification, "incomplete_bounded_search_identity_unresolved")

    def test_completed_search_without_identity_demoted_to_lower_priority(self):
        diag = {
            "confirmed_identity": None,
            "validated_audio": [],
            "captures_successfully_parsed": 3,
            "archive_captures_failed": 0,
            "archive_captures_tried": [{}, {}, {}],
            "identity_candidates": [],
            "global_cdx_self_test_passed": True,
            "bounded_search_completed": True,
            "exact_cdx_queries": [{"error_type": None}],
            "discovery_cdx_queries": [{"error_type": None}],
        }
        classification, _, _ = probe.classify_result(diag)
        self.assertEqual(classification, "lower_priority_unresolved")

    def test_trusted_identity_still_takes_precedence(self):
        diag = {
            "confirmed_identity": {"trusted": True},
            "validated_audio": [],
            "archive_captures_failed": 99,
            "global_cdx_self_test_passed": False,
            "bounded_search_completed": False,
        }
        classification, _, _ = probe.classify_result(diag)
        self.assertEqual(classification, "identity_found_audio_unresolved")


class TestBlockedAdjacentStories(unittest.TestCase):
    def test_la_story_rejected_for_apr24_target(self):
        target = probe.TARGETS[1]
        plan_item = {
            "timestamp": "20180424005159",
            "url": "https://www.npr.org/sections/money/2018/04/23/605033696/3-things-you-didn-t-know-about-la",
            "archive_url": "https://web.archive.org/web/20180424005159id_/https://www.npr.org/sections/money/2018/04/23/605033696/3-things-you-didn-t-know-about-la",
            "archive_variant": "id_",
            "source": "bounded_discovery",
        }
        page = """
        <html><head>
        <title>3 Things You Didn't Know About LA : Planet Money : NPR</title>
        <meta property='article:published_time' content='2018-04-23T12:00:00Z'>
        <link rel='canonical' href='https://www.npr.org/sections/money/2018/04/23/605033696/3-things-you-didn-t-know-about-la'>
        </head><body>showTitle: \"The Indicator from Planet Money\" p=510325</body></html>
        """
        parsed = probe.parse_capture_result(target, plan_item, page)
        self.assertFalse(parsed["episode_qualified"])
        self.assertTrue(parsed["blocked_adjacent_unrelated"])
        self.assertIn("adjacent_unrelated_story", parsed["rejection_reasons"])


class TestProvenanceLinkedAudioRequirement(unittest.TestCase):
    def test_untrusted_audio_evidence_not_recoverable(self):
        audio_evidence = {
            "audio_url": "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2018/10/file.mp3",
            "audio_id": "20181011",
            "provenance": {
                "trust_level": "untrusted",
                "episode_qualified": False,
                "target_episode": "2018-10-11",
            },
        }

        with mock.patch.object(probe.base, "validate_audio_candidate_live", return_value={
            "candidate_url": audio_evidence["audio_url"],
            "validation_status": "validated",
            "valid_npr_indicator_audio": True,
        }):
            result = probe.base.validate_audio_evidence_live(audio_evidence, "2018-10-11")

        self.assertFalse(result["trusted_for_recovery"])


class TestSeededExactCdxCap(unittest.TestCase):
    def _seed(self, timestamp, url, source):
        return {"timestamp": timestamp, "url": url, "source": source}

    def test_seeded_exact_cdx_urls_are_hard_capped(self):
        prior_diag = {
            "date_window_captures": [
                {
                    "timestamp": "20180101000000",
                    "original_url": f"https://www.npr.org/sections/money/2018/01/01/70{i}/a-{i}",
                }
                for i in range(10)
            ],
            "identity_candidates": [],
            "cdx_queries": [],
        }
        called = []

        def fake_cdx_exact(url, limit=8):
            called.append(url)
            return {"rows": [], "query_url": "q", "error_type": None, "error_message": None}

        with mock.patch.object(probe.base, "wayback_cdx_url_exact", fake_cdx_exact):
            probe.build_capture_retry_plan(probe.TARGETS[0], prior_diag)

        self.assertEqual(len(called), probe.MAX_SEEDED_EXACT_CDX_URLS)

    def test_highest_priority_seeded_urls_retained(self):
        u_stage = "https://www.npr.org/sections/money/2018/04/26/111111111/stage"
        u_identity = "https://www.npr.org/sections/money/2018/04/26/222222222/identity"
        u_cdx = "https://www.npr.org/sections/money/2018/04/26/333333333/cdx"
        u_other = "https://www.npr.org/sections/money/2018/04/26/444444444/other"
        seeds = [
            self._seed("20180426010101", u_cdx, "prior_cdx_scored"),
            self._seed("20180426010102", u_identity, "prior_identity_candidate"),
            self._seed("20180426010103", u_stage, "prior_stage_c"),
            self._seed("20180426010104", u_other, "unknown_source"),
        ]
        with mock.patch.object(probe, "MAX_SEEDED_EXACT_CDX_URLS", 3):
            selected = probe._seeded_urls_for_exact_cdx(seeds)
        self.assertEqual(selected, [u_stage, u_identity, u_cdx])


class TestRequestBudgetCoverage(unittest.TestCase):
    def test_budget_includes_seeded_exact_cdx(self):
        budget = probe.request_budget()["per_episode"]
        self.assertIn("seeded_exact_cdx_queries", budget)
        self.assertEqual(budget["seeded_exact_cdx_queries"], probe.MAX_SEEDED_EXACT_CDX_URLS)

    def test_declared_max_matches_capped_code_path(self):
        budget = probe.request_budget()["per_episode"]
        expected = (
            probe.MAX_DISCOVERY_CDX_QUERIES
            + probe.MAX_SEEDED_EXACT_CDX_URLS
            + probe.MAX_CAPTURE_ATTEMPTS
            + probe.MAX_PLAYER_FETCHES
            + probe.MAX_ARCHIVED_PLAYER_CDX_QUERIES
            + probe.MAX_ARCHIVED_PLAYER_FETCHES
            + probe.MAX_AUDIO_VALIDATIONS
        )
        self.assertEqual(budget["max_logical_requests"], expected)

    def test_workflow_timeout_exceeds_conservative_budget(self):
        workflow = REPO_ROOT / ".github/workflows/probe-batch2-final3-identity-recovery.yml"
        self.assertTrue(workflow.exists(), f"Missing workflow file: {workflow}")
        text = workflow.read_text(encoding="utf-8")
        match = re.search(r"timeout-minutes:\s*(\d+)", text)
        self.assertIsNotNone(match)
        workflow_timeout_seconds = int(match.group(1)) * 60
        runtime_ceiling = probe.request_budget()["per_run"]["conservative_timeout_ceiling_seconds"]
        self.assertGreater(workflow_timeout_seconds, runtime_ceiling)


class TestNarrowDateBoundedPatterns(unittest.TestCase):
    def test_patterns_stay_on_exact_reference_date_and_money_section(self):
        patterns = probe._build_bounded_patterns(probe.TARGETS[0])
        self.assertEqual(
            patterns,
            [
                "https://www.npr.org/sections/money/2018/10/11/",
                "http://www.npr.org/sections/money/2018/10/11/",
            ],
        )


class TestAffiliateExactTitleEvidencePath(unittest.TestCase):
    def test_affiliate_exact_title_urls_contribute_but_do_not_create_trusted_identity(self):
        target = probe.TARGETS[1]
        affiliate_url = "https://www.npr.org/sections/theindicator/2018/04/24/605819103/when-chinas-ships-come-in"

        html = """
        <html><head>
        <title>When China's Ships Come In : The Indicator from Planet Money : NPR</title>
        <meta property='article:published_time' content='2018-04-24T11:00:00Z'>
        <link rel='canonical' href='https://www.npr.org/sections/theindicator/2018/04/24/605819103/when-chinas-ships-come-in'>
        </head><body>showTitle: \"The Indicator from Planet Money\" p=510325</body></html>
        """

        def fake_cdx_exact(url, limit=8):
            self.assertEqual(url, affiliate_url)
            return {
                "rows": [{"timestamp": "20180424110000", "original": affiliate_url}],
                "query_url": "https://web.archive.org/cdx/search/cdx?...",
                "error_type": None,
                "error_message": None,
            }

        with mock.patch.object(
            probe,
            "load_prior_evidence",
            return_value={"batch2_diag": {}, "sources": {}, "global_cdx_self_test_passed": True},
        ), mock.patch.object(
            probe,
            "_load_affiliate_exact_title_evidence",
            return_value={
                "files_scanned": ["indicator_unresolved_affiliate_recovery_00_09.json"],
                "matched_sources": ["indicator_unresolved_affiliate_recovery_00_09.json"],
                "matched_source_count": 1,
                "exact_title_urls": [affiliate_url],
            },
        ), mock.patch.object(
            probe.base,
            "wayback_cdx_url_exact",
            side_effect=fake_cdx_exact,
        ), mock.patch.object(
            probe,
            "_with_backoff_fetch",
            return_value={"text": html},
        ), mock.patch.object(
            probe,
            "_should_run_broad_discovery",
            return_value=False,
        ):
            diag = probe.investigate_target(target)

        self.assertEqual(diag["exact_title_affiliate_archive_evidence"]["matched_source_count"], 1)
        self.assertTrue(any(q.get("url") == affiliate_url for q in diag["exact_cdx_queries"]))
        self.assertIsNone(diag["confirmed_identity"])
        self.assertEqual(diag["final_classification"], "lower_priority_unresolved")


if __name__ == "__main__":
    unittest.main()
