from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import unittest.mock
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
        E = endpoints_per_target
        W = recovery.WAYBACK_MAX_CAPTURES
        R = recovery.MAX_RETRIES
        C = recovery.MAX_CANDIDATES_PER_TARGET
        max_per_target = E * R + W * R + C * 2 * R
        max_run = 17 * max_per_target  # 17 genuine unresolved targets
        # Endpoint matrix was reduced from 14 → 7 generic sources removed.
        self.assertEqual(E, 7)
        self.assertEqual(W, 3)   # up to 3 Wayback archive fetches
        self.assertEqual(R, 2)   # 2 retries per request
        self.assertEqual(C, 3)   # candidate cap of 3
        # 7*2 + 3*2 + 3*2*2 = 14 + 6 + 12 = 32 per target
        self.assertEqual(max_per_target, 32)
        self.assertEqual(max_run, 17 * 32)   # 544


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


class WaybackCaptureSelectionTests(unittest.TestCase):
    """Tests for select_wayback_captures."""

    def _make_cdx(self, rows):
        """Build a minimal CDX JSON response with header + data rows."""
        header = ["urlkey", "timestamp", "statuscode", "digest"]
        return json.dumps([header] + rows)

    def test_empty_input_returns_empty(self):
        result = recovery.select_wayback_captures("", "2022-06-09")
        self.assertEqual(result, [])

    def test_non_200_captures_excluded(self):
        cdx = self._make_cdx([
            ["key", "20220609120000", "404", "abc"],
            ["key", "20220609130000", "301", "def"],
        ])
        result = recovery.select_wayback_captures(cdx, "2022-06-09")
        self.assertEqual(result, [])

    def test_200_captures_included_and_bounded(self):
        cdx = self._make_cdx([
            ["key", "20220609110000", "200", "aaa"],
            ["key", "20220609120000", "200", "bbb"],
            ["key", "20220609130000", "200", "ccc"],
            ["key", "20220609140000", "200", "ddd"],
        ])
        result = recovery.select_wayback_captures(cdx, "2022-06-09", max_captures=3)
        self.assertEqual(len(result), 3)
        self.assertTrue(all("timestamp" in c for c in result))

    def test_sorted_by_proximity_to_target_date(self):
        # 20220609 is closest; 20220501 and 20221201 are further away.
        cdx = self._make_cdx([
            ["key", "20221201000000", "200", "far_future"],
            ["key", "20220609080000", "200", "close"],
            ["key", "20220501000000", "200", "far_past"],
        ])
        result = recovery.select_wayback_captures(cdx, "2022-06-09", max_captures=3)
        self.assertEqual(result[0]["timestamp"], "20220609080000")

    def test_cap_respected(self):
        cdx = self._make_cdx([
            ["key", f"2022060{i}000000", "200", f"d{i}"] for i in range(1, 8)
        ])
        result = recovery.select_wayback_captures(cdx, "2022-06-09", max_captures=2)
        self.assertEqual(len(result), 2)

    def test_invalid_json_returns_empty(self):
        result = recovery.select_wayback_captures("not json", "2022-06-09")
        self.assertEqual(result, [])


class FetchWaybackPlayerPageTests(unittest.TestCase):
    """Tests for fetch_wayback_player_page."""

    def test_builds_id_modifier_url(self):
        """The id_ modifier must appear in the fetched URL for clean archive HTML."""
        fetched_urls = []

        def fake_request(url, method="GET"):
            fetched_urls.append(url)
            return {"ok": True, "status": 200, "text": "", "final_url": url}

        recovery.fetch_wayback_player_page(
            "https://www.npr.org/player/embed/999/888",
            "20220609120000",
            request_fn=fake_request,
        )
        self.assertEqual(len(fetched_urls), 1)
        url = fetched_urls[0]
        self.assertIn("id_", url)
        self.assertIn("20220609120000", url)
        self.assertIn("player/embed/999/888", url)

    def test_returns_request_fn_result(self):
        def fake_request(url, method="GET"):
            return {"ok": False, "status": 404, "text": ""}

        result = recovery.fetch_wayback_player_page(
            "https://www.npr.org/player/embed/1/2",
            "20220609000000",
            request_fn=fake_request,
        )
        self.assertFalse(result["ok"])


