"""Unit tests for probe_wayback_player_snapshot.py

These tests exercise all logic that does not require live network access:
  - target loading and Two Indicators exclusion
  - target filtering by story ID (--story-id / --targets)
  - player URL construction
  - CDX response parsing
  - Simplecast episode UUID extraction (strict — show UUID + random UUIDs rejected)
  - legacy audio URL extraction (ondemand.npr.org / podtrac)
  - live/generic MP3 rejection
  - playable-audio validation (HEAD+GET, 200/206, audio MIME)
  - classification semantics (UUID alone is never RECOVERED_AND_VALIDATED)
  - checkpoint creation and resume behaviour
  - request budget bounds
  - production-file guard
"""
from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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


def _cdx_one_capture(ts: str = "20220613100000") -> str:
    return json.dumps([
        ["timestamp", "original", "statuscode", "mimetype", "digest"],
        [ts, "https://www.npr.org/player/embed/X/Y", "200", "text/html", "sha1:A"],
    ])


def _make_fetch(
    cdx_text: str = "[]",
    page_html: str = "<html></html>",
    audio_ok: bool = False,
    audio_status: int = 200,
    audio_content_type: str = "audio/mpeg",
) -> "callable":
    """Build a fake fetch function for use as probe_target fetch_fn."""
    def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES, method: str = "GET") -> dict:
        if "cdx/search" in url:
            return {"ok": True, "text": cdx_text, "final_url": url, "http_status": 200,
                    "content_type": "application/json", "content_length": None}
        if "web.archive.org/web/" in url:
            return {"ok": True, "text": page_html, "final_url": url,
                    "http_status": 200, "content_type": "text/html", "content_length": None}
        # Audio validation request (candidate URL)
        if audio_ok:
            return {"ok": True, "final_url": url, "http_status": audio_status,
                    "content_type": audio_content_type, "content_length": "1234567",
                    "text": ""}
        return {"ok": False, "error": "HTTPError 404: Not Found", "text": ""}
    return fake_fetch


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
# Explicit target selection (--story-id / --targets)
# ---------------------------------------------------------------------------

class TestTargetSelection(unittest.TestCase):
    def _map_with_five(self) -> tuple[dict, Path]:
        FIVE = ["1104792247", "1105092405", "1105388082", "1105707030", "1105986237"]
        extra = ["9999991", "9999992"]
        episodes = [
            _no_audio_ep(sid, str(int(sid) + 1), f"2022-0{i+1}-01", f"Ep {sid}")
            for i, sid in enumerate(FIVE + extra)
        ]
        return _make_enclosure_map(episodes), FIVE

    def test_exact_five_target_selection(self):
        """load_targets(story_ids=FIVE) returns exactly the five requested targets."""
        map_data, FIVE = self._map_with_five()
        with TemporaryDirectory() as tmp:
            map_path = Path(tmp) / "indicator_enclosure_map.json"
            map_path.write_text(json.dumps(map_data), encoding="utf-8")
            with patch.object(probe, "ENCLOSURE_MAP", map_path):
                targets = probe.load_targets(story_ids=FIVE)
        self.assertEqual(len(targets), 5)
        returned_ids = {t["story_id"] for t in targets}
        self.assertEqual(returned_ids, set(FIVE))

    def test_story_ids_filter_excludes_others(self):
        """Targets not in story_ids are excluded even if they are no_audio."""
        map_data, FIVE = self._map_with_five()
        with TemporaryDirectory() as tmp:
            map_path = Path(tmp) / "indicator_enclosure_map.json"
            map_path.write_text(json.dumps(map_data), encoding="utf-8")
            with patch.object(probe, "ENCLOSURE_MAP", map_path):
                targets = probe.load_targets(story_ids=FIVE)
        returned_ids = {t["story_id"] for t in targets}
        self.assertNotIn("9999991", returned_ids)
        self.assertNotIn("9999992", returned_ids)


# ---------------------------------------------------------------------------
# Player URL construction
# ---------------------------------------------------------------------------

