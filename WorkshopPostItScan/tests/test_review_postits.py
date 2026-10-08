import csv
import http.client
import io
import json
import fcntl
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import review_postits as review


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.images = self.root / "photos"
        self.images.mkdir()
        (self.images / "one.jpg").write_bytes(b"example photo")
        self.source = self.root / "scan.json"
        self.corrections = self.root / "corrections.json"
        self.row = dict(bild="photos/one.jpg", adresse_roh="MA11", adresse="MA11",
                        seite="M", zeile="A", spalte=1, teilposition=1,
                        text="Pflanzen\naus Europa", text_englisch="Plants\nfrom Europe",
                        status="prüfen", hinweis="Handschrift", fehler="", sha256="abc")
        self.write_source([self.row])
        self.store = self.new_store()

    def write_source(self, rows):
        review.atomic_json(self.source, rows)

    def new_store(self):
        return review.ReviewStore(self.source, self.corrections, self.images, self.root)

    def payload(self, **changes):
        row = self.store.snapshot()["items"][0]
        data = dict(bild=row["bild"], adresse=row["adresse"], text=row["text"],
                    text_englisch=row["text_englisch"], geprueft=True,
                    basis_hash=row["_basis_hash"], revision=row["_revision"])
        data.update(changes)
        return data

    def test_save_preserves_scan_and_survives_restart(self):
        before = self.source.read_bytes()
        self.store.save(self.payload(adresse="s f 5 2", text="Korrigiert\n→ Text"))
        self.assertEqual(self.source.read_bytes(), before)
        row = self.new_store().snapshot()["items"][0]
        self.assertEqual(row["adresse"], "SF52")
        self.assertEqual((row["seite"], row["zeile"], row["spalte"], row["teilposition"]), ("S", "F", 5, 2))
        self.assertEqual(row["text"], "Korrigiert\n→ Text")
        self.assertTrue(row["geprueft"])
        self.assertTrue(row["manuell_geaendert"])
        self.assertEqual(row["adresse_roh"], "MA11")
        self.assertEqual(row["status"], "prüfen")

    def test_validation_and_empty_address(self):
        for changes in [dict(adresse="XQ99"), dict(text=None), dict(text_englisch=42),
                        dict(geprueft="yes"), dict(revision=True), dict(bild="not-in-json.jpg")]:
            with self.assertRaises(review.ReviewError):
                self.store.save(self.payload(**changes))
        self.assertFalse(self.corrections.exists())
        self.store.save(self.payload(adresse=""))
        row = self.store.snapshot()["items"][0]
        self.assertIsNone(row["adresse"])
        self.assertIsNone(row["spalte"])

    def test_only_marking_reviewed_is_not_a_manual_text_change(self):
        self.store.save(self.payload())
        row = self.store.snapshot()["items"][0]
        self.assertTrue(row["geprueft"])
        self.assertFalse(row["manuell_geaendert"])

    def test_changed_scan_marks_saved_corrections_stale(self):
        self.store.save(self.payload(text="Korrektur"))
        self.write_source([dict(self.row, text="Neuer Scan", text_englisch="New scan")])
        row = self.store.snapshot()["items"][0]
        self.assertEqual(row["text"], "Korrektur")
        self.assertEqual(row["_scan"]["text"], "Neuer Scan")
        self.assertTrue(row["pruefung_veraltet"])
        self.assertFalse(row["geprueft"])
        self.store.save(self.payload())
        row = self.store.snapshot()["items"][0]
        self.assertFalse(row["pruefung_veraltet"])
        self.assertTrue(row["geprueft"])

    def test_scan_change_during_edit_rejects_save(self):
        draft = self.payload(text="My unsaved text")
        self.write_source([dict(self.row, text="New API result")])
        with self.assertRaises(review.ReviewError) as result:
            self.store.save(draft)
        self.assertEqual(result.exception.status, 409)
        self.assertFalse(self.corrections.exists())

    def test_concurrent_tabs_cannot_overwrite_each_other(self):
        draft = self.payload()
        def save_once():
            try:
                self.store.save(draft)
                return 200
            except review.ReviewError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as executor:
            codes = list(executor.map(lambda _: save_once(), range(2)))
        self.assertEqual(sorted(codes), [200, 409])
        self.assertEqual(self.new_store().snapshot()["items"][0]["_revision"], 1)

    def test_bad_scan_keeps_last_valid_rows_but_blocks_writes(self):
        self.source.write_text("{broken", encoding="utf-8")
        snapshot = self.store.snapshot()
        self.assertTrue(snapshot["warnung"])
        self.assertEqual(len(snapshot["items"]), 1)
        with self.assertRaises(review.ReviewError) as result:
            self.store.save(self.payload())
        self.assertEqual(result.exception.status, 503)
        with self.assertRaises(review.ReviewError):
            self.store.export("json")
        self.write_source([self.row])
        self.assertFalse(self.store.snapshot()["warnung"])

    def test_new_scan_rows_appear_without_losing_corrections(self):
        self.store.save(self.payload(text="Manuell"))
        self.write_source([self.row, dict(self.row, bild="photos/two.jpg")])
        rows = self.store.snapshot()["items"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["text"], "Manuell")
        self.assertFalse(rows[1]["geprueft"])

    def test_exports_preserve_original_and_add_review_fields(self):
        self.store.save(self.payload(text='Text; "Zitat"\n→ mehr', text_englisch='Text; "quote"\n→ more'))
        data, _ = self.store.export("json")
        row = json.loads(data)[0]
        self.assertTrue(row["geprueft"])
        self.assertNotIn("_scan", row)
        self.assertNotIn("_basis_hash", row)
        data, _ = self.store.export("csv")
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        csv_row = next(csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(csv_row["text"], row["text"])
        self.assertEqual(csv_row["text_englisch"], row["text_englisch"])

    def test_image_access_is_limited_to_known_photos(self):
        self.assertEqual(self.store.image("photos/one.jpg")[0], b"example photo")
        for name in [".env", "../secret.jpg", "photos/missing.jpg"]:
            with self.assertRaises(review.ReviewError):
                self.store.image(name)
        (self.root / "outside.jpg").write_bytes(b"private")
        (self.images / "escape.jpg").symlink_to(self.root / "outside.jpg")
        self.write_source([self.row, dict(self.row, bild="photos/escape.jpg"), dict(self.row, bild="../outside.jpg")])
        for name in ["photos/escape.jpg", "../outside.jpg"]:
            with self.assertRaises(review.ReviewError) as result:
                self.store.image(name)
            self.assertEqual(result.exception.status, 403)

    def test_invalid_corrections_are_not_overwritten_and_same_path_rejected(self):
        self.corrections.write_text('{"version":1,"eintraege":{"photos/one.jpg":{}}}')
        before = self.corrections.read_bytes()
        with self.assertRaises(ValueError):
            self.new_store()
        self.assertEqual(self.corrections.read_bytes(), before)
        with self.assertRaises(ValueError):
            review.ReviewStore(self.source, self.source, self.images, self.root)

    def test_atomic_scan_replacements_while_reading(self):
        def replace():
            for i in range(20):
                self.write_source([dict(self.row, text=str(i))])
        with ThreadPoolExecutor(max_workers=1) as executor:
            writer = executor.submit(replace)
            for _ in range(20):
                result = self.store.snapshot()
                self.assertFalse(result["warnung"])
                self.assertEqual(len(result["items"]), 1)
            writer.result()

    def test_second_process_cannot_use_same_correction_file(self):
        with Path(str(self.corrections) + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([
                sys.executable, str(Path(review.__file__).resolve()),
                "--input", str(self.source), "--corrections", str(self.corrections),
                "--image-root", str(self.images),
            ], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("bereits von einem Prüfserver", result.stderr)

    def test_http_assets_save_exports_and_private_files(self):
        server = review.ReviewServer(("0.0.0.0", 0), self.store)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            port = server.server_address[1]
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            for path, mime in [("/", "text/html"), ("/app.js", "text/javascript"),
                               ("/style.css", "text/css"), ("/api/items", "application/json"),
                               ("/api/image?bild=photos/one.jpg", "image/jpeg")]:
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(mime, response.getheader("Content-Type"))
                self.assertTrue(response.read())
            connection.request("POST", "/api/corrections", json.dumps(self.payload(text="Saved via HTTP")), {"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["revision"], 1)
            connection.request("GET", "/api/export/json")
            response = connection.getresponse()
            self.assertEqual(json.loads(response.read())[0]["text"], "Saved via HTTP")
            for path in ["/.env", "/scan.json", "/api/image?bild=.env"]:
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
            connection.request("POST", "/api/corrections", json.dumps(self.payload()), {"Content-Type": "application/json", "Origin": "https://other.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            # Zugriff über eine nicht lokale IPv4-Adresse im Host-Header zulassen.
            network_host = f"134.169.42.204:{port}"
            connection.request("GET", "/api/items", headers={"Host": network_host})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.request("POST", "/api/corrections", json.dumps(self.payload()),
                               {"Content-Type": "application/json", "Host": network_host,
                                "Origin": f"http://{network_host}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.request("GET", "/api/items", headers={"Host": "other.example"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
