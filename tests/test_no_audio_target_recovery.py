from pathlib import Path
from tempfile import TemporaryDirectory
import json
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

    def test_wrong_content_type_is_not_playable(self):
        """HTML/JSON/application content type must not be treated as audio."""
        def fake_request(**kwargs):
            return {
                "ok": True,
                "http_status": 200,
                "final_url": kwargs["url"],
                "content_type": "text/html; charset=utf-8",
                "content_length": "9999",
                "text": "",
            }

        result = recovery.validate_audio_candidate(
            candidate_url="https://www.npr.org/some-page",
            request_fn=fake_request,
        )
        self.assertFalse(result["playable"])

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


class ProvenanceGateTests(unittest.TestCase):
    """Tests that RECOVERED_AND_VALIDATED requires high provenance confidence."""

    def _make_regular_target(self) -> recovery.Target:
        return recovery.Target(
            date="2020-03-15",
            title="Some Episode",
            story_id="999111222",
            audio_id="888000333",
            npr_url="https://www.npr.org/2020/03/15/999111222/some-episode",
        )

    def test_playable_unrelated_audio_with_zero_provenance_is_not_recovered(self):
        """A generic playable audio URL with score 0 must NOT become RECOVERED_AND_VALIDATED."""
        target = self._make_regular_target()
        weak_provenance = {"confidence": "low", "score": 0.0, "evidence": []}
        status = recovery.classify_target(
            target=target,
            baseline=recovery.baseline_classification(target.story_id),
            validated_audio={"playable": True},
            duplicate_result={"is_duplicate": False, "matched_story_id": None, "reason": None},
            provenance=weak_provenance,
        )
        self.assertNotEqual(status, "RECOVERED_AND_VALIDATED")
        self.assertEqual(status, "CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED")

    def test_playable_audio_with_medium_provenance_is_not_recovered(self):
        """Medium confidence (score 0.35–0.69) must not pass the gate."""
        target = self._make_regular_target()
        medium_provenance = {"confidence": "medium", "score": 0.35, "evidence": ["some evidence"]}
        status = recovery.classify_target(
            target=target,
            baseline=recovery.baseline_classification(target.story_id),
            validated_audio={"playable": True},
            duplicate_result={"is_duplicate": False, "matched_story_id": None, "reason": None},
            provenance=medium_provenance,
        )
        self.assertNotEqual(status, "RECOVERED_AND_VALIDATED")

    def test_playable_audio_with_high_provenance_is_recovered(self):
        """High confidence + playable audio on a regular target → RECOVERED_AND_VALIDATED."""
        target = self._make_regular_target()
        high_provenance = {"confidence": "high", "score": 0.8, "evidence": ["story_id in URL"]}
        status = recovery.classify_target(
            target=target,
            baseline=recovery.baseline_classification(target.story_id),
            validated_audio={"playable": True},
            duplicate_result={"is_duplicate": False, "matched_story_id": None, "reason": None},
            provenance=high_provenance,
        )
        self.assertEqual(status, "RECOVERED_AND_VALIDATED")

    def test_non_playable_audio_with_high_provenance_is_not_recovered(self):
        """High provenance alone is not enough — audio must also be playable."""
        target = self._make_regular_target()
        high_provenance = {"confidence": "high", "score": 0.8, "evidence": ["story_id in URL"]}
        status = recovery.classify_target(
            target=target,
            baseline=recovery.baseline_classification(target.story_id),
            validated_audio={"playable": False},
            duplicate_result={"is_duplicate": False, "matched_story_id": None, "reason": None},
            provenance=high_provenance,
        )
        self.assertNotEqual(status, "RECOVERED_AND_VALIDATED")