class TestPlayerUrl(unittest.TestCase):
    def test_player_url_format(self):
        """probe_target constructs player/embed/<story_id>/<audio_id>."""
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="1104792247",
                audio_id="1198988717",
                date="2022-06-13",
                title="Test Episode",
                output_dir=Path(out_dir),
                fetch_fn=_make_fetch(cdx_text="[]"),
            )
        expected_player_url = "https://www.npr.org/player/embed/1104792247/1198988717"
        self.assertEqual(result["player_url"], expected_player_url)

    def test_cdx_url_contains_exact_player_url(self):
        """CDX is queried with the exact player URL, not a wildcard or partial."""
        fetched_urls: list[str] = []

        def fake_fetch(url: str, read_bytes: int = probe.MAX_TEXT_BYTES, method: str = "GET") -> dict:
            fetched_urls.append(url)
            return {"ok": True, "text": "[]", "final_url": url, "http_status": 200,
                    "content_type": "", "content_length": None}

        with TemporaryDirectory() as out_dir:
            probe.probe_target(
                story_id="1104792247",
                audio_id="1198988717",
                date="2022-06-13",
                title="Test Episode",
                output_dir=Path(out_dir),
                fetch_fn=fake_fetch,
            )
        cdx_call = next((u for u in fetched_urls if "cdx/search" in u), None)
        self.assertIsNotNone(cdx_call)
        self.assertIn("1104792247", cdx_call)
        self.assertIn("1198988717", cdx_call)
        # No wildcard broadening
        self.assertNotIn("matchType=domain", cdx_call)
        self.assertNotIn("matchType=prefix", cdx_call)


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
        cdx = self._cdx_json(["20200101120000", "20220614080000", "20220613100000"])
        result = probe._parse_cdx(cdx, "2022-06-13")
        self.assertEqual(result[0], "20220613100000")
        self.assertEqual(result[1], "20220614080000")
        self.assertEqual(result[2], "20200101120000")

    def test_caps_at_wayback_max_captures(self):
        # WAYBACK_MAX_CAPTURES must be 3 per design spec
        self.assertEqual(probe.WAYBACK_MAX_CAPTURES, 3)
        many = [f"2022060{i}120000" for i in range(9)]
        cdx = self._cdx_json(many)
        result = probe._parse_cdx(cdx, "2022-06-04")
        self.assertLessEqual(len(result), probe.WAYBACK_MAX_CAPTURES)
        self.assertLessEqual(len(result), 3)

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
# UUID filtering — show UUID and random/cookie UUIDs rejected
# ---------------------------------------------------------------------------

class TestUuidFiltering(unittest.TestCase):
    """Tests proving strict episode-UUID filtering."""

    def test_show_uuid_rejected(self):
        """The known Simplecast show UUID must never appear in simplecast_episode_uuids."""
        show_uuid = probe.SIMPLECAST_SHOW_UUID
        # Even in an /episodes/<uuid>/audio URL that happens to use the show UUID
        html = (
            f'"episodeId": "{show_uuid}"\n'
            f'"episodeUuid": "{show_uuid}"\n'
            f'awEpisodeId="{show_uuid}"\n'
            f'href="/episodes/{show_uuid}/audio/test.mp3"'
        )
        result = probe.extract_from_player_html(html)
        self.assertNotIn(show_uuid.lower(), [u.lower() for u in result["simplecast_episode_uuids"]])

    def test_random_uuid_not_extracted_without_episode_key(self):
        """A UUID that appears near 'simplecast' without an episode-specific key is NOT accepted."""
        random_uuid = "deadbeef-0000-1111-2222-333333333333"
        # Place the UUID near "simplecast" but without any episode-specific binding
        html = f'data-simplecast-player="{random_uuid}" some other content'
        result = probe.extract_from_player_html(html)
        self.assertNotIn(random_uuid, result["simplecast_episode_uuids"])

    def test_cookie_uuid_not_extracted(self):
        """A UUID from a cookie/session context near 'simplecast' is NOT accepted."""
        cookie_uuid = "cafebabe-cafe-babe-cafe-babecafebabe"
        html = f'Set-Cookie: simplecast_session={cookie_uuid}; Path=/'
        result = probe.extract_from_player_html(html)
        self.assertNotIn(cookie_uuid, result["simplecast_episode_uuids"])

    def test_episode_audio_path_uuid_accepted(self):
        """A UUID in /episodes/<uuid>/audio/ path IS accepted as episode-specific."""
        ep_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        html = f'href="https://cdn.simplecastaudio.com/episodes/{ep_uuid}/audio/test.mp3"'
        result = probe.extract_from_player_html(html)
        self.assertIn(ep_uuid, result["simplecast_episode_uuids"])

    def test_aw_episode_id_uuid_accepted(self):
        """A UUID in awEpisodeId= parameter IS accepted as episode-specific."""
        ep_uuid = "f1f2f3f4-f5f6-f7f8-f9fa-fbfcfdfeff00"
        html = f'awEpisodeId="{ep_uuid}"'
        result = probe.extract_from_player_html(html)
        self.assertIn(ep_uuid, result["simplecast_episode_uuids"])
        self.assertIn(ep_uuid, result["aw_episode_id_uuids"])

    def test_analytics_uuid_near_simplecast_not_extracted(self):
        """A UUID that appears near the word 'simplecast' but only in an analytics context
        is NOT accepted by the strict extractor."""
        analytics_uuid = "11111111-2222-3333-4444-555555555555"
        html = f'data-analytics-id="simplecast_track_{analytics_uuid}"'
        result = probe.extract_from_player_html(html)
        self.assertNotIn(analytics_uuid, result["simplecast_episode_uuids"])


