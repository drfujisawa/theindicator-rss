import unittest

import recover_unresolved_indicator_consolidated as pipeline


class AudioClassificationTests(unittest.TestCase):
    def test_livestream_audio_is_rejected(self):
        result = pipeline.classify_audio_candidate(
            candidate_url=(
                "https://playerservices.streamtheworld.com/"
                "api/livestream-redirect/WYPR_HD1.mp3"
            ),
            final_url=(
                "https://playerservices.streamtheworld.com/"
                "api/livestream-redirect/WYPR_HD1.mp3"
            ),
            content_type="audio/mpeg",
            status_code=200,
        )

        self.assertEqual(result["status"], "rejected_livestream")
        self.assertFalse(result["accepted"])

    def test_non_npr_audio_is_rejected(self):
        result = pipeline.classify_audio_candidate(
            candidate_url="https://example.org/audio/episode.mp3",
            final_url="https://example.org/audio/episode.mp3",
            content_type="audio/mpeg",
            status_code=200,
        )

        self.assertEqual(result["status"], "rejected_non_npr_audio")
        self.assertFalse(result["accepted"])

    def test_indicator_ondemand_audio_is_accepted(self):
        result = pipeline.classify_audio_candidate(
            candidate_url=(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                "indicator/2018/10/example.mp3"
            ),
            final_url=(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                "indicator/2018/10/example.mp3"
            ),
            content_type="audio/mpeg",
            status_code=206,
        )

        self.assertEqual(
            result["status"],
            "validated_npr_episode_audio",
        )
        self.assertTrue(result["accepted"])

    def test_octet_stream_alone_is_rejected(self):
        result = pipeline.classify_audio_candidate(
            candidate_url=(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                "indicator/2018/10/example.mp3"
            ),
            final_url=(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                "indicator/2018/10/example.mp3"
            ),
            content_type="application/octet-stream",
            status_code=200,
        )

        self.assertEqual(result["status"], "rejected_non_audio_response")
        self.assertFalse(result["accepted"])

    def test_request_failure_is_rejected(self):
        result = pipeline.classify_audio_candidate(
            candidate_url=(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                "indicator/2018/10/example.mp3"
            ),
            final_url=(
                "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                "indicator/2018/10/example.mp3"
            ),
            content_type="audio/mpeg",
            status_code=None,
        )

        self.assertEqual(result["status"], "rejected_bad_response")
        self.assertFalse(result["accepted"])


class EpisodeClassificationTests(unittest.TestCase):
    def test_duplicate_episode_stays_probable_duplicate(self):
        ledger = pipeline.create_ledger({
            "date": "2018-03-12",
            "title": "Hurricane Joseph & The Calculator That Time Forgot",
            "duplicate_reference_dates": ["2018-08-28"],
        })

        status, explanation = pipeline.determine_episode_status(ledger)

        self.assertEqual(status, "probable_duplicate_rebroadcast")
        self.assertIn("duplicate/rebroadcast", explanation)

    def test_identity_without_working_audio_stays_unresolved(self):
        ledger = pipeline.create_ledger({
            "date": "2018-10-29",
            "title": "Judgement Bonds",
        })
        ledger["npr_story_ids"] = ["760000001"]
        ledger["npr_story_urls"] = [
            "https://www.npr.org/2018/10/29/760000001/judgement-bonds",
        ]
        ledger["validation_results"] = [
            {
                "validation_status": "rejected_non_npr_audio",
            }
        ]

        status, _ = pipeline.determine_episode_status(ledger)

        self.assertEqual(
            status,
            "identity_found_but_audio_unresolved",
        )

    def test_valid_audio_and_identity_confirms_recovery(self):
        ledger = pipeline.create_ledger({
            "date": "2018-10-29",
            "title": "Judgement Bonds",
        })
        ledger["npr_story_ids"] = ["760000001"]
        ledger["validation_results"] = [
            {
                "validation_status": "validated_npr_episode_audio",
                "candidate_url": (
                    "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                    "indicator/2018/10/example.mp3"
                ),
            }
        ]

        status, _ = pipeline.determine_episode_status(ledger)

        self.assertEqual(status, "confirmed_recovered")

    def test_valid_audio_without_identity_or_page_match_stays_unresolved(self):
        ledger = pipeline.create_ledger({
            "date": "2018-10-29",
            "title": "Judgement Bonds",
        })
        ledger["validation_results"] = [
            {
                "validation_status": "validated_npr_episode_audio",
                "candidate_url": (
                    "https://ondemand.npr.org/anon.npr-podcasts/podcast/npr/"
                    "indicator/2018/10/example.mp3"
                ),
            }
        ]

        status, _ = pipeline.determine_episode_status(ledger)

        self.assertNotEqual(status, "confirmed_recovered")

    def test_near_exact_title_without_date_does_not_qualify_page(self):
        page_match = pipeline.score_page_match(
            expected_title="Traffic And Tariffs",
            expected_date="2018-05-01",
            metadata={
                "og_title": "Traffic And Tariffs Again",
                "html_title": "Traffic And Tariffs Again",
                "dates": [],
            },
            url="https://www.wbur.org/news/2018/04/30/traffic-and-tariffs-again",
        )

        self.assertFalse(page_match["qualified"])

    def test_request_failure_in_validate_cannot_produce_recovery(self):
        ledger = pipeline.create_ledger({
            "date": "2018-10-29",
            "title": "Judgement Bonds",
        })
        ledger["npr_story_ids"] = ["760000001"]
        ledger["validation_results"] = [
            {
                "validation_status": "rejected_request_error",
                "reason": "Connection timed out",
            }
        ]

        status, _ = pipeline.determine_episode_status(ledger)

        self.assertNotEqual(status, "confirmed_recovered")


if __name__ == "__main__":
    unittest.main()
