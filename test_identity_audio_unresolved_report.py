import json
import unittest

import report_identity_audio_unresolved as report


class IdentityAudioUnresolvedReportTests(unittest.TestCase):
    def test_build_report_contains_ranked_target_episodes(self):
        payload = report.build_report(generated_at="2026-08-09T00:00:00+00:00")

        self.assertEqual(payload["target_episode_count"], 12)
        self.assertEqual(
            [episode["rank"] for episode in payload["episodes"]],
            list(range(1, 13)),
        )
        self.assertTrue(
            all(
                episode["final_classification"]
                == "identity_found_but_audio_unresolved"
                for episode in payload["episodes"]
            )
        )

    def test_report_surfaces_rejected_identity_chain_details(self):
        payload = report.build_report(generated_at="2026-08-09T00:00:00+00:00")
        by_title = {
            episode["reference_title"]: episode
            for episode in payload["episodes"]
        }

        fed = by_title["Fed Accounts For All!"]
        self.assertIn(
            "https://www.npr.org/player/embed/129451895/129454071",
            fed["player_urls"],
        )
        self.assertIn("129451895", fed["npr_ids_found"]["discovered_story_ids"])
        self.assertTrue(
            any(
                row["id"] == "129451895"
                for row in fed["npr_ids_found"]["rejected_or_unverified_ids"]
            )
        )

        whos_hiring = by_title["Who's Hiring?"]
        self.assertIn("408289115", whos_hiring["npr_ids_found"]["discovered_story_ids"])
        self.assertEqual(whos_hiring["identity_confidence"], "very_low")
        self.assertTrue(
            any(
                candidate["validation_status"] == "rejected_request_error"
                for candidate in whos_hiring["audio_candidates_tested"]
            )
        )

    def test_checked_in_report_matches_builder_output(self):
        expected = report.build_report(generated_at="CHECKED_IN")

        with open(report.OUTPUT_REPORT_FILE, "r", encoding="utf-8") as file:
            checked_in = json.load(file)

        checked_in["generated_at"] = "CHECKED_IN"
        self.assertEqual(checked_in, expected)


if __name__ == "__main__":
    unittest.main()
