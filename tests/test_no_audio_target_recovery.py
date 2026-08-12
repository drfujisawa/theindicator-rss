from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import URLError

from scripts.recovery import recover_no_audio_targets as recovery


class RequestAndOutcomeTests(unittest.TestCase):
    def test_network_failure_is_distinct_from_no_result(self):
        def failing_request(**_kwargs):
            raise URLError("temporary DNS failure")

        response = recovery.request_with_retries(
            url="https://www.npr.org/example",
            retries=2,
            request_fn=failing_request,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error_type"], "network_error")

        outcome = recovery.classify_probe_outcome(
            endpoint_attempts=[response],
            candidate_urls=[],
            validated_audio=None,
        )
        self.assertEqual(outcome, "network_failed_all")

    def test_successful_requests_without_candidates_is_no_candidate(self):
        outcome = recovery.classify_probe_outcome(
            endpoint_attempts=[{"ok": True}],
            candidate_urls=[],
            validated_audio=None,
        )
        self.assertEqual(outcome, "no_candidate_found")


class CandidateValidationTests(unittest.TestCase):
    def test_candidate_validation_uses_playability_signals(self):
        calls = []

        def fake_request(**kwargs):
            calls.append(kwargs["method"])
            return {
                "ok": True,
                "http_status": 200,
                "final_url": kwargs["url"],
                "content_type": "audio/mpeg",
                "content_length": "123456",
                "text": "",
            }

        result = recovery.validate_audio_candidate(
            candidate_url="https://ondemand.npr.org/anon.npr-mp3/npr/indicator/test.mp3",
            request_fn=fake_request,
        )
        self.assertTrue(result["playable"])
        self.assertEqual(result["http_status"], 200)
        self.assertIn("HEAD", calls)

    def test_story_audio_id_provenance(self):
        target = recovery.Target(
            date="2021-08-20",
            title="Two Indicators: Will Remote Work Kill The Office?",
            story_id="1029846068",
            audio_id="1198960544",
            npr_url="https://www.npr.org/2021/08/20/1029846068/two-indicators-will-remote-work-kill-the-office",
        )
        provenance = recovery.compute_identity_provenance(
            target=target,
            source_endpoints=[
                "https://www.npr.org/player/embed/1029846068/1198960544",
            ],
            validated_audio={
                "final_url": "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/file.mp3?e=1029846068",
                "simplecast_uuid": None,
            },
        )
        self.assertGreaterEqual(provenance["score"], 0.7)
        self.assertEqual(provenance["confidence"], "high")


class DuplicateDetectionTests(unittest.TestCase):
    def test_duplicate_underlying_audio_detection(self):
        duplicate = recovery.detect_duplicate_underlying_audio(
            validated_audio={
                "playable": True,
                "candidate_url": "https://npr.simplecastaudio.com/show/episodes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/audio",
                "final_url": "https://npr.simplecastaudio.com/show/episodes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/audio",
                "simplecast_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
            same_day_resolved=[
                {
                    "story_id": "1034120823",
                    "final_url": "https://npr.simplecastaudio.com/show/episodes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/audio",
                }
            ],
        )
        self.assertTrue(duplicate["is_duplicate"])
        self.assertEqual(duplicate["matched_story_id"], "1034120823")

    def test_two_indicators_same_audio_classifies_duplicate(self):
        target = recovery.Target(
            date="2021-09-03",
            title="Two Indicators: Water Pressure",
            story_id="1034085667",
            audio_id="1198960519",
            npr_url="https://www.npr.org/2021/09/03/1034085667/two-indicators-water-pressure",
        )
        status = recovery.classify_target(
            target=target,
            baseline=recovery.baseline_classification(target.story_id),
            validated_audio={"playable": True},
            duplicate_result={
                "is_duplicate": True,
                "matched_story_id": "1034120823",
                "reason": "matching Simplecast episode UUID",
            },
        )
        self.assertEqual(status, "DUPLICATE_OR_ALTERNATE_OF_EXISTING_RSS_ITEM")


class ProductionSafetyTests(unittest.TestCase):
    def test_production_file_guard_detects_no_change(self):
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.txt"
            b = Path(tmp) / "b.txt"
            a.write_text("alpha", encoding="utf-8")
            b.write_text("beta", encoding="utf-8")
            before = recovery.capture_file_hashes([a, b])
            after = recovery.capture_file_hashes([a, b])
            self.assertFalse(recovery.production_files_changed(before, after))


if __name__ == "__main__":
    unittest.main()