# ---------------------------------------------------------------------------
# HTML extraction — episode-specific patterns
# ---------------------------------------------------------------------------

class TestExtractFromPlayerHtml(unittest.TestCase):
    def test_extracts_simplecast_uuid_from_episode_id_key(self):
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        html = f'{{"episodeId": "{uuid}", "title": "Test"}}'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_episode_uuids"])
        self.assertIn(uuid, result["episode_key_uuids"])

    def test_extracts_simplecast_uuid_from_episode_uuid_key(self):
        uuid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
        html = f'"episodeUuid": "{uuid}"'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_episode_uuids"])

    def test_extracts_simplecast_audio_url_with_episode_path(self):
        """simplecastaudio.com URL with /episodes/<uuid>/audio is extracted."""
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        audio_url = f"https://cdn.simplecastaudio.com/episodes/{uuid}/audio/upload/test.mp3"
        html = f'<script>window.playerData = {{audioUrl: "{audio_url}"}};</script>'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_episode_uuids"])
        self.assertTrue(any(uuid in u for u in result["simplecast_audio_urls"]))

    def test_generic_simplecast_cdn_url_without_episode_path_not_captured(self):
        """A simplecastaudio.com URL that lacks /episodes/<uuid>/audio is NOT an audio candidate."""
        audio_url = "https://cdn.simplecastaudio.com/pod/audio/test.mp3"
        html = f'<audio src="{audio_url}"></audio>'
        result = probe.extract_from_player_html(html)
        # No episode UUID in path → no audio URL captured
        self.assertEqual(result["simplecast_audio_urls"], [])
        self.assertEqual(result["simplecast_episode_uuids"], [])

    def test_empty_html_returns_empty_results(self):
        result = probe.extract_from_player_html("")
        self.assertEqual(result["simplecast_episode_uuids"], [])
        self.assertEqual(result["simplecast_audio_urls"], [])
        self.assertEqual(result["legacy_audio_urls"], [])
        self.assertEqual(result["episode_key_uuids"], [])
        self.assertEqual(result["aw_episode_id_uuids"], [])

    def test_deduplicates_extracted_uuids(self):
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        html = (
            f'"episodeId": "{uuid}"\n'
            f'"episodeUuid": "{uuid}"\n'
        )
        result = probe.extract_from_player_html(html)
        self.assertEqual(result["simplecast_episode_uuids"].count(uuid), 1)

    def test_case_insensitive_key_matching(self):
        uuid = "aabbccdd-aabb-aabb-aabb-aabbccddeeff"
        html = f'"EPISODEID": "{uuid}"'
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_episode_uuids"])

    def test_real_world_known_good_pattern(self):
        """Simulate the June 22 known-good player bootstrap pattern."""
        uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        audio_url = f"https://cdn.simplecastaudio.com/episodes/{uuid}/audio/upload/test.mp3"
        html = textwrap.dedent(f"""
            <script id="__NEXT_DATA__" type="application/json">
            {{
              "props": {{
                "pageProps": {{
                  "episode": {{
                    "episodeId": "{uuid}",
                    "title": "The price of free stock trading",
                    "audioUrl": "{audio_url}"
                  }}
                }}
              }}
            }}
            </script>
        """)
        result = probe.extract_from_player_html(html)
        self.assertIn(uuid, result["simplecast_episode_uuids"])
        self.assertIn(uuid, result["episode_key_uuids"])
        self.assertTrue(any(uuid in u for u in result["simplecast_audio_urls"]))


