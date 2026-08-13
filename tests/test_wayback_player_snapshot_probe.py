"""Unit tests for probe_wayback_player_snapshot.py

These tests exercise all logic that does not require live network access:
  - target loading and Two Indicators exclusion
  - player URL construction
  - CDX response parsing
  - Simplecast UUID / audio URL extraction from player HTML
  - production-file guard
  - summary counters
"""
from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from scripts.recovery import probe_wayback_player_snapshot as probe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_enclosure_map(episodes: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
        "episodes": {ep["story_id"]: ep for ep in episodes},
    }


def _no_audio_ep(story_id: str, audio_id: str, date: str, title: str) -> dict:
    return {
        "story_id": story_id,
        "audio_id": audio_id,
        "date": date,
        "title": title,
        "status": "no_audio",
        "npr_url": f"https://www.npr.org/sections/money/{date}/{story_id}/{title.lower().replace(' ', '-')}",
    }


def _resolved_ep(story_id: str) -> dict:
    return {
        "story_id": story_id,
        "audio_id": "999",
        "date": "2022-01-01",
        "title": "Resolved",
        "status": "resolved",
        "enclosure_url": "https://example.com/audio.mp3",
    }


# ---------------------------------------------------------------------------
# Target loading
# ---------------------------------------------------------------------------

class TestLoadTargets(unittest.TestCase):
    def test_loads_no_audio_episodes_only(self):
        episodes = [
            _no_audio_ep("111", "AAA", "2022-01-01", "Episode A"),
            _no_audio_ep("222", "BBB", "2022-01-02", "Episode B"),
            _resolved_ep("333"),
        ]
        map_data = _make_enclosure_map(episodes)
        with TemporaryDirectory() as tmp:
            map_path = Path(tmp) / "indicator_enclosure_map.json"
            map_path.write_text(json.dumps(map_data), encoding="utf-8")
            with patch.object(probe, "ENCLOSURE_MAP", map_path):
                targets = probe.load_targets()
        self.assertEqual(len(targets), 2)
        story_ids = {t["story_id"] for t in targets}
        self.assertIn("111", story_ids)
        self.assertIn("222", story_ids)
        self.assertNotIn("333", story_ids)

    def test_excludes_two_indicators_story_ids(self):
        TWO_IDS = list(probe.TWO_INDICATORS_STORY_IDS)
        episodes = [
            _no_audio_ep(TWO_IDS[0], "AAA", "2022-01-01", "Two Indicators 1"),
            _no_audio_ep(TWO_IDS[1], "BBB", "2022-01-02", "Two Indicators 2"),
            _no_audio_ep("555", "CCC", "2022-01-03", "Regular Episode"),
        ]
        map_data = _make_enclosure_map(episodes)
        with TemporaryDirectory() as tmp:
            map_path = Path(tmp) / "indicator_enclosure_map.json"
            map_path.write_text(json.dumps(map_data), encoding="utf-8")
            with patch.object(probe, "ENCLOSURE_MAP", map_path):
                targets = probe.load_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["story_id"], "555")

    def test_sorted_by_date_then_story_id(self):
        episodes = [
            _no_audio_ep("300", "AAA", "2022-01-03", "C"),
            _no_audio_ep("100", "BBB", "2022-01-01", "A"),
            _no_audio_ep("200", "CCC", "2022-01-02", "B"),
        ]
        map_data = _make_enclosure_map(episodes)
        with TemporaryDirectory() as tmp:
            map_path = Path(tmp) / "indicator_enclosure_map.json"
            map_path.write_text(json.dumps(map_data), encoding="utf-8")
            with patch.object(probe, "ENCLOSURE_MAP", map_path):
                targets = probe.load_targets()
        self.assertEqual(
            [t["story_id"] for t in targets], ["100", "200", "300"]
        )


# ---------------------------------------------------------------------------
# Player URL construction
# ---------------------------------------------------------------------------

