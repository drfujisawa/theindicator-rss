#!/usr/bin/env python3
from pathlib import Path
import json
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib.error import URLError

REPO_ROOT = Path(__file__).resolve().parents[1]

VALIDATION_FILE = str(REPO_ROOT / "data" / "recovery" / "indicator_npr_audio_validation.json")
OUTPUT_FILE = str(REPO_ROOT / "archive" / "recovery" / "indicator_npr_e_parameter_test.json")
TARGETS = [
    {"title": "Paranormal Profits", "audio_id": "662707862"},
    {"title": "The Traffic Tariff", "audio_id": "730102905"},
]

TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IndicatorNPRParameterTest/1.0)",
    "Range": "bytes=0-4095",
}


def fetch(url):
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=TIMEOUT) as response:
        sample = response.read(4096)
        return {
            "status_code": getattr(response, "status", None),
            "final_url": response.geturl(),
            "content_type": response.headers.get("Content-Type", ""),
            "sample_size": len(sample),
        }


def replace_e(url, new_e):
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["e"] = [new_e]
    new_query = urlencode(query, doseq=True)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


def build_report(control_url, targets, fetcher):
    control_response = fetcher(control_url)
    tests = []
    for target in targets:
        modified_url = replace_e(control_url, target["audio_id"])
        response = fetcher(modified_url)
        tests.append(
            {
                "target_title": target["title"],
                "target_audio_id": target["audio_id"],
                "modified_url": modified_url,
                "response": response,
                "same_final_url_as_control": response.get("final_url")
                == control_response.get("final_url"),
            }
        )
    return {
        "method": "test-whether-npr-e-parameter-selects-audio",
        "control": {"audio_url": control_url, "response": control_response},
        "tests": tests,
        "interpretation": {
            "if_same_final_url_is_true": "The e parameter is tracking metadata and does not select the MP3.",
            "if_same_final_url_is_false": "The e parameter may participate in selecting the episode audio.",
        },
    }


def load_control_url():
    with open(VALIDATION_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)
    validated = data.get("validated_audio", [])
    if not validated:
        raise RuntimeError("No validated audio records found.")
    control_url = validated[0].get("audio_url")
    if not control_url:
        raise RuntimeError("Control record has no audio URL.")
    return control_url


class TestNprEParameterUnit(unittest.TestCase):
    def test_replace_e_overwrites_only_e_parameter(self):
        url = "https://example.com/audio.mp3?d=637&e=111&t=podcast&p=510325"
        updated = replace_e(url, "999")
        query = parse_qs(urlparse(updated).query)
        self.assertEqual(query["e"], ["999"])
        self.assertEqual(query["d"], ["637"])
        self.assertEqual(query["t"], ["podcast"])
        self.assertEqual(query["p"], ["510325"])

    def test_build_report_uses_modified_url_and_compares_final_destination(self):
        control = "https://example.com/audio.mp3?d=637&e=593259696&t=podcast&p=510325"

        def fake_fetch(url):
            return {
                "status_code": 206,
                "final_url": url.replace("https://example.com/", "https://ondemand.npr.org/"),
                "content_type": "audio/mpeg",
                "sample_size": 4096,
            }

        report_payload = build_report(control, TARGETS, fake_fetch)
        self.assertEqual(report_payload["method"], "test-whether-npr-e-parameter-selects-audio")
        self.assertEqual(len(report_payload["tests"]), len(TARGETS))
        for row, target in zip(report_payload["tests"], TARGETS):
            self.assertEqual(row["target_audio_id"], target["audio_id"])
            self.assertIn(f"e={target['audio_id']}", row["modified_url"])
            self.assertFalse(row["same_final_url_as_control"])

    def test_fetch_uses_expected_headers_and_timeout(self):
        fake_response = mock.MagicMock()
        fake_response.read.return_value = b"a" * 4096
        fake_response.geturl.return_value = "https://final.example/audio.mp3"
        fake_response.status = 206
        fake_response.headers.get.return_value = "audio/mpeg"
        fake_ctx = mock.MagicMock()
        fake_ctx.__enter__.return_value = fake_response

        with mock.patch(f"{fetch.__module__}.urlopen", return_value=fake_ctx) as mocked_open:
            result = fetch("https://example.com/audio.mp3?e=1")

        request = mocked_open.call_args[0][0]
        self.assertEqual(request.headers.get("Range"), "bytes=0-4095")
        self.assertEqual(mocked_open.call_args.kwargs["timeout"], TIMEOUT)
        self.assertEqual(result["status_code"], 206)
        self.assertEqual(result["content_type"], "audio/mpeg")
        self.assertEqual(result["sample_size"], 4096)


@unittest.skipUnless(
    os.getenv("RUN_LIVE_NETWORK_TESTS") == "1",
    "Live network integration test disabled (set RUN_LIVE_NETWORK_TESTS=1 to enable).",
)
class TestNprEParameterLiveIntegration(unittest.TestCase):
    def test_live_report_generation(self):
        control_url = load_control_url()
        try:
            payload = build_report(control_url, TARGETS, fetch)
        except URLError as error:
            self.skipTest(f"Network unavailable for live integration test: {error}")
        self.assertEqual(len(payload["tests"]), len(TARGETS))


if __name__ == "__main__":
    unittest.main()
