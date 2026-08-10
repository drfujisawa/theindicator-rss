#!/usr/bin/env python3
"""Unit tests for probe_batch2_final3_identity_recovery.py (network-free)."""

import unittest
from unittest import mock

import probe_batch2_final3_identity_recovery as probe


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
        target = probe.TARGETS[2]
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


class TestAlternateCaptureAfterTimeout(unittest.TestCase):
    def test_second_capture_attempt_used_after_first_timeout(self):
        target = probe.TARGETS[1]
        first = {
            "timestamp": "20180426234034",
            "url": "https://www.npr.org/sections/money/2018/04/26/606151956/california-s-housing-conundrum",
            "archive_url": "https://web.archive.org/web/20180426234034id_/https://www.npr.org/sections/money/2018/04/26/606151956/california-s-housing-conundrum",
            "archive_variant": "id_",
            "source": "prior_stage_c",
        }
        second = {
            "timestamp": "20180427195754",
            "url": "https://www.npr.org/sections/money/2018/04/26/606151956/california-s-housing-conundrum",
            "archive_url": "https://web.archive.org/web/20180427195754id_/https://www.npr.org/sections/money/2018/04/26/606151956/california-s-housing-conundrum",
            "archive_variant": "id_",
            "source": "exact_cdx_alternate_timestamp",
        }

        html_ok = """
        <html><head>
        <title>California's Housing Conundrum : The Indicator from Planet Money : NPR</title>
        <meta property='article:published_time' content='2018-04-26T16:00:00Z'>
        <link rel='canonical' href='https://www.npr.org/sections/money/2018/04/26/606151956/california-s-housing-conundrum'>
        </head><body>showTitle: \"The Indicator from Planet Money\" p=510325</body></html>
        """

        call_count = {"n": 0}

        def fake_fetch(url):
            call_count["n"] += 1
            if "20180426234034" in url:
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
    def test_archive_failures_not_reported_as_no_identity(self):
        diag = {
            "confirmed_identity": None,
            "validated_audio": [],
            "captures_successfully_parsed": 0,
            "archive_captures_failed": 4,
            "identity_candidates": [],
        }
        classification, _, _ = probe.classify_result(diag)
        self.assertEqual(classification, "archive_fetch_failed_identity_unresolved")


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


if __name__ == "__main__":
    unittest.main()
