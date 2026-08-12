#!/usr/bin/env python3
from pathlib import Path

import json
import unittest

from scripts.analysis import report_identity_audio_unresolved as report

REPO_ROOT = Path(__file__).resolve().parents[1]


def _target_episodes_from_ledger():
    ledger = report.load_json(report.INPUT_LEDGER_FILE)
    return [
        episode
        for episode in ledger.get("episodes", [])
        if episode.get("final_status") == report.TARGET_STATUS
    ]


class IdentityAudioUnresolvedReportTests(unittest.TestCase):
    def test_build_report_tracks_current_ledger_targets(self):
        payload = report.build_report(generated_at="2026-08-09T00:00:00+00:00")
        ledger_targets = _target_episodes_from_ledger()

        self.assertEqual(payload["target_episode_count"], len(ledger_targets))
        self.assertEqual(len(payload["episodes"]), len(ledger_targets))
        self.assertEqual(
            [episode["rank"] for episode in payload["episodes"]],
            list(range(1, len(ledger_targets) + 1)),
        )
        self.assertTrue(
            all(
                episode["final_classification"] == report.TARGET_STATUS
                for episode in payload["episodes"]
            )
        )

        payload_keys = {
            (episode["reference_date"], episode["reference_title"])
            for episode in payload["episodes"]
        }
        ledger_keys = {
            (episode["reference_date"], episode["reference_title"])
            for episode in ledger_targets
        }
        self.assertEqual(payload_keys, ledger_keys)

    def test_report_episodes_preserve_identity_and_validation_details(self):
        payload = report.build_report(generated_at="2026-08-09T00:00:00+00:00")
        ledger_by_key = {
            (episode["reference_date"], episode["reference_title"]): episode
            for episode in _target_episodes_from_ledger()
        }

        for episode in payload["episodes"]:
            key = (episode["reference_date"], episode["reference_title"])
            source = ledger_by_key[key]
            discovered = episode["npr_ids_found"]

            for story_id in source.get("npr_story_ids", []):
                self.assertIn(story_id, discovered["discovered_story_ids"])
            for player_story_id in source.get("npr_player_story_ids", []):
                self.assertIn(player_story_id, discovered["discovered_player_story_ids"])
            for audio_id in source.get("npr_audio_ids", []):
                self.assertIn(audio_id, discovered["discovered_audio_ids"])

            self.assertEqual(
                len(episode["audio_candidates_tested"]),
                len(source.get("validation_results", [])),
            )
            self.assertTrue(episode["identity_confidence"])
            self.assertTrue(episode["strongest_evidence"])

    def test_checked_in_report_file_is_valid_and_targeted(self):
        with open(
            report.BASE_DIR / report.OUTPUT_REPORT_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            checked_in = json.load(file)

        self.assertEqual(checked_in.get("target_status"), report.TARGET_STATUS)
        self.assertIsInstance(checked_in.get("episodes", []), list)


if __name__ == "__main__":
    unittest.main()