# ---------------------------------------------------------------------------
# Legacy audio URL extraction
# ---------------------------------------------------------------------------

class TestLegacyAudioExtraction(unittest.TestCase):
    def test_ondemand_npr_mp3_extracted(self):
        """ondemand.npr.org .mp3 URL is extracted as a legacy audio candidate."""
        url = "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2020/01/20200101_indicator_test.mp3"
        html = f'<audio src="{url}"></audio>'
        result = probe.extract_from_player_html(html)
        self.assertIn(url, result["legacy_audio_urls"])

    def test_ondemand_npr_m4a_extracted(self):
        """ondemand.npr.org .m4a URL is extracted as a legacy audio candidate."""
        url = "https://ondemand.npr.org/anon.npr-aac/npr/indicator/2020/01/20200101_indicator_test.m4a"
        html = f'<source src="{url}" />'
        result = probe.extract_from_player_html(html)
        self.assertIn(url, result["legacy_audio_urls"])

    def test_podtrac_npr_510325_extracted(self):
        """play.podtrac.com/npr-510325 URL is extracted as a legacy audio candidate."""
        url = "https://play.podtrac.com/npr-510325/ondemand.npr.org/indicator/2020/01/test.mp3"
        html = f'<source src="{url}">'
        result = probe.extract_from_player_html(html)
        self.assertIn(url, result["legacy_audio_urls"])

    def test_live_stream_not_extracted(self):
        """NPR live radio stream URL is NOT extracted."""
        live_url = "https://ondemand.npr.org/live.mp3"
        html = f'<audio src="{live_url}"></audio>'
        result = probe.extract_from_player_html(html)
        self.assertNotIn(live_url, result["legacy_audio_urls"])

    def test_generic_live_mp3_not_extracted(self):
        """A generic live.mp3 URL that matches the ondemand domain is not extracted."""
        live_url = "https://ondemand.npr.org/stream/live.mp3"
        html = f'<audio src="{live_url}"></audio>'
        result = probe.extract_from_player_html(html)
        self.assertNotIn(live_url, result["legacy_audio_urls"])

    def test_unrelated_audio_url_not_extracted(self):
        """An unrelated audio URL (not from ondemand.npr.org / podtrac npr-510325) is NOT extracted."""
        unrelated = "https://cdn.example.com/audio/random-podcast.mp3"
        html = f'<audio src="{unrelated}"></audio>'
        result = probe.extract_from_player_html(html)
        self.assertNotIn(unrelated, result["legacy_audio_urls"])


# ---------------------------------------------------------------------------
# Audio validation
# ---------------------------------------------------------------------------

class TestAudioValidation(unittest.TestCase):
    def test_playable_requires_200_or_206_and_audio_mime(self):
        """validate_audio_candidate returns playable=True only for 200/206 + audio MIME."""
        def ok_head(url, read_bytes=0, method="GET"):
            return {"ok": True, "final_url": url, "http_status": 200,
                    "content_type": "audio/mpeg", "content_length": "999", "text": ""}
        val = probe.validate_audio_candidate("https://example.com/ep.mp3", fetch_fn=ok_head)
        self.assertTrue(val["playable"])

    def test_206_partial_is_playable(self):
        """HTTP 206 with audio MIME is accepted."""
        def ok_head(url, read_bytes=0, method="GET"):
            return {"ok": True, "final_url": url, "http_status": 206,
                    "content_type": "audio/mpeg", "content_length": None, "text": ""}
        val = probe.validate_audio_candidate("https://example.com/ep.mp3", fetch_fn=ok_head)
        self.assertTrue(val["playable"])

    def test_non_audio_mime_is_not_playable(self):
        """HTTP 200 with text/html MIME is not playable."""
        def bad_mime(url, read_bytes=0, method="GET"):
            return {"ok": True, "final_url": url, "http_status": 200,
                    "content_type": "text/html", "content_length": None, "text": ""}
        val = probe.validate_audio_candidate("https://example.com/page", fetch_fn=bad_mime)
        self.assertFalse(val["playable"])

    def test_404_is_not_playable(self):
        """HTTP 404 is not playable."""
        def not_found(url, read_bytes=0, method="GET"):
            return {"ok": False, "error": "HTTPError 404: Not Found", "text": ""}
        val = probe.validate_audio_candidate("https://example.com/missing.mp3", fetch_fn=not_found)
        self.assertFalse(val["playable"])

    def test_head_failure_falls_back_to_get(self):
        """When HEAD fails, GET with Range is attempted."""
        calls: list[str] = []

        def mixed(url, read_bytes=0, method="GET"):
            calls.append(method)
            if method == "HEAD":
                return {"ok": False, "error": "HEAD not allowed", "text": ""}
            return {"ok": True, "final_url": url, "http_status": 200,
                    "content_type": "audio/mpeg", "content_length": None, "text": ""}

        val = probe.validate_audio_candidate("https://example.com/ep.mp3", fetch_fn=mixed)
        self.assertIn("HEAD", calls)
        self.assertIn("GET", calls)
        self.assertTrue(val["playable"])


