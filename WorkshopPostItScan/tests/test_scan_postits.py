import argparse
import contextlib
import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import scan_postits as scan


def answer(raw="M A 3 2", text="Original → Text\nZweite Zeile", review=False,
           english="Original → Text\nSecond line"):
    return json.dumps({"adresse_roh": raw, "text": text, "text_englisch": english,
                       "pruefen": review, "hinweis": ""})


class Response:
    def __init__(self, status=200, events=None, headers=None):
        self.status_code = status
        self.events = events if events is not None else [{"type": "done", "response": answer()}]
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_lines(self):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield event if isinstance(event, bytes) else json.dumps(event).encode()


class TranscriptionTests(unittest.TestCase):
    def test_address_ranges_and_missing_values(self):
        for raw, expected in [("m a 3 2", "MA32"), ("S-F-5-1", "SF51"), ("UB12", "UB12")]:
            self.assertEqual(scan.normalize_address(raw), expected)
        for raw in [None, "", "A32", "MA3", "XA32", "MG32", "MA62", "MA33", "MA32/UB12"]:
            self.assertIsNone(scan.normalize_address(raw))

    def test_original_text_and_address_components(self):
        row = scan.parse_transcription(answer())
        self.assertEqual(row["text"], "Original → Text\nZweite Zeile")
        self.assertEqual(row["text_englisch"], "Original → Text\nSecond line")
        self.assertEqual((row["seite"], row["zeile"], row["spalte"], row["teilposition"]), ("M", "A", 3, 2))
        self.assertEqual(row["status"], "ok")

    def test_ambiguous_and_unreadable_results_require_review(self):
        for value in [answer("MA3"), answer(None), answer(text="[unleserlich]"), answer(text=""), answer(review=True)]:
            self.assertEqual(scan.parse_transcription(value)["status"], "prüfen")
        self.assertIsNone(scan.parse_transcription(answer("MA3"))["adresse"])

    def test_original_and_english_are_separate_and_english_can_be_identical(self):
        for original, english in [
            ("Pflanzen aus\nSüdeuropa", "Plants from\nSouthern Europe"),
            ("Travel → delivery!", "Travel → delivery!"),
            ("Travel → Pflanzen", "Travel → plants"),
        ]:
            row = scan.parse_transcription(answer(text=original, english=english))
            self.assertEqual(row["text"], original)
            self.assertEqual(row["text_englisch"], english)

    def test_missing_invalid_or_empty_translation_is_an_error(self):
        for value in [None, 42, ""]:
            with self.assertRaises(scan.ScanError):
                scan.parse_transcription(answer(english=value))
        data = json.loads(answer())
        del data["text_englisch"]
        with self.assertRaises(scan.ScanError):
            scan.parse_transcription(json.dumps(data))

    def test_unreadable_translation_requires_review(self):
        row = scan.parse_transcription(answer(english="[unreadable]"))
        self.assertEqual(row["status"], "prüfen")

    def test_invalid_model_output_and_code_fence(self):
        for value in ["no JSON", "[]", '{"adresse_roh":1}', '{"text":"x"}']:
            with self.assertRaises(scan.ScanError):
                scan.parse_transcription(value)
        self.assertEqual(scan.parse_transcription("```json\n" + answer() + "\n```")["adresse"], "MA32")

    def test_stream_uses_done_response_instead_of_doubling_chunks(self):
        response = Response(events=[b"", {"type": "start"}, {"type": "chunk", "content": "partial"}, {"type": "done", "response": "complete"}])
        self.assertEqual(scan.read_stream(response), "complete")

    def test_incomplete_or_broken_stream_stops_run(self):
        for events in [[{"type": "chunk", "content": "partial"}], [b"not json"], [requests.ConnectionError()], [{"error": True}], [b"[]"]]:
            with self.assertRaises(scan.StopRun):
                scan.read_stream(Response(events=events))


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.photo = Path(self.temp.name) / "foto.jpg"
        self.photo.write_bytes(b"photo bytes")

    def test_multipart_binary_upload_and_new_thread(self):
        def post(url, **kwargs):
            self.assertEqual(url, scan.URL)
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-token")
            self.assertFalse(kwargs["allow_redirects"])
            self.assertTrue(kwargs["stream"])
            parts = kwargs["files"]
            self.assertEqual(parts["jsonBody"][2], "application/json")
            body = json.loads(parts["jsonBody"][1])
            self.assertIsNone(body["thread"])
            self.assertEqual(body["model"], "gpt-5.6-luna")
            self.assertEqual(parts["chatAttachment"][1].read(), b"photo bytes")
            self.assertEqual(parts["chatAttachment"][2], "image/jpeg")
            return Response()

        with patch.object(scan.requests, "post", side_effect=post):
            self.assertEqual(scan.request_transcription(self.photo, "test-token", "gpt-5.6-luna", threading.Event())["status"], "ok")

    def test_429_retries_are_bounded_and_respect_retry_after(self):
        stop = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        with patch.object(scan.requests, "post", side_effect=[Response(429, headers={"Retry-After": "7"}), Response(429), Response()]) as post:
            scan.request_transcription(self.photo, "token", "model", stop)
        self.assertEqual(post.call_count, 3)
        self.assertEqual(stop.wait.call_args_list[0].args, (7.0,))
        with patch.object(scan.requests, "post", return_value=Response(429)) as post:
            with self.assertRaises(scan.ScanError):
                scan.request_transcription(self.photo, "token", "model", stop)
        self.assertEqual(post.call_count, 3)

    def test_auth_redirect_and_timeout_stop_without_retry(self):
        for status in [401, 403, 302]:
            with patch.object(scan.requests, "post", return_value=Response(status)) as post:
                row, fatal = scan.scan_one(self.photo, "token", "model", threading.Event())
                self.assertTrue(fatal)
                self.assertEqual(row["status"], "fehler")
                self.assertEqual(post.call_count, 1)
        with patch.object(scan.requests, "post", side_effect=requests.Timeout()) as post:
            stop = threading.Event()
            row, fatal = scan.scan_one(self.photo, "token", "model", stop)
            self.assertTrue(fatal)
            self.assertTrue(stop.is_set())
            self.assertIn("Serverauftrag", row["fehler"])
            self.assertEqual(post.call_count, 1)

    def test_completed_but_invalid_answer_is_only_an_image_error(self):
        with patch.object(scan.requests, "post", return_value=Response(events=[{"type": "done", "response": "invalid"}])):
            row, fatal = scan.scan_one(self.photo, "token", "model", threading.Event())
        self.assertFalse(fatal)
        self.assertEqual(row["status"], "fehler")


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for i in range(4):
            (self.root / f"{i}.jpg").write_bytes(f"photo {i}".encode())
        self.args = argparse.Namespace(input=self.root, output=self.root / "results", force=False, model="gpt-5.6-luna", workers=2, limit=None)

    def run_quietly(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return scan.run(self.args, "token")

    def test_two_active_requests_including_streams_and_duplicate_addresses(self):
        barrier = threading.Barrier(2)
        mutex = threading.Lock()
        state = {"active": 0, "maximum": 0}

        class StreamingResponse(Response):
            def iter_lines(self):
                barrier.wait(timeout=5)
                yield from super().iter_lines()

            def __exit__(self, *args):
                with mutex:
                    state["active"] -= 1

        def post(*args, **kwargs):
            with mutex:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            return StreamingResponse()

        with patch.object(scan.requests, "post", side_effect=post) as post:
            self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(post.call_count, 4)
        self.assertEqual(state, {"active": 0, "maximum": 2})
        rows = scan.load_results(self.args.output)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["adresse"] for row in rows.values()}, {"MA32"})

    def test_resume_changed_file_and_csv_roundtrip(self):
        with patch.object(scan.requests, "post", return_value=Response()) as post:
            self.assertEqual(self.run_quietly(), 0)
            self.assertEqual(post.call_count, 4)
            post.reset_mock()
            self.assertEqual(self.run_quietly(), 0)
            post.assert_not_called()
            (self.root / "0.jpg").write_bytes(b"changed")
            self.assertEqual(self.run_quietly(), 0)
            self.assertEqual(post.call_count, 1)
        csv_path = self.root / "results.csv"
        self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with csv_path.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file, delimiter=";"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["text"], "Original → Text\nZweite Zeile")
        self.assertEqual(rows[0]["text_englisch"], "Original → Text\nSecond line")

    def test_old_results_and_changed_prompt_are_reprocessed(self):
        with patch.object(scan.requests, "post", return_value=Response()) as post:
            self.assertEqual(self.run_quietly(), 0)
            rows = scan.load_results(self.args.output)
            first = next(iter(rows.values()))
            del first["text_englisch"]
            del first["prompt_sha256"]
            scan.save_results(self.args.output, rows)
            post.reset_mock()
            self.assertEqual(self.run_quietly(), 0)
            self.assertEqual(post.call_count, 1)
            post.reset_mock()
            with patch.object(scan, "PROMPT_HASH", "new-prompt-version"):
                self.assertEqual(self.run_quietly(), 0)
                self.assertEqual(post.call_count, 4)

    def test_limit_force_changed_model_and_failed_image_resume(self):
        self.args.limit = 1
        with patch.object(scan.requests, "post", return_value=Response()) as post:
            self.run_quietly()
            self.assertEqual(post.call_count, 1)
            self.args.force = True
            self.run_quietly()
            self.assertEqual(post.call_count, 2)
            self.args.force = False
            self.args.model = "gpt-6-astra"
            self.run_quietly()
            self.assertEqual(post.call_count, 3)
        self.args.force = True
        with patch.object(scan.requests, "post", return_value=Response(400)):
            self.assertEqual(self.run_quietly(), 1)
        self.args.force = False
        with patch.object(scan.requests, "post", return_value=Response()) as post:
            self.assertEqual(self.run_quietly(), 0)
            self.assertEqual(post.call_count, 1)

    def test_fatal_failure_never_starts_next_batch(self):
        barrier = threading.Barrier(2)

        def post(*args, **kwargs):
            barrier.wait(timeout=5)
            return Response(401)

        with patch.object(scan.requests, "post", side_effect=post) as post:
            self.assertEqual(self.run_quietly(), 2)
            self.assertEqual(post.call_count, 2)
        self.assertEqual(len(scan.load_results(self.args.output)), 2)

    def test_invalid_checkpoint_is_not_overwritten(self):
        path = self.root / "results.json"
        path.write_text("not JSON", encoding="utf-8")
        with patch.object(scan.requests, "post") as post:
            with self.assertRaises(ValueError):
                self.run_quietly()
            post.assert_not_called()
        self.assertEqual(path.read_text(), "not JSON")


if __name__ == "__main__":
    unittest.main()
