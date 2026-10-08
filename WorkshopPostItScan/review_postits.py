#!/usr/bin/env python3
"""Lokale Post-it-Prüfseite; Scan-Ergebnisse werden ausschließlich gelesen."""

import argparse
import csv
import fcntl
import hashlib
import io
import ipaddress
import json
import mimetypes
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
SCAN_FIELDS = ["bild", "adresse_roh", "adresse", "seite", "zeile", "spalte",
               "teilposition", "text", "text_englisch", "status", "hinweis", "fehler",
               "sha256", "modell", "prompt_sha256"]
REVIEW_FIELDS = ["geprueft", "manuell_geaendert", "pruefung_veraltet"]


class ReviewError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def fingerprint(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def address_parts(value):
    if value is None or value == "":
        return dict(adresse=None, seite=None, zeile=None, spalte=None, teilposition=None)
    if not isinstance(value, str):
        raise ReviewError("Die Adresse muss Text oder leer sein.")
    compact = re.sub(r"[\s.,;/_:()\-]+", "", value.upper())
    if not compact:
        return address_parts(None)
    if not re.fullmatch(r"[MSU][A-F][1-5][12]", compact):
        raise ReviewError("Adresse ungültig: erwartet wird z. B. MA32 (M/S/U, A–F, 1–5, 1/2).")
    return dict(adresse=compact, seite=compact[0], zeile=compact[1],
                spalte=int(compact[2]), teilposition=int(compact[3]))


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as file:
            temporary = Path(file.name)
            json.dump(data, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ReviewStore:
    def __init__(self, source, corrections, image_root, project_root=ROOT):
        self.source = Path(source).resolve()
        self.corrections_path = Path(corrections).resolve()
        if self.source == self.corrections_path:
            raise ValueError("Scan-JSON und Korrekturdatei müssen verschieden sein.")
        self.image_root = Path(image_root).resolve()
        self.project_root = Path(project_root).resolve()
        self.mutex = threading.RLock()
        self.rows = {}
        self.warning = ""
        self.corrections = {}
        if self.corrections_path.exists():
            with self.corrections_path.open(encoding="utf-8") as file:
                saved = json.load(file)
            if not isinstance(saved, dict) or saved.get("version") != 1 or not isinstance(saved.get("eintraege"), dict):
                raise ValueError("Ungültige Korrekturdatei; Datei wird nicht überschrieben.")
            for key, value in saved["eintraege"].items():
                if (not isinstance(key, str) or not isinstance(value, dict) or "adresse" not in value
                        or not isinstance(value.get("basis_hash"), str)
                        or type(value.get("revision")) is not int or value["revision"] < 1
                        or not isinstance(value.get("text"), str)
                        or not isinstance(value.get("text_englisch"), str)
                        or not isinstance(value.get("geprueft"), bool)):
                    raise ValueError("Ungültiger Korrekturdatensatz; Datei wird nicht überschrieben.")
                address_parts(value.get("adresse"))
            self.corrections = saved["eintraege"]
        self.refresh()

    def refresh(self):
        # Der Scanner ersetzt atomar; ein geöffneter Dateideskriptor liest eine vollständige Version.
        try:
            with self.source.open(encoding="utf-8") as file:
                rows = json.load(file)
            if not isinstance(rows, list):
                raise ValueError("Scan-JSON muss eine Liste sein.")
            mapped = {}
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("bild"), str) or not row["bild"]:
                    raise ValueError("Ungültiger Scan-Datensatz.")
                if row["bild"] in mapped:
                    raise ValueError("Doppelter Bildpfad im Scan-JSON.")
                fingerprint(row)
                mapped[row["bild"]] = row
            self.rows = mapped
            self.warning = ""
        except (OSError, ValueError, UnicodeError):
            self.warning = "Scan-JSON momentan nicht lesbar. Der letzte gültige Stand bleibt sichtbar; Speichern ist vorübergehend gesperrt."

    def merged(self, key):
        original = self.rows[key]
        row = dict(original)
        correction = self.corrections.get(key)
        stale = bool(correction and correction["basis_hash"] != fingerprint(original))
        if correction:
            row.update(address_parts(correction["adresse"]))
            row.update(text=correction["text"], text_englisch=correction["text_englisch"])
        row.update(
            geprueft=bool(correction and correction["geprueft"] and not stale),
            manuell_geaendert=bool(correction and any(row.get(k) != original.get(k) for k in ("adresse", "text", "text_englisch"))),
            pruefung_veraltet=stale,
        )
        return row

    def snapshot(self):
        with self.mutex:
            self.refresh()
            items = []
            for key in sorted(self.rows):
                row = self.merged(key)
                row["_basis_hash"] = fingerprint(self.rows[key])
                row["_revision"] = self.corrections.get(key, {}).get("revision", 0)
                row["_scan"] = self.rows[key]
                items.append(row)
            return {"items": items, "warnung": self.warning}

    def save(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("bild"), str):
            raise ReviewError("Ein Bildpfad wird benötigt.")
        key = data["bild"]
        parts = address_parts(data.get("adresse"))
        if not isinstance(data.get("text"), str) or not isinstance(data.get("text_englisch"), str):
            raise ReviewError("Originaltext und englischer Text müssen Zeichenketten sein.")
        if not isinstance(data.get("geprueft"), bool) or type(data.get("revision")) is not int:
            raise ReviewError("Prüfmarkierung oder Revision ist ungültig.")
        with self.mutex:
            self.refresh()
            if self.warning:
                raise ReviewError(self.warning, 503)
            if key not in self.rows:
                raise ReviewError("Bild nicht mehr im Scan-JSON vorhanden.", 404)
            if data.get("basis_hash") != fingerprint(self.rows[key]):
                raise ReviewError("Das Scan-Ergebnis wurde geändert. Neue Scan-Daten ansehen und vor dem Speichern bestätigen.", 409)
            current = self.corrections.get(key, {}).get("revision", 0)
            if data["revision"] != current:
                raise ReviewError("Ein anderer Browser hat diesen Zettel geändert. Daten neu laden, bevor du speicherst.", 409)
            entry = dict(adresse=parts["adresse"], text=data["text"], text_englisch=data["text_englisch"],
                         geprueft=data["geprueft"], basis_hash=data["basis_hash"], revision=current + 1)
            updated = dict(self.corrections)
            updated[key] = entry
            atomic_json(self.corrections_path, {"version": 1, "eintraege": updated})
            self.corrections = updated
            return {"revision": entry["revision"]}

    def image(self, key):
        with self.mutex:
            self.refresh()
            if key not in self.rows:
                raise ReviewError("Bild nicht bekannt.", 404)
            path = (self.project_root / key).resolve()
            if not path.is_relative_to(self.image_root) or path.suffix.lower() not in (".jpg", ".png"):
                raise ReviewError("Bildpfad nicht erlaubt.", 403)
            try:
                return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            except OSError:
                raise ReviewError("Bilddatei nicht gefunden oder nicht lesbar.", 404) from None

    def export(self, kind):
        with self.mutex:
            self.refresh()
            if self.warning:
                raise ReviewError(self.warning, 503)
            rows = [self.merged(key) for key in sorted(self.rows)]
        if kind == "json":
            return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8"
        if kind != "csv":
            raise ReviewError("Exportformat nicht bekannt.", 404)
        # Zusätzliche zukünftige Scan-Felder auch im CSV erhalten.
        fields = SCAN_FIELDS + sorted({key for row in rows for key in row} - set(SCAN_FIELDS + REVIEW_FIELDS)) + REVIEW_FIELDS
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, address, store):
        self.store = store
        super().__init__(address, ReviewHandler)


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_data(self, data, content_type, status=200, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200):
        self.send_data(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def check_local_request(self):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        name, separator, requested_port = host.rpartition(":")
        valid_host = separator and requested_port == str(port)
        if valid_host and name != "localhost":
            try:
                ipaddress.IPv4Address(name)
            except ipaddress.AddressValueError:
                valid_host = False
        if not valid_host:
            raise ReviewError("Host nicht erlaubt.", 403)
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{host}":
            raise ReviewError("Anfrage aus fremder Website nicht erlaubt.", 403)

    def do_GET(self):
        try:
            self.check_local_request()
            url = urlsplit(self.path)
            path = url.path
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in assets:
                filename, mime = assets[path]
                self.send_data((ROOT / "review_ui" / filename).read_bytes(), mime)
            elif path == "/api/items":
                self.send_json(self.server.store.snapshot())
            elif path == "/api/image":
                key = parse_qs(url.query).get("bild", [""])[0]
                data, mime = self.server.store.image(key)
                self.send_data(data, mime)
            elif path in ("/api/export/json", "/api/export/csv"):
                kind = path.rsplit("/", 1)[1]
                data, mime = self.server.store.export(kind)
                self.send_data(data, mime, filename=f"postits_geprueft.{kind}")
            else:
                raise ReviewError("Nicht gefunden.", 404)
        except ReviewError as exc:
            self.send_json({"fehler": str(exc)}, exc.status)
        except OSError:
            self.send_json({"fehler": "Datei konnte nicht gelesen werden."}, 500)

    def do_POST(self):
        try:
            self.check_local_request()
            if self.path != "/api/corrections":
                raise ReviewError("Nicht gefunden.", 404)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ReviewError("JSON wird benötigt.", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ReviewError("Anfrage leer oder zu groß.", 413)
                data = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise ReviewError("Ungültiges JSON.") from None
            self.send_json(self.server.store.save(data))
        except ReviewError as exc:
            self.send_json({"fehler": str(exc)}, exc.status)
        except OSError:
            self.send_json({"fehler": "Korrektur konnte nicht gespeichert werden."}, 500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "postits.json")
    parser.add_argument("--corrections", type=Path, default=ROOT / "postits_korrekturen.json")
    parser.add_argument("--image-root", type=Path, default=ROOT / "Worshop scan")
    parser.add_argument("--host", default="0.0.0.0", help="Bind-Adresse; Standard: alle IPv4-Netzwerkadressen")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port muss zwischen 1 und 65535 liegen.")
    try:
        corrections = args.corrections.resolve()
        if corrections == args.input.resolve():
            raise ValueError("Scan-JSON und Korrekturdatei müssen verschieden sein.")
        corrections.parent.mkdir(parents=True, exist_ok=True)
        # Die Lock-Datei darf bestehen bleiben: flock gibt die Sperre beim Beenden frei.
        with Path(str(corrections) + ".lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Diese Korrekturdatei wird bereits von einem Prüfserver verwendet.") from None
            store = ReviewStore(args.input, corrections, args.image_root)
            with ReviewServer((args.host, args.port), store) as server:
                print(f"Prüfserver lauscht auf {args.host}:{args.port}", flush=True)
                print(f"Auf diesem Rechner: http://127.0.0.1:{args.port}", flush=True)
                if args.host == "0.0.0.0":
                    print(f"Von anderen Rechnern: http://<IP-dieses-PCs>:{args.port}", flush=True)
                print(f"Korrekturen: {corrections}\nBeenden mit Strg+C.", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    print("\nPrüfserver beendet.")
        return 0
    except (OSError, ValueError, ReviewError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