# ---------------------------------------------------------------------------
# Classification semantics
# ---------------------------------------------------------------------------

class TestClassification(unittest.TestCase):
    def test_uuid_without_playable_audio_is_not_recovered(self):
        """UUID_FOUND_AUDIO_UNRESOLVED is assigned when UUID found but audio unreachable."""
        ep_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        player_html = f'"episodeId": "{ep_uuid}"'

        fetch = _make_fetch(
            cdx_text=_cdx_one_capture(),
            page_html=player_html,
            audio_ok=False,   # validation fails
        )
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "UUID_FOUND_AUDIO_UNRESOLVED")
        self.assertNotEqual(result["classification"], "RECOVERED_AND_VALIDATED")

    def test_playable_audio_yields_recovered_and_validated(self):
        """RECOVERED_AND_VALIDATED requires playable audio (200/206 + audio MIME)."""
        ep_uuid = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
        audio_url = f"https://cdn.simplecastaudio.com/episodes/{ep_uuid}/audio/test.mp3"
        player_html = f'"episodeId": "{ep_uuid}"\nhref="{audio_url}"'

        fetch = _make_fetch(
            cdx_text=_cdx_one_capture(),
            page_html=player_html,
            audio_ok=True, audio_status=200, audio_content_type="audio/mpeg",
        )
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "RECOVERED_AND_VALIDATED")

    def test_no_captures_classification(self):
        fetch = _make_fetch(cdx_text="[]")
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="T",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "NO_WAYBACK_CAPTURES")

    def test_network_failure_classification(self):
        def always_fails(url, read_bytes=probe.MAX_TEXT_BYTES, method="GET"):
            return {"ok": False, "error": "network timeout", "text": ""}
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="T",
                output_dir=Path(out_dir), fetch_fn=always_fails,
            )
        self.assertEqual(result["classification"], "NETWORK_FAILURE")

    def test_no_media_found_classification(self):
        fetch = _make_fetch(
            cdx_text=_cdx_one_capture(),
            page_html="<html><body>No audio here</body></html>",
        )
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="T",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "NO_TARGET_MEDIA_FOUND")

    def test_audio_candidate_not_playable_classification(self):
        """AUDIO_CANDIDATE_NOT_PLAYABLE: URL found but audio validation fails."""
        legacy_url = "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2020/test.mp3"
        fetch = _make_fetch(
            cdx_text=_cdx_one_capture(),
            page_html=f'<audio src="{legacy_url}"></audio>',
            audio_ok=False,
        )
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2020-01-15", title="T",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "AUDIO_CANDIDATE_NOT_PLAYABLE")


# ---------------------------------------------------------------------------
# probe_target integration (mocked network) — full flow
# ---------------------------------------------------------------------------