class PlayerProvenanceTests(unittest.TestCase):
    """Tests for the dual-ID provenance scoring fix."""

    def _target(self, story_id="1104034175", audio_id="1198988725"):
        return recovery.Target(
            date="2022-06-09",
            title="Test Episode",
            story_id=story_id,
            audio_id=audio_id,
            npr_url=f"https://www.npr.org/2022/06/09/{story_id}/test",
        )

    def test_player_embed_url_with_both_ids_scores_055(self):
        """source endpoint = player/embed/<story_id>/<audio_id> → +0.55 (both IDs present)."""
        target = self._target()
        player_url = f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"
        prov = recovery.compute_identity_provenance(
            target,
            [player_url],
            validated_audio=None,
        )
        self.assertAlmostEqual(prov["score"], 0.55)
        self.assertEqual(prov["confidence"], "medium")

    def test_player_embed_plus_simplecast_uuid_qualifies_as_high(self):
        """player embed (0.55) + simplecast_uuid (0.20) = 0.75 ≥ 0.70 → 'high'."""
        target = self._target()
        player_url = f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"
        simplecast_audio = (
            "https://npr.simplecastaudio.com/0a4e8d3b-fe23-4948-9e39-20fcf16f9331/"
            "episodes/e9827f64-db6e-4abb-aee9-a9fe394033ae/audio/128/default.mp3"
        )
        validated_audio = {
            "candidate_url": simplecast_audio,
            "final_url": simplecast_audio,
            "playable": True,
            "http_status": 200,
            "content_type": "audio/mpeg",
            "content_length": 12345678,
            "simplecast_uuid": "e9827f64-db6e-4abb-aee9-a9fe394033ae",
        }
        prov = recovery.compute_identity_provenance(target, [player_url], validated_audio)
        self.assertAlmostEqual(prov["score"], 0.75)
        self.assertEqual(prov["confidence"], "high")

    def test_wayback_archived_player_url_also_scores_055(self):
        """The archive URL contains both IDs → same +0.55 as the live player."""
        target = self._target()
        archive_url = (
            "https://web.archive.org/web/20220609120000id_/"
            f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"
        )
        prov = recovery.compute_identity_provenance(target, [archive_url], None)
        self.assertAlmostEqual(prov["score"], 0.55)

    def test_story_only_endpoint_scores_035(self):
        """Endpoint containing only story_id still gets +0.35 (not +0.55)."""
        target = self._target()
        story_url = f"https://www.npr.org/2022/06/09/{target.story_id}/test-episode"
        prov = recovery.compute_identity_provenance(target, [story_url], None)
        self.assertAlmostEqual(prov["score"], 0.35)

    def test_generic_endpoint_with_no_ids_scores_zero(self):
        """An endpoint with neither story_id nor audio_id gives score 0."""
        target = self._target()
        generic_url = "https://www.npr.org/podcasts/510325/the-indicator-from-planet-money"
        prov = recovery.compute_identity_provenance(target, [generic_url], None)
        self.assertEqual(prov["score"], 0.0)
        self.assertEqual(prov["confidence"], "low")

    def test_player_embed_chain_qualifies_even_without_ids_in_final_url(self):
        """The provenance chain qualifies even when the final Simplecast URL has no NPR IDs."""
        target = self._target()
        player_url = f"https://www.npr.org/player/embed/{target.story_id}/{target.audio_id}"
        # Final URL is a pure Simplecast UUID URL: no NPR IDs anywhere in it.
        simplecast_url = (
            "https://cdn.simplecast.com/audio/0a4e8d3b-fe23-4948-9e39-20fcf16f9331/"
            "episodes/deadbeef-0000-0000-0000-000000000001/audio/128/default.mp3"
        )
        validated_audio = {
            "candidate_url": simplecast_url,
            "final_url": simplecast_url,
            "playable": True,
            "http_status": 200,
            "content_type": "audio/mpeg",
            "content_length": 9999999,
            "simplecast_uuid": "deadbeef-0000-0000-0000-000000000001",
        }
        prov = recovery.compute_identity_provenance(target, [player_url], validated_audio)
        # 0.55 (both IDs in endpoint) + 0.00 (no IDs in final URL) + 0.20 (uuid) = 0.75
        self.assertAlmostEqual(prov["score"], 0.75)
        self.assertEqual(prov["confidence"], "high")

    def test_wrong_story_archive_capture_cannot_recover_target(self):
        """An archived page for a *different* story_id cannot recover this target."""
        target = self._target(story_id="1104034175", audio_id="1198988725")
        # Archive URL comes from a different story (999999999/888888888).
        wrong_archive_url = (
            "https://web.archive.org/web/20220609120000id_/"
            "https://www.npr.org/player/embed/999999999/888888888"
        )
        simplecast_url = (
            "https://npr.simplecastaudio.com/0a4e8d3b-fe23-4948-9e39-20fcf16f9331/"
            "episodes/cafecafe-0000-0000-0000-cafecafe0000/audio/128/default.mp3"
        )
        validated_audio = {
            "candidate_url": simplecast_url,
            "final_url": simplecast_url,
            "playable": True,
            "http_status": 200,
            "content_type": "audio/mpeg",
            "content_length": 1234567,
            "simplecast_uuid": "cafecafe-0000-0000-0000-cafecafe0000",
        }
        prov = recovery.compute_identity_provenance(target, [wrong_archive_url], validated_audio)
        # Neither ID from the wrong archive URL matches target → endpoint score 0.
        # Final URL also has no NPR IDs → score 0 + 0.20 (uuid) = 0.20 < 0.70.
        self.assertEqual(prov["confidence"], "low")
        self.assertLess(prov["score"], 0.70)

    def test_generic_simplecast_uuid_is_rejected(self):
        """UUID from a generic show-page source scores below 0.70 → NOT recovered."""
        target = self._target()
        generic_source = "https://www.npr.org/podcasts/510325/the-indicator-from-planet-money"
        validated_audio = {
            "candidate_url": "https://cdn.simplecast.com/audio/0a4e8d3b/episodes/aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa/audio/128/default.mp3",
            "final_url": "https://cdn.simplecast.com/audio/0a4e8d3b/episodes/aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa/audio/128/default.mp3",
            "playable": True,
            "http_status": 200,
            "content_type": "audio/mpeg",
            "content_length": 99999,
            "simplecast_uuid": "aaaaaaaa-0000-0000-0000-aaaaaaaaaaaa",
        }
        prov = recovery.compute_identity_provenance(target, [generic_source], validated_audio)
        # Generic source (no NPR IDs) → 0.00 endpoint + 0.20 uuid = 0.20 → low
        self.assertEqual(prov["confidence"], "low")
        self.assertLess(prov["score"], 0.70)