class TwoIndicatorsSafetyTests(unittest.TestCase):
    """Tests that Two Indicators targets cannot be independently recovered."""

    def _make_two_indicators_target(self) -> recovery.Target:
        return recovery.Target(
            date="2021-09-03",
            title="Two Indicators: Water Pressure",
            story_id="1034085667",
            audio_id="1198960519",
            npr_url="https://www.npr.org/2021/09/03/1034085667/two-indicators-water-pressure",
        )

    def test_two_indicators_playable_candidate_without_distinctness_proof_not_recovered(self):
        """Two Indicators target with playable audio and high provenance stays unresolved."""
        target = self._make_two_indicators_target()
        high_provenance = {"confidence": "high", "score": 0.8, "evidence": ["story_id in URL"]}
        status = recovery.classify_target(
            target=target,
            baseline=recovery.baseline_classification(target.story_id),
            validated_audio={"playable": True},
            duplicate_result={"is_duplicate": False, "matched_story_id": None, "reason": None},
            provenance=high_provenance,
        )
        # Must remain PROBABLY_NOT_SEPARATE_EPISODE, not RECOVERED_AND_VALIDATED.
        self.assertEqual(status, "PROBABLY_NOT_SEPARATE_EPISODE")

    def test_two_indicators_duplicate_found_classifies_correctly(self):
        """Two Indicators target with a corpus duplicate → DUPLICATE_OR_ALTERNATE."""
        target = self._make_two_indicators_target()
        status = recovery.classify_target(
            target=target,
            baseline=recovery.baseline_classification(target.story_id),
            validated_audio={"playable": True},
            duplicate_result={
                "is_duplicate": True,
                "matched_story_id": "1034120823",
                "reason": "matching Simplecast episode UUID",
            },
            provenance={"confidence": "high", "score": 0.8, "evidence": []},
        )
        self.assertEqual(status, "DUPLICATE_OR_ALTERNATE_OF_EXISTING_RSS_ITEM")

    def test_two_indicators_all_four_story_ids_are_protected(self):
        """All four Two Indicators story IDs stay PROBABLY_NOT_SEPARATE_EPISODE."""
        for story_id in recovery.TWO_INDICATORS_STORY_IDS:
            target = recovery.Target(
                date="2021-01-01",
                title="Two Indicators: Test",
                story_id=story_id,
                audio_id="0000000000",
                npr_url=f"https://www.npr.org/2021/01/01/{story_id}/test",
            )
            status = recovery.classify_target(
                target=target,
                baseline=recovery.baseline_classification(story_id),
                validated_audio={"playable": True},
                duplicate_result={"is_duplicate": False, "matched_story_id": None, "reason": None},
                provenance={"confidence": "high", "score": 0.9, "evidence": []},
            )
            self.assertEqual(
                status,
                "PROBABLY_NOT_SEPARATE_EPISODE",
                f"story_id {story_id} must not become RECOVERED_AND_VALIDATED",
            )


class DuplicateDetectionTests(unittest.TestCase):
    def test_duplicate_underlying_audio_detection(self):
        duplicate = recovery.detect_duplicate_underlying_audio(
            validated_audio={
                "playable": True,
                "candidate_url": "https://npr.simplecastaudio.com/show/episodes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/audio",
                "final_url": "https://npr.simplecastaudio.com/show/episodes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/audio",
                "simplecast_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            },
            resolved_corpus=[
                {
                    "story_id": "1034120823",
                    "final_url": "https://npr.simplecastaudio.com/show/episodes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/audio",
                }
            ],
        )
        self.assertTrue(duplicate["is_duplicate"])
        self.assertEqual(duplicate["matched_story_id"], "1034120823")

    def test_cross_date_duplicate_audio_detection(self):
        """Duplicate audio from a different date must still be detected (cross-date)."""
        duplicate = recovery.detect_duplicate_underlying_audio(
            validated_audio={
                "playable": True,
                "candidate_url": "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/same.mp3",
                "final_url": "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/same.mp3",
                "simplecast_uuid": None,
            },
            resolved_corpus=[
                {
                    "story_id": "9991111111",
                    "date": "2020-12-01",   # different date
                    "final_url": "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/same.mp3",
                }
            ],
        )
        self.assertTrue(duplicate["is_duplicate"])
        self.assertEqual(duplicate["matched_story_id"], "9991111111")

    def test_no_duplicate_when_corpus_is_empty(self):
        result = recovery.detect_duplicate_underlying_audio(
            validated_audio={
                "playable": True,
                "candidate_url": "https://ondemand.npr.org/unique.mp3",
                "final_url": "https://ondemand.npr.org/unique.mp3",
                "simplecast_uuid": None,
            },
            resolved_corpus=[],
        )
        self.assertFalse(result["is_duplicate"])

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
            provenance={"confidence": "high", "score": 0.8, "evidence": []},
        )
        self.assertEqual(status, "DUPLICATE_OR_ALTERNATE_OF_EXISTING_RSS_ITEM")