class TestProbeTarget(unittest.TestCase):
    def test_no_captures_result(self):
        fetch = _make_fetch(cdx_text="[]")
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "NO_WAYBACK_CAPTURES")
        self.assertEqual(result["cdx_capture_count"], 0)
        self.assertEqual(result["simplecast_episode_uuids"], [])

    def test_uuid_found_in_archived_html_gives_uuid_found_audio_unresolved(self):
        ep_uuid = "e9827f64-db6e-4abb-aee9-a9fe394033ae"
        fetch = _make_fetch(
            cdx_text=_cdx_one_capture(),
            page_html=f'"episodeId": "{ep_uuid}"',
            audio_ok=False,
        )
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertIn(ep_uuid, result["simplecast_episode_uuids"])
        self.assertEqual(result["classification"], "UUID_FOUND_AUDIO_UNRESOLVED")

    def test_legacy_url_found_but_not_playable_gives_candidate_not_playable(self):
        legacy_url = "https://ondemand.npr.org/anon.npr-mp3/npr/indicator/2020/test.mp3"
        fetch = _make_fetch(
            cdx_text=_cdx_one_capture(),
            page_html=f'<audio src="{legacy_url}"></audio>',
            audio_ok=False,
        )
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2020-01-15", title="T",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "AUDIO_CANDIDATE_NOT_PLAYABLE")
        self.assertIn(legacy_url, result["legacy_audio_urls"])

    def test_no_media_in_html(self):
        fetch = _make_fetch(
            cdx_text=_cdx_one_capture(),
            page_html="<html><body>Nothing here</body></html>",
        )
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="T",
                output_dir=Path(out_dir), fetch_fn=fetch,
            )
        self.assertEqual(result["classification"], "NO_TARGET_MEDIA_FOUND")

    def test_cdx_error_gives_network_failure(self):
        def always_fails(url, read_bytes=probe.MAX_TEXT_BYTES, method="GET"):
            return {"ok": False, "error": "network timeout", "text": ""}
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="T",
                output_dir=Path(out_dir), fetch_fn=always_fails,
            )
        self.assertEqual(result["classification"], "NETWORK_FAILURE")
        self.assertIsNotNone(result["cdx_error"])

    def test_fetch_error_on_archived_page_recorded_in_snapshot(self):
        def fake_fetch(url, read_bytes=probe.MAX_TEXT_BYTES, method="GET"):
            if "cdx/search" in url:
                return {"ok": True, "text": _cdx_one_capture(), "final_url": url,
                        "http_status": 200, "content_type": "", "content_length": None}
            return {"ok": False, "error": "connection refused", "text": ""}
        with TemporaryDirectory() as out_dir:
            result = probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-13", title="T",
                output_dir=Path(out_dir), fetch_fn=fake_fetch,
            )
        self.assertEqual(len(result["snapshots"]), 1)
        self.assertEqual(result["snapshots"][0]["fetch_status"], "error")
        self.assertEqual(result["classification"], "NO_TARGET_MEDIA_FOUND")

    def test_archived_url_uses_id_modifier(self):
        """Archived page must be fetched with the id_/ modifier."""
        fetched_urls: list[str] = []

        def fake_fetch(url, read_bytes=probe.MAX_TEXT_BYTES, method="GET"):
            fetched_urls.append(url)
            if "cdx/search" in url:
                return {"ok": True, "text": _cdx_one_capture("20220613120000"),
                        "final_url": url, "http_status": 200,
                        "content_type": "", "content_length": None}
            return {"ok": True, "text": "<html></html>", "final_url": url,
                    "http_status": 200, "content_type": "text/html", "content_length": None}

        with TemporaryDirectory() as out_dir:
            probe.probe_target(
                story_id="1104792247", audio_id="1198988717",
                date="2022-06-13", title="Test",
                output_dir=Path(out_dir), fetch_fn=fake_fetch,
            )

        archived_calls = [u for u in fetched_urls if "web.archive.org/web/" in u]
        self.assertTrue(len(archived_calls) >= 1)
        for url in archived_calls:
            self.assertIn("id_/", url, f"Missing id_/ modifier in: {url}")
            self.assertIn("1104792247", url)
            self.assertIn("1198988717", url)


# ---------------------------------------------------------------------------
# Request budget
# ---------------------------------------------------------------------------