class TestPlayerUrl(unittest.TestCase):
    def test_player_url_format(self):
        """probe_target constructs player/embed/<story_id>/<audio_id>."""
        calls = []

        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            calls.append(url)
            # CDX returns empty list (no captures)
            if "cdx/search" in url:
                return {"ok": True, "text": "[]", "final_url": url, "http_status": 200}
            return {"ok": False, "error": "not called", "text": ""}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            result = probe.probe_target(
                story_id="1104792247",
                audio_id="1198988717",
                date="2022-06-13",
                title="Test Episode",
            )

        expected_player_url = (
            "https://www.npr.org/player/embed/1104792247/1198988717"
        )
        self.assertEqual(result["player_url"], expected_player_url)
        # CDX URL must reference the exact player URL
        cdx_call = next((u for u in calls if "cdx/search" in u), None)
        self.assertIsNotNone(cdx_call)
        self.assertIn("1104792247", cdx_call)
        self.assertIn("1198988717", cdx_call)


# ---------------------------------------------------------------------------
# CDX parsing
# ---------------------------------------------------------------------------

class TestParseCdx(unittest.TestCase):
    def _cdx_json(self, timestamps: list[str]) -> str:
        header = ["timestamp", "original", "statuscode", "mimetype", "digest"]
        rows = [header] + [
            [ts, "https://www.npr.org/player/embed/X/Y", "200", "text/html", "sha1:ABC"]
            for ts in timestamps
        ]
        return json.dumps(rows)

    def test_returns_empty_for_empty_response(self):
        result = probe._parse_cdx("[]", "2022-06-13")
        self.assertEqual(result, [])

    def test_returns_empty_for_malformed_json(self):
        result = probe._parse_cdx("not-json", "2022-06-13")
        self.assertEqual(result, [])

    def test_parses_timestamps_sorted_by_proximity(self):
        # target date: 2022-06-13 → 20220613
        # distant: 20200101, close: 20220614, exact: 20220613
        cdx = self._cdx_json(["20200101120000", "20220614080000", "20220613100000"])
        result = probe._parse_cdx(cdx, "2022-06-13")
        # First should be the exact date match (distance=0), then next-day, then old
        self.assertEqual(result[0], "20220613100000")
        self.assertEqual(result[1], "20220614080000")
        self.assertEqual(result[2], "20200101120000")

    def test_caps_at_wayback_max_captures(self):
        many = [f"2022060{i}120000" for i in range(9)]
        cdx = self._cdx_json(many)
        result = probe._parse_cdx(cdx, "2022-06-04")
        self.assertLessEqual(len(result), probe.WAYBACK_MAX_CAPTURES)

    def test_filters_non_200_status_codes(self):
        header = ["timestamp", "original", "statuscode", "mimetype", "digest"]
        rows = [
            header,
            ["20220613100000", "url", "301", "text/html", "sha1:A"],
            ["20220614100000", "url", "404", "text/html", "sha1:B"],
            ["20220615100000", "url", "200", "text/html", "sha1:C"],
        ]
        result = probe._parse_cdx(json.dumps(rows), "2022-06-13")
        self.assertEqual(result, ["20220615100000"])


# ---------------------------------------------------------------------------
# HTML extraction — strict player HTML scope
# ---------------------------------------------------------------------------