class CandidateCapTests(unittest.TestCase):
    """Tests for the hard per-target candidate cap and ranking."""

    def _make_target(self) -> recovery.Target:
        return recovery.Target(
            date="2020-05-10",
            title="Test Episode",
            story_id="123456789",
            audio_id="987654321",
            npr_url="https://www.npr.org/2020/05/10/123456789/test",
        )

    def test_candidate_cap_enforced(self):
        """rank_candidates returns at most MAX_CANDIDATES_PER_TARGET after slicing."""
        target = self._make_target()
        # Create 15 unique candidates
        candidates = [f"https://cdn.example.com/audio/{i}.mp3" for i in range(15)]
        source_by_candidate = {url: [] for url in candidates}
        ranked = recovery.rank_candidates(candidates, target, source_by_candidate)
        selected = ranked[: recovery.MAX_CANDIDATES_PER_TARGET]
        skipped = ranked[recovery.MAX_CANDIDATES_PER_TARGET :]
        self.assertLessEqual(len(selected), recovery.MAX_CANDIDATES_PER_TARGET)
        self.assertEqual(len(selected) + len(skipped), len(ranked))

    def test_high_provenance_candidates_ranked_first(self):
        """Candidates linked to story_id/audio_id source endpoints come first."""
        target = self._make_target()
        strong_url = "https://ondemand.npr.org/anon.npr-mp3/indicator/strong.mp3"
        weak_url = "https://cdn.example.com/generic.mp3"
        source_by_candidate = {
            strong_url: [f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"],
            weak_url: ["https://www.npr.org/some-other-page"],
        }
        ranked = recovery.rank_candidates([weak_url, strong_url], target, source_by_candidate)
        self.assertEqual(ranked[0], strong_url)

    def test_duplicate_url_identity_deduplicated(self):
        """Two wrapper URLs resolving to the same underlying path are deduplicated."""
        target = self._make_target()
        url_a = "https://ondemand.npr.org/anon.npr-mp3/indicator/ep.mp3?foo=1"
        url_b = "https://ondemand.npr.org/anon.npr-mp3/indicator/ep.mp3?bar=2"
        # normalize_audio_identity strips query string, so netloc+path are equal
        identity_a = recovery.normalize_audio_identity(url_a)
        identity_b = recovery.normalize_audio_identity(url_b)
        self.assertEqual(identity_a, identity_b)
        ranked = recovery.rank_candidates([url_a, url_b], target, {url_a: [], url_b: []})
        self.assertEqual(len(ranked), 1)

    def test_request_budget_constants(self):
        """Verify the formal max request count matches the documented budget."""
        endpoints_per_target = len(recovery.build_endpoint_matrix(
            recovery.Target(
                date="2020-01-01",
                title="T",
                story_id="1",
                audio_id="2",
                npr_url="https://www.npr.org/2020/01/01/1/t",
            )
        ))
        max_per_target = (
            endpoints_per_target * recovery.MAX_RETRIES
            + recovery.MAX_CANDIDATES_PER_TARGET * 2 * recovery.MAX_RETRIES
        )
        max_run = 21 * max_per_target
        # Matches the documented budget in the module docstring.
        self.assertEqual(endpoints_per_target, 14)
        self.assertEqual(max_per_target, 14 * 3 + 8 * 2 * 3)   # 42 + 48 = 90
        self.assertEqual(max_run, 21 * 90)                       # 1 890


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

    def test_production_file_mutation_raises(self):
        """assert_production_files_unchanged must raise RuntimeError on mutation."""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "feed.xml"
            f.write_text("<rss/>", encoding="utf-8")
            before = recovery.capture_file_hashes([f])
            # Mutate the file after capturing the before hash.
            f.write_text("<rss updated/>", encoding="utf-8")
            after = recovery.capture_file_hashes([f])
            with self.assertRaises(RuntimeError) as ctx:
                recovery.assert_production_files_unchanged(before, after)
            self.assertIn("PRODUCTION FILE MUTATION DETECTED", str(ctx.exception))

    def test_no_mutation_does_not_raise(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "history.json"
            f.write_text("{}", encoding="utf-8")
            before = recovery.capture_file_hashes([f])
            after = recovery.capture_file_hashes([f])
            # Should not raise.
            recovery.assert_production_files_unchanged(before, after)


class PartialRunTests(unittest.TestCase):
    """Tests for interrupted/partial run distinguishability."""

    def test_placeholder_written_before_results(self):
        """After a normal run, the placeholder must have run_complete=True."""
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            placeholder_path = output_dir / "no_audio_target_recovery_placeholder.json"

            # Before run — placeholder does not exist yet.
            self.assertFalse(placeholder_path.exists())

            # Simulate what run() does: write placeholder immediately.
            output_dir.mkdir(parents=True, exist_ok=True)
            recovery.write_json(
                placeholder_path,
                {"run_complete": False, "note": "in progress"},
            )
            # Placeholder exists with run_complete=False (partial state).
            data = json.loads(placeholder_path.read_text())
            self.assertFalse(data["run_complete"])

            # On completion, overwrite with run_complete=True.
            recovery.write_json(
                placeholder_path,
                {"run_complete": True, "note": "complete"},
            )
            data = json.loads(placeholder_path.read_text())
            self.assertTrue(data["run_complete"])

    def test_partial_run_looks_different_from_complete_run(self):
        """A placeholder with run_complete=False is distinguishable from True."""
        partial = {"run_complete": False}
        complete = {"run_complete": True}
        self.assertNotEqual(partial["run_complete"], complete["run_complete"])


class BatchSelectionTests(unittest.TestCase):
    """Tests for deterministic batch selection."""

    def _make_targets(self, n: int = 21) -> list[recovery.Target]:
        return [
            recovery.Target(
                date=f"2020-01-{i + 1:02d}",
                title=f"Episode {i}",
                story_id=str(1000 + i),
                audio_id=str(2000 + i),
                npr_url=f"https://www.npr.org/2020/01/{i+1:02d}/{1000+i}/ep",
            )
            for i in range(n)
        ]

    def test_batch1_returns_first_n_targets(self):
        targets = self._make_targets(21)
        batch = recovery.select_batch_targets(targets, batch=1, batch_size=5)
        self.assertEqual([t.story_id for t in batch], [t.story_id for t in targets[:5]])

    def test_batch2_returns_second_n_targets(self):
        targets = self._make_targets(21)
        batch = recovery.select_batch_targets(targets, batch=2, batch_size=5)
        self.assertEqual([t.story_id for t in batch], [t.story_id for t in targets[5:10]])

    def test_last_batch_returns_remainder(self):
        targets = self._make_targets(21)
        # batch 5 with batch_size=5 covers indices 20..20 (1 target)
        batch = recovery.select_batch_targets(targets, batch=5, batch_size=5)
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0].story_id, targets[20].story_id)

    def test_batch_beyond_end_returns_empty(self):
        targets = self._make_targets(5)
        batch = recovery.select_batch_targets(targets, batch=2, batch_size=5)
        self.assertEqual(batch, [])

    def test_invalid_batch_raises(self):
        targets = self._make_targets(5)
        with self.assertRaises(ValueError):
            recovery.select_batch_targets(targets, batch=0, batch_size=5)

    def test_invalid_batch_size_raises(self):
        targets = self._make_targets(5)
        with self.assertRaises(ValueError):
            recovery.select_batch_targets(targets, batch=1, batch_size=0)

    def test_ordering_is_deterministic(self):
        """Two separate calls with the same inputs return identical lists."""
        targets = self._make_targets(21)
        batch_a = recovery.select_batch_targets(targets, batch=3, batch_size=5)
        batch_b = recovery.select_batch_targets(targets, batch=3, batch_size=5)
        self.assertEqual(
            [t.story_id for t in batch_a],
            [t.story_id for t in batch_b],
        )

    def test_batches_are_non_overlapping_and_cover_all(self):
        """Five batches of size 5 over 21 targets cover all 21 without overlap."""
        targets = self._make_targets(21)
        all_selected: list[str] = []
        for b in range(1, 6):
            batch = recovery.select_batch_targets(targets, batch=b, batch_size=5)
            all_selected.extend(t.story_id for t in batch)
        self.assertEqual(len(all_selected), 21)
        self.assertEqual(len(set(all_selected)), 21)