class TestRequestBudget(unittest.TestCase):
    def test_max_3_archive_fetches_per_target(self):
        """probe_target must never fetch more than WAYBACK_MAX_CAPTURES (3) archived pages."""
        self.assertEqual(probe.WAYBACK_MAX_CAPTURES, 3)

        # CDX returns 9 captures; probe must cap at 3
        many_ts = [f"2022060{i}120000" for i in range(9)]
        header = ["timestamp", "original", "statuscode", "mimetype", "digest"]
        cdx_text = json.dumps(
            [header] + [[ts, "url", "200", "text/html", "sha1:X"] for ts in many_ts]
        )
        archive_fetch_count = [0]

        def fake_fetch(url, read_bytes=probe.MAX_TEXT_BYTES, method="GET"):
            if "cdx/search" in url:
                return {"ok": True, "text": cdx_text, "final_url": url,
                        "http_status": 200, "content_type": "", "content_length": None}
            if "web.archive.org/web/" in url:
                archive_fetch_count[0] += 1
                return {"ok": True, "text": "<html></html>", "final_url": url,
                        "http_status": 200, "content_type": "text/html", "content_length": None}
            return {"ok": False, "error": "404", "text": ""}

        with TemporaryDirectory() as out_dir:
            probe.probe_target(
                story_id="111", audio_id="222", date="2022-06-04", title="T",
                output_dir=Path(out_dir), fetch_fn=fake_fetch,
            )
        self.assertLessEqual(archive_fetch_count[0], 3)

    def test_per_target_budget_constant_matches_design(self):
        """Verify that the per-target budget constant is within design spec."""
        # Design spec: 1 CDX + ≤3 fetches + ≤3 validations (HEAD+GET) = 18 max
        per_target = probe.MAX_RETRIES + probe.WAYBACK_MAX_CAPTURES * probe.MAX_RETRIES + probe.MAX_AUDIO_CANDIDATES * 2
        self.assertLessEqual(per_target, 18)


# ---------------------------------------------------------------------------
# Checkpoint and resume
# ---------------------------------------------------------------------------

class TestCheckpointResume(unittest.TestCase):
    def _ep_fetch(self, story_id: str = "111", audio_id: str = "222") -> "callable":
        return _make_fetch(cdx_text="[]")

    def test_checkpoint_created_per_target(self):
        """A checkpoint JSON file must be written immediately after probe_target completes."""
        fetch = _make_fetch(cdx_text="[]")
        with TemporaryDirectory() as out_dir:
            out_path = Path(out_dir)
            probe.probe_target(
                story_id="9876543", audio_id="111",
                date="2022-06-13", title="T",
                output_dir=out_path, fetch_fn=fetch,
            )
            cp = out_path / "checkpoint_9876543.json"
            self.assertTrue(cp.exists(), "Checkpoint file must be created immediately")
            data = json.loads(cp.read_text())
            self.assertEqual(data["story_id"], "9876543")

    def test_completed_target_skipped_on_rerun(self):
        """A target with an existing checkpoint is skipped (not re-fetched)."""
        with TemporaryDirectory() as out_dir:
            out_path = Path(out_dir)
            # Write a pre-existing checkpoint
            checkpoint_data = {
                "story_id": "9876543",
                "classification": "NO_WAYBACK_CAPTURES",
                "title": "T",
                "date": "2022-06-13",
                "player_url": "https://www.npr.org/player/embed/9876543/111",
            }
            (out_path / "checkpoint_9876543.json").write_text(
                json.dumps(checkpoint_data), encoding="utf-8"
            )

            fetch_call_count = [0]
            def counting_fetch(url, read_bytes=probe.MAX_TEXT_BYTES, method="GET"):
                fetch_call_count[0] += 1
                return {"ok": True, "text": "[]", "final_url": url,
                        "http_status": 200, "content_type": "", "content_length": None}

            result = probe.probe_target(
                story_id="9876543", audio_id="111",
                date="2022-06-13", title="T",
                output_dir=out_path, fetch_fn=counting_fetch,
            )
            # Must return checkpoint data without making any network calls
            self.assertEqual(fetch_call_count[0], 0)
            self.assertEqual(result["story_id"], "9876543")

    def test_force_reruns_despite_checkpoint(self):
        """force=True causes re-probing even when checkpoint exists."""
        with TemporaryDirectory() as out_dir:
            out_path = Path(out_dir)
            (out_path / "checkpoint_9876543.json").write_text(
                json.dumps({"story_id": "9876543", "classification": "OLD"}),
                encoding="utf-8",
            )

            fetch_call_count = [0]
            def counting_fetch(url, read_bytes=probe.MAX_TEXT_BYTES, method="GET"):
                fetch_call_count[0] += 1
                return {"ok": True, "text": "[]", "final_url": url,
                        "http_status": 200, "content_type": "", "content_length": None}

            probe.probe_target(
                story_id="9876543", audio_id="111",
                date="2022-06-13", title="T",
                output_dir=out_path, fetch_fn=counting_fetch, force=True,
            )
            self.assertGreater(fetch_call_count[0], 0)


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
            probe._assert_production_unchanged(before, after)  # should not raise

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