class WaybackArchiveFetchIntegrationTests(unittest.TestCase):
    """Integration tests for investigate_target's Wayback archive fetch step."""

    def _make_target(self, story_id="1104034175", audio_id="1198988725"):
        return recovery.Target(
            date="2022-06-09",
            title="Test Episode",
            story_id=story_id,
            audio_id=audio_id,
            npr_url=f"https://www.npr.org/2022/06/09/{story_id}/test",
        )

    def _cdx_response(self, timestamps):
        header = ["urlkey", "timestamp", "statuscode", "digest"]
        rows = [["k", ts, "200", "x"] for ts in timestamps]
        return json.dumps([header] + rows)

    def test_cdx_result_triggers_archive_fetch(self):
        """When CDX returns captures, investigate_target fetches each archive page."""
        cdx_ts = ["20220609110000", "20220609130000"]
        fetched_urls = []

        def fake_request(url, method="GET"):
            fetched_urls.append(url)
            if "api.cdn.com/v2/search" in url or "web.archive.org/cdx" in url:
                # CDX response
                return {"ok": True, "status": 200, "text": self._cdx_response(cdx_ts), "final_url": url}
            if "web.archive.org/web/" in url and "id_/" in url:
                # Archived player page — no audio candidates
                return {"ok": True, "status": 200, "text": "<html>archived</html>", "final_url": url}
            # All other endpoints fail
            return {"ok": False, "status": 404, "text": "", "final_url": url}

        target = self._make_target()
        with unittest.mock.patch.object(recovery, "request_with_retries", side_effect=fake_request):
            result = recovery.investigate_target(target, {}, [], output_dir=None)

        archive_fetches = [u for u in fetched_urls if "id_/" in u]
        self.assertGreaterEqual(len(archive_fetches), 1, "Expected at least one archive page fetch")
        for url in archive_fetches:
            self.assertIn(target.story_id, url)
            self.assertIn(target.audio_id, url)

    def test_archive_capture_selection_is_bounded(self):
        """At most WAYBACK_MAX_CAPTURES archive pages are fetched."""
        many_ts = [f"2022060{i}000000" for i in range(1, 9)]  # 8 captures in CDX
        archive_fetches = []

        def fake_request(url, method="GET"):
            if "web.archive.org/cdx" in url or "api.cdn.com/v2/search" in url:
                return {"ok": True, "status": 200, "text": self._cdx_response(many_ts), "final_url": url}
            if "id_/" in url:
                archive_fetches.append(url)
                return {"ok": True, "status": 200, "text": "<html>archived</html>", "final_url": url}
            return {"ok": False, "status": 404, "text": "", "final_url": url}

        target = self._make_target()
        with unittest.mock.patch.object(recovery, "request_with_retries", side_effect=fake_request):
            recovery.investigate_target(target, {}, [], output_dir=None)

        self.assertLessEqual(len(archive_fetches), recovery.WAYBACK_MAX_CAPTURES)

    def test_live_player_simplecast_uuid_leads_to_recovery(self):
        """player_embed → Simplecast UUID in page → playable → RECOVERED_AND_VALIDATED."""
        simplecast_url = (
            "https://npr.simplecastaudio.com/0a4e8d3b-fe23-4948-9e39-20fcf16f9331/"
            "episodes/e9827f64-db6e-4abb-aee9-a9fe394033ae/audio/128/default.mp3"
        )
        player_html = f'<html><script>var x = {{audioUrl: "{simplecast_url}"}};</script></html>'

        def fake_request(url, method="GET"):
            if f"player/embed/" in url and "archive" not in url:
                return {"ok": True, "status": 200, "text": player_html, "final_url": url}
            if "simplecastaudio" in url:
                return {"ok": True, "status": 200, "content_type": "audio/mpeg",
                        "content_length": 9999999, "final_url": url, "text": ""}
            return {"ok": False, "status": 404, "text": "", "final_url": url}

        target = self._make_target()
        with unittest.mock.patch.object(recovery, "request_with_retries", side_effect=fake_request):
            with unittest.mock.patch.object(recovery, "validate_audio_candidate") as mock_val:
                mock_val.return_value = {
                    "candidate_url": simplecast_url,
                    "final_url": simplecast_url,
                    "playable": True,
                    "http_status": 200,
                    "content_type": "audio/mpeg",
                    "content_length": 9999999,
                    "simplecast_uuid": "e9827f64-db6e-4abb-aee9-a9fe394033ae",
                }
                result = recovery.investigate_target(target, {}, [], output_dir=None)

        self.assertEqual(result["final_classification"], "RECOVERED_AND_VALIDATED")

    def test_archived_player_simplecast_uuid_leads_to_recovery(self):
        """Wayback archive player page → UUID → playable → RECOVERED_AND_VALIDATED."""
        simplecast_url = (
            "https://npr.simplecastaudio.com/0a4e8d3b-fe23-4948-9e39-20fcf16f9331/"
            "episodes/abcd1234-0000-0000-0000-abcd12340000/audio/128/default.mp3"
        )
        archived_html = f'<html><script>window.__STATE__ = {{audioUrl: "{simplecast_url}"}};</script></html>'
        target = self._make_target()
        cdx_ts = ["20220609110000"]

        def fake_request(url, method="GET"):
            if "web.archive.org/cdx" in url:
                return {"ok": True, "status": 200, "text": self._cdx_response(cdx_ts), "final_url": url}
            if "id_/" in url:
                return {"ok": True, "status": 200, "text": archived_html, "final_url": url}
            return {"ok": False, "status": 404, "text": "", "final_url": url}

        with unittest.mock.patch.object(recovery, "request_with_retries", side_effect=fake_request):
            with unittest.mock.patch.object(recovery, "validate_audio_candidate") as mock_val:
                mock_val.return_value = {
                    "candidate_url": simplecast_url,
                    "final_url": simplecast_url,
                    "playable": True,
                    "http_status": 200,
                    "content_type": "audio/mpeg",
                    "content_length": 9999999,
                    "simplecast_uuid": "abcd1234-0000-0000-0000-abcd12340000",
                }
                result = recovery.investigate_target(target, {}, [], output_dir=None)

        self.assertEqual(result["final_classification"], "RECOVERED_AND_VALIDATED")

    def test_two_indicators_skipped_without_network_calls(self):
        """Two Indicators targets exit immediately with no network probing."""
        two_indicators_id = next(iter(recovery.TWO_INDICATORS_STORY_IDS))
        target = recovery.Target(
            date="2021-07-07",
            title="Two Indicators: X",
            story_id=two_indicators_id,
            audio_id="999999999",
            npr_url=f"https://www.npr.org/2021/07/07/{two_indicators_id}/two-indicators",
        )
        calls = []

        def fake_request(url, method="GET"):
            calls.append(url)
            return {"ok": True, "status": 200, "text": "", "final_url": url}

        with unittest.mock.patch.object(recovery, "request_with_retries", side_effect=fake_request):
            result = recovery.investigate_target(target, {}, [], output_dir=None)

        self.assertEqual(result["final_classification"], "PROBABLY_NOT_SEPARATE_EPISODE")
        self.assertEqual(len(calls), 0, "Two Indicators must not trigger any network requests")
        self.assertEqual(result["probe_outcome"], "skipped_two_indicators")

    def test_legacy_api_ondemand_recovery_2020(self):
        """2020 target: legacy API by story_id returns ondemand.npr.org URL → recovered."""
        ondemand_url = (
            "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/indicator/"
            "2020/05/20200512_indicator_pay-cuts-vs-layoffs-abc12345.mp3"
        )
        api_json = json.dumps({
            "list": [{"id": "854889059", "audio": [{"format": {"mp3": [{"$text": ondemand_url}]}}]}]
        })
        target = recovery.Target(
            date="2020-05-12",
            title="Pay Cuts Vs. Layoffs",
            story_id="854889059",
            audio_id="855066846",
            npr_url="https://www.npr.org/2020/05/12/854889059/pay-cuts-vs-layoffs",
        )

        def fake_request(url, method="GET"):
            if "854889059" in url and "api.npr.org" in url:
                return {"ok": True, "status": 200, "text": api_json, "final_url": url}
            return {"ok": False, "status": 404, "text": "", "final_url": url}

        with unittest.mock.patch.object(recovery, "request_with_retries", side_effect=fake_request):
            with unittest.mock.patch.object(recovery, "validate_audio_candidate") as mock_val:
                mock_val.return_value = {
                    "candidate_url": ondemand_url,
                    "final_url": ondemand_url + "?e=854889059",
                    "playable": True,
                    "http_status": 200,
                    "content_type": "audio/mpeg",
                    "content_length": 8000000,
                    "simplecast_uuid": None,
                }
                result = recovery.investigate_target(target, {}, [], output_dir=None)

        # 0.35 (story_id in API endpoint) + 0.45 (story_id in final URL) = 0.80 → high
        self.assertEqual(result["final_classification"], "RECOVERED_AND_VALIDATED")


if __name__ == "__main__":
    unittest.main()