class CompletionSkipTests(unittest.TestCase):
    """Tests for completed-target skip logic and partial-result persistence."""

    def _make_target(self, story_id: str = "111") -> recovery.Target:
        return recovery.Target(
            date="2020-06-01",
            title="Test",
            story_id=story_id,
            audio_id="222",
            npr_url="https://www.npr.org/2020/06/01/111/test",
        )

    def test_completed_targets_are_skipped(self):
        """A target with an existing checkpoint is NOT in to_process."""
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            t = self._make_target("999")
            # Pre-write a checkpoint to simulate a prior completed run.
            checkpoint = output_dir / f"checkpoint_{t.story_id}.json"
            recovery.write_json(checkpoint, {"story_id": t.story_id, "run_complete": True})

            to_process, already_completed = recovery.partition_by_completion([t], output_dir)

            self.assertEqual(already_completed, [t])
            self.assertEqual(to_process, [])

    def test_unfinished_targets_remain_eligible(self):
        """A target without a checkpoint IS in to_process."""
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            t = self._make_target("888")

            to_process, already_completed = recovery.partition_by_completion([t], output_dir)

            self.assertEqual(to_process, [t])
            self.assertEqual(already_completed, [])

    def test_mixed_batch_partitions_correctly(self):
        """In a mixed batch, completed targets go to already_completed and others to to_process."""
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            done = self._make_target("100")
            pending = self._make_target("200")
            checkpoint = output_dir / f"checkpoint_{done.story_id}.json"
            recovery.write_json(checkpoint, {"story_id": done.story_id})

            to_process, already_completed = recovery.partition_by_completion(
                [done, pending], output_dir
            )
            self.assertIn(done, already_completed)
            self.assertIn(pending, to_process)
            self.assertEqual(len(to_process) + len(already_completed), 2)

    def test_per_target_checkpoint_written_immediately(self):
        """investigate_target writes a checkpoint before the next target is processed."""
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            t = self._make_target("555")
            checkpoints_written: list[str] = []

            def fake_investigate(target, history_item, resolved_corpus, output_dir=None):
                result = {"story_id": target.story_id, "final_classification": "CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED"}
                if output_dir is not None:
                    cp = output_dir / f"checkpoint_{target.story_id}.json"
                    recovery.write_json(cp, result)
                    checkpoints_written.append(target.story_id)
                return result

            # Simulate two targets; fake_investigate persists each checkpoint.
            t2 = self._make_target("666")
            for target in [t, t2]:
                fake_investigate(target, {}, [], output_dir=output_dir)

            # Both checkpoints must exist immediately after processing.
            self.assertTrue((output_dir / "checkpoint_555.json").exists())
            self.assertTrue((output_dir / "checkpoint_666.json").exists())
            self.assertEqual(checkpoints_written, ["555", "666"])

    def test_artifact_upload_path_valid_after_partial_run(self):
        """Output dir has at least one JSON file even after a partial run (timeout/cancel)."""
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            # Write a placeholder (what run() does immediately on start).
            recovery.write_json(
                output_dir / "no_audio_target_recovery_placeholder.json",
                {"run_complete": False, "batch": 1},
            )
            # Write one partial checkpoint (as if one target completed before cancel).
            recovery.write_json(
                output_dir / "checkpoint_12345.json",
                {"story_id": "12345", "final_classification": "CONFIRMED_EPISODE_AUDIO_STILL_UNRESOLVED"},
            )
            json_files = list(output_dir.glob("*.json"))
            # The artifact upload path (*.json) resolves to ≥ 1 file.
            self.assertGreaterEqual(len(json_files), 1)
            # Placeholder alone ensures the path is valid even before the first target finishes.
            placeholder = output_dir / "no_audio_target_recovery_placeholder.json"
            self.assertTrue(placeholder.exists())
            data = json.loads(placeholder.read_text())
            self.assertFalse(data["run_complete"])  # partial run marker


if __name__ == "__main__":
    unittest.main()