class TestExtractFromPlayerHtml(unittest.TestCase):
    def test_extracts_simplecast_uuid_from_episode_id_key(self):
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        html = f'{{"episodeId": "{uuid}", "title": "Test"}}'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_uuids"])
        self.assertIn(uuid, result["episode_id_key_uuids"])

    def test_extracts_simplecast_uuid_from_episode_uuid_key(self):
        uuid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        html = f'"episodeUuid": "{uuid}"'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_uuids"])

    def test_extracts_simplecast_audio_url_with_uuid(self):
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        audio_url = f"https://cdn.simplecastaudio.com/pod/{uuid}/audio/upload/test.mp3"
        html = f'<script>window.playerData = {{audioUrl: "{audio_url}"}};</script>'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_uuids"])
        self.assertTrue(
            any(uuid in u for u in result["simplecast_audio_urls"])
        )

    def test_extracts_uuid_near_simplecast_keyword(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        html = f'data-simplecast-episode="{uuid}" other="stuff"'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["uuid_near_simplecast"])
        self.assertIn(uuid, result["simplecast_uuids"])

    def test_does_not_extract_uuid_far_from_simplecast(self):
        """A UUID that is more than 120 chars from the word 'simplecast' should
        NOT appear in uuid_near_simplecast."""
        uuid = "aaaabbbb-cccc-dddd-eeee-111111111111"
        far_uuid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        # Place far_uuid 300 chars away from simplecast
        html = f'data-simplecast-x="{uuid}" {"x" * 300} other="{far_uuid}"'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["uuid_near_simplecast"])
        self.assertNotIn(far_uuid, result["uuid_near_simplecast"])

    def test_empty_html_returns_empty_results(self):
        result = probe.extract_from_player_html("")
        self.assertEqual(result["simplecast_uuids"], [])
        self.assertEqual(result["simplecast_audio_urls"], [])
        self.assertEqual(result["uuid_near_simplecast"], [])
        self.assertEqual(result["episode_id_key_uuids"], [])

    def test_deduplicates_extracted_uuids(self):
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        html = (
            f'"episodeId": "{uuid}"\n'
            f'"episodeUuid": "{uuid}"\n'
            f'data-simplecast-ep="{uuid}"'
        )
        result = probe.extract_from_player_html(html)
        self.assertEqual(result["simplecast_uuids"].count(uuid), 1)

    def test_simplecast_audio_url_without_uuid_still_captured(self):
        audio_url = "https://cdn.simplecastaudio.com/pod/audio/test.mp3"
        html = f'<audio src="{audio_url}"></audio>'
        result = probe.extract_from_player_html(html)
        self.assertIn(audio_url, result["simplecast_audio_urls"])

    def test_case_insensitive_key_matching(self):
        uuid = "aabbccdd-aabb-aabb-aabb-aabbccddeeff"
        html = f'"EPISODEID": "{uuid}"'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_uuids"])

    def test_real_world_known_good_pattern(self):
        """Simulate the June 22 known-good player bootstrap pattern."""
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        html = textwrap.dedent(f"""
            <script id="__NEXT_DATA__" type="application/json">
            {{
              "props": {{
                "pageProps": {{
                  "episode": {{
                    "episodeId": "{uuid}",
                    "title": "The price of free stock trading",
                    "audioUrl": "https://cdn.simplecastaudio.com/pod/{uuid}/audio/upload/test.mp3"
                  }}
                }}
              }}
            }}
            </script>
        """)
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_uuids"])
        self.assertIn(uuid, result["episode_id_key_uuids"])
        self.assertTrue(any(uuid in u for u in result["simplecast_audio_urls"]))


# ---------------------------------------------------------------------------
# probe_target integration (mocked network)
# ---------------------------------------------------------------------------

class TestProbeTarget(unittest.TestCase):
    def _cdx_with_one_capture(self, ts: str = "20220613100000") -> str:
        return json.dumps([
            ["timestamp", "original", "statuscode", "mimetype", "digest"],
            [ts, "https://www.npr.org/player/embed/X/Y", "200", "text/html", "sha1:A"],
        ])

    def test_no_captures_sets_recovery_status(self):
        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            if "cdx/search" in url:
                return {"ok": True, "text": "[]", "final_url": url}
            return {"ok": False, "error": "unexpected", "text": ""}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            result = probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
            )
        self.assertEqual(result["recovery_status"], "no_captures")
        self.assertEqual(result["cdx_capture_count"], 0)
        self.assertIsNone(result["best_simplecast_uuid"])

    def test_uuid_found_in_archived_html(self):
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        player_html = f'"episodeId": "{uuid}"'

        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            if "cdx/search" in url:
                return {"ok": True, "text": self._cdx_with_one_capture(), "final_url": url}
            # Archived player page
            return {"ok": True, "text": player_html, "final_url": url,
                    "http_status": 200, "content_type": "text/html"}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            result = probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
            )

        self.assertEqual(result["recovery_status"], "uuid_found")
        self.assertEqual(result["best_simplecast_uuid"], uuid)

    def test_audio_url_found_without_uuid_key(self):
        audio_url = "https://cdn.simplecastaudio.com/pod/audio/test.mp3"
        player_html = f'<audio src="{audio_url}"></audio>'

        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            if "cdx/search" in url:
                return {"ok": True, "text": self._cdx_with_one_capture(), "final_url": url}
            return {"ok": True, "text": player_html, "final_url": url,
                    "http_status": 200, "content_type": "text/html"}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="Test",
            )

        self.assertEqual(result["recovery_status"], "audio_url_found")
        self.assertIsNotNone(result["best_simplecast_audio_url"])

    def test_not_found_when_html_has_no_simplecast(self):
        player_html = "<html><body>No audio here</body></html>"

        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            if "cdx/search" in url:
                return {"ok": True, "text": self._cdx_with_one_capture(), "final_url": url}
            return {"ok": True, "text": player_html, "final_url": url,
                    "http_status": 200, "content_type": "text/html"}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="Test",
            )

        self.assertEqual(result["recovery_status"], "not_found")
        self.assertIsNone(result["best_simplecast_uuid"])

    def test_cdx_error_recorded(self):
        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            return {"ok": False, "error": "network timeout", "text": ""}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="Test",
            )

        self.assertEqual(result["recovery_status"], "cdx_error")
        self.assertIsNotNone(result["cdx_error"])

    def test_fetch_error_on_archived_page_recorded_in_snapshot(self):
        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            if "cdx/search" in url:
                return {"ok": True, "text": self._cdx_with_one_capture(), "final_url": url}
            return {"ok": False, "error": "connection refused", "text": ""}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="Test",
            )

        self.assertEqual(len(result["snapshots"]), 1)
        self.assertEqual(result["snapshots"][0]["fetch_status"], "error")
        self.assertEqual(result["recovery_status"], "not_found")

    def test_archived_url_uses_id_modifier(self):
        """Archived page must be fetched with the id_/ modifier."""
        fetched_urls: list[str] = []

        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES) -> dict:
            fetched_urls.append(url)
            if "cdx/search" in url:
                return {"ok": True, "text": self._cdx_with_one_capture("20220613120000"), "final_url": url}
            return {"ok": True, "text": "<html></html>", "final_url": url,
                    "http_status": 200, "content_type": "text/html"}

        with patch.object(probe, "_fetch", side_effect=fake_fetch):
            probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
            )

        archived_calls = [u for u in fetched_urls if "web.archive.org/web/" in u]
        self.assertTrue(len(archived_calls) >= 1)
        for url in archived_calls:
            self.assertIn("id_/", url, f"Missing id_/ modifier in: {url}")
            self.assertIn("1104792247", url)
            self.assertIn("1198988717", url)


# ---------------------------------------------------------------------------
# Production-file guard
# ---------------------------------------------------------------------------

class TestProductionFileGuard(unittest.TestCase):
    def test_raises_when_file_changes(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed.xml"
            path.write_bytes(b"original")
            before = {str(path): probe._hash_file(path)}
            path.write_bytes(b"mutated")
            after = {str(path): probe._hash_file(path)}
            with self.assertRaises(RuntimeError) as ctx:
                probe._assert_production_unchanged(before, after)
            self.assertIn("PRODUCTION FILE MUTATION", str(ctx.exception))

    def test_passes_when_files_unchanged(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "feed.xml"
            path.write_bytes(b"original")
            before = {str(path): probe._hash_file(path)}
            after = {str(path): probe._hash_file(path)}
            # Should not raise
            probe._assert_production_unchanged(before, after)

    def test_missing_file_handled_as_missing(self):
        path = Path("/nonexistent/path/file.xml")
        h = probe._hash_file(path)
        self.assertEqual(h, "__missing__")


# ---------------------------------------------------------------------------
# Scope: exactly 17 targets expected from production enclosure map
# ---------------------------------------------------------------------------

class TestProductionTargetCount(unittest.TestCase):
    def test_production_map_yields_17_targets(self):
        """The real indicator_enclosure_map.json must produce exactly 17 targets."""
        if not probe.ENCLOSURE_MAP.exists():
            self.skipTest("indicator_enclosure_map.json not found")
        targets = probe.load_targets()
        self.assertEqual(
            len(targets),
            probe.EXPECTED_TARGET_COUNT,
            f"Expected {probe.EXPECTED_TARGET_COUNT} confirmed unresolved targets, got {len(targets)}",
        )

    def test_none_of_the_17_are_two_indicators(self):
        if not probe.ENCLOSURE_MAP.exists():
            self.skipTest("indicator_enclosure_map.json not found")
        targets = probe.load_targets()
        for t in targets:
            self.assertNotIn(
                t["story_id"],
                probe.TWO_INDICATORS_STORY_IDS,
                f"Two Indicators story_id {t['story_id']} must be excluded",
            )


if __name__ == "__main__":
    unittest.main()
