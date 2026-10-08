#!/usr/bin/env python3
"""Post-it-Fotos über die TU-Braunschweig-KI-Toolbox transkribieren."""

import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
URL = "https://ki-toolbox.tu-braunschweig.de/api/v1/chat/send"
FIELDS = [
    "bild", "adresse_roh", "adresse", "seite", "zeile", "spalte",
    "teilposition", "text", "text_englisch", "status", "hinweis", "fehler",
    "sha256", "modell", "prompt_sha256",
]
PROMPT = """Transkribiere das einzelne Post-it auf dem angehängten Foto.
Suche die Adresse auf dem GESAMTEN Zettel, nicht nur oben links.
Eine vollständige Adresse besteht in dieser Reihenfolge aus:
Seite M, S oder U; Zeile A bis F; Spalte 1 bis 5; Teilposition 1 oder 2.
Leerzeichen oder Trennzeichen zwischen diesen vier Zeichen sind möglich.
Erfinde und ergänze KEINE fehlenden Zeichen. Bei unvollständiger Adresse
gib nur die tatsächlich gelesenen Zeichen als adresse_roh an, bei fehlender
Adresse null. Andere Zeichen nicht passend zum Schema umdeuten.
Der text enthält den eigentlichen Inhalt OHNE den separaten Adresscode.
Schreibe text originalgetreu in der ursprünglichen Sprache, ohne Übersetzung,
Zusammenfassung oder Rechtschreibkorrektur. Erhalte Zeilenumbrüche und Pfeile.
Erzeuge zusätzlich text_englisch als vollständige englische Übersetzung
von text. Übersetze alle nicht englischen Textstellen sinngemäß und ohne
Zusammenfassung oder Ergänzungen. Bereits englische Textstellen bleiben
unverändert; bei vollständig englischem Inhalt ist text_englisch gleich text.
Bei gemischten Sprachen übersetze nur die nicht englischen Teile. Erhalte
Zeilenumbrüche, Listen, Pfeile, Zahlen und Eigennamen in beiden Textfeldern.
Unleserliche Stellen markiere in text als [unleserlich] und in text_englisch
als [unreadable]. Übersetze den Marker auch bei sonst englischem Text.
Erfinde keine Übersetzung für unleserliche Stellen.
Wenn Adresse, Inhalt oder Übersetzung
unsicher, mehrdeutig oder unleserlich sind, setze pruefen auf true und
erkläre kurz im hinweis, was geprüft werden muss.
Antworte ausschließlich mit einem JSON-Objekt ohne Markdown:
{"adresse_roh":"MA32", "text":"erste Zeile\\nzweite Zeile",
 "text_englisch":"first line\\nsecond line",
 "pruefen":false, "hinweis":""}
"""
PROMPT_HASH = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()


class ScanError(Exception):
    """Ein einzelnes Bild konnte nicht verarbeitet werden."""


class StopRun(ScanError):
    """Keine weiteren Anfragen starten (Authentifizierung/unklarer Auftrag)."""


def normalize_address(raw):
    if raw is None:
        return None
    compact = re.sub(r"[\s.,;/_:()\-]+", "", raw.upper())
    return compact if re.fullmatch(r"[MSU][A-F][1-5][12]", compact) else None


def parse_transcription(response):
    value = response.strip()
    # Einige Modelle liefern trotz Anweisung einen einzelnen Markdown-Codeblock.
    if value.startswith("```"):
        value = re.sub(r"\A```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```\Z", "", value)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        raise ScanError("Modellantwort ist kein gültiges JSON.") from None
    if not isinstance(data, dict):
        raise ScanError("Modellantwort muss ein JSON-Objekt sein.")
    raw = data.get("adresse_roh")
    if (raw is not None and not isinstance(raw, str)) or "adresse_roh" not in data:
        raise ScanError("adresse_roh fehlt oder hat einen ungültigen Typ.")
    if not isinstance(data.get("text"), str) or not isinstance(data.get("pruefen"), bool):
        raise ScanError("text oder pruefen fehlt oder hat einen ungültigen Typ.")
    if not isinstance(data.get("hinweis"), str):
        raise ScanError("hinweis fehlt oder hat einen ungültigen Typ.")
    if not isinstance(data.get("text_englisch"), str):
        raise ScanError("text_englisch fehlt oder hat einen ungültigen Typ.")
    if data["text"].strip() and not data["text_englisch"].strip():
        raise ScanError("Die englische Übersetzung ist leer, obwohl Originaltext vorhanden ist.")
    address = normalize_address(raw)
    notes = [data["hinweis"]] if data["hinweis"] else []
    if address is None:
        notes.append("Adresse fehlt, ist unvollständig oder entspricht nicht dem Schema.")
    if not data["text"].strip():
        notes.append("Kein Zetteltext erkannt.")
    review = data["pruefen"] or address is None or not data["text"].strip()
    review = review or "[unleserlich]" in data["text"].lower()
    review = review or "[unreadable]" in data["text_englisch"].lower()
    return {
        "adresse_roh": raw, "adresse": address,
        "seite": address[0] if address else None,
        "zeile": address[1] if address else None,
        "spalte": int(address[2]) if address else None,
        "teilposition": int(address[3]) if address else None,
        "text": data["text"], "text_englisch": data["text_englisch"],
        "status": "prüfen" if review else "ok",
        "hinweis": " ".join(notes), "fehler": "",
    }


def read_stream(response):
    size = 0
    try:
        for line in response.iter_lines():
            if not line.strip():
                continue
            size += len(line)
            if size > 4 * 1024 * 1024:
                raise StopRun("API-Stream zu groß; Abschluss des Auftrags ist unklar.")
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise StopRun("Ungültiger API-Stream; Abschluss des Auftrags ist unklar.") from None
            if not isinstance(event, dict):
                raise StopRun("Ungültiges Stream-Ereignis; Abschluss des Auftrags ist unklar.")
            if event.get("error"):
                raise StopRun("Die API meldet einen Fehler im Stream; Auftrag unklar.")
            if event.get("type") == "done":
                result = event.get("response")
                if not isinstance(result, str):
                    raise ScanError("API-Abschluss enthält keinen Antworttext.")
                return result
    except requests.RequestException:
        raise StopRun("Verbindung im Stream abgebrochen; Serverauftrag könnte noch laufen.") from None
    raise StopRun("API-Stream ohne done beendet; Serverauftrag könnte noch laufen.")


def retry_delay(value, attempt):
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return float(2 ** (attempt + 1))


def request_transcription(path, token, model, stop):
    payload = {"thread": None, "prompt": PROMPT, "model": model}
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    for attempt in range(3):
        if stop.is_set():
            raise ScanError("Lauf gestoppt; keine weitere Anfrage gesendet.")
        try:
            with path.open("rb") as photo:
                # Beide Parts tragen einen eigenen Content-Type. requests setzt die Boundary.
                parts = {
                    "jsonBody": (None, json.dumps(payload, ensure_ascii=False), "application/json"),
                    "chatAttachment": (path.name, photo, mime),
                }
                with requests.post(
                    URL, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    files=parts, stream=True, timeout=(15, 180), allow_redirects=False,
                ) as response:
                    if response.status_code in (401, 403) or 300 <= response.status_code < 400:
                        raise StopRun(f"API-Zugriff abgelehnt (HTTP {response.status_code}); Token prüfen.")
                    if response.status_code == 429 and attempt < 2:
                        delay = retry_delay(response.headers.get("Retry-After"), attempt)
                    elif response.status_code != 200:
                        raise ScanError(f"API-Anfrage fehlgeschlagen (HTTP {response.status_code}).")
                    else:
                        return parse_transcription(read_stream(response))
            # Antwort schließen, bevor gewartet oder erneut gesendet wird.
            if stop.wait(delay):
                raise ScanError("Lauf während der Wartezeit gestoppt.")
        except requests.RequestException:
            raise StopRun("Verbindungsfehler/Timeout; Serverauftrag könnte noch laufen. Kein automatischer Retry.") from None
    raise ScanError("API-Ratenlimit erreicht.")


def scan_one(path, token, model, stop):
    row = dict.fromkeys(FIELDS)
    row.update(bild=os.path.relpath(path, ROOT), modell=model, prompt_sha256=PROMPT_HASH,
               status="fehler", text="", text_englisch="", hinweis="", fehler="")
    try:
        with path.open("rb") as source:
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        row["sha256"] = digest.hexdigest()
        row.update(request_transcription(path, token, model, stop))
        return row, False
    except StopRun as exc:
        stop.set()
        row["fehler"] = str(exc)
        return row, True
    except (ScanError, OSError) as exc:
        row["fehler"] = str(exc) if isinstance(exc, ScanError) else "Bilddatei konnte nicht gelesen werden."
        return row, False


def atomic_write(path, writer, encoding):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding=encoding, newline="", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            writer(file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_results(base, rows):
    ordered = sorted(rows.values(), key=lambda row: row["bild"])
    atomic_write(Path(str(base) + ".json"), lambda file: json.dump(ordered, file, ensure_ascii=False, indent=2), "utf-8")

    def write_csv(file):
        writer = csv.DictWriter(file, fieldnames=FIELDS, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)

    atomic_write(Path(str(base) + ".csv"), write_csv, "utf-8-sig")


def load_results(base):
    path = Path(str(base) + ".json")
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as file:
        rows = json.load(file)
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("bild"), str) for row in rows):
        raise ValueError("Vorhandene JSON-Ergebnisdatei hat ein ungültiges Format.")
    return {row["bild"]: row for row in rows}


def unchanged(path, row, model):
    if not row or row.get("status") not in ("ok", "prüfen") or row.get("modell") != model:
        return False
    if row.get("prompt_sha256") != PROMPT_HASH or not isinstance(row.get("text_englisch"), str):
        return False
    try:
        with path.open("rb") as file:
            digest = hashlib.sha256()
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        return row.get("sha256") == digest.hexdigest()
    except OSError:
        return False


def run(args, token):
    folder = args.input.resolve()
    if not folder.is_dir():
        raise ValueError(f"Bilderordner nicht gefunden: {folder}")
    images = sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in (".jpg", ".png"))
    rows = load_results(args.output)
    pending = [path for path in images if args.force or not unchanged(path, rows.get(os.path.relpath(path, ROOT)), args.model)]
    if args.limit is not None:
        pending = pending[:args.limit]
    print(f"{len(images)} Bilder gefunden; {len(pending)} zu verarbeiten; {args.workers} Worker.", flush=True)
    stop = threading.Event()
    iterator = iter(pending)
    fatal = False
    failures = 0
    completed = 0
    executor = ThreadPoolExecutor(max_workers=args.workers)
    active = {}

    def fill_slots():
        while len(active) < args.workers and not stop.is_set():
            path = next(iterator, None)
            if path is None:
                break
            active[executor.submit(scan_one, path, token, args.model, stop)] = path

    try:
        fill_slots()
        while active:
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                active.pop(future)
                row, abort = future.result()
                rows[row["bild"]] = row
                fatal = fatal or abort
                failures += row["status"] == "fehler"
                completed += 1
                save_results(args.output, rows)
                print(f"[{completed}/{len(pending)}] {row['bild']}: {row['status']}" + (f" — {row['fehler']}" if row["fehler"] else ""), flush=True)
            fill_slots()
    except KeyboardInterrupt:
        stop.set()
        fatal = True
        print("Abbruch: laufende Antworten werden noch abgewartet und gespeichert.", file=sys.stderr, flush=True)
        for future in active:
            row, _ = future.result()
            rows[row["bild"]] = row
            save_results(args.output, rows)
    finally:
        stop.set()
        executor.shutdown(wait=True, cancel_futures=True)
    save_results(args.output, rows)
    print(f"Ergebnisse: {args.output}.json und {args.output}.csv", flush=True)
    if fatal:
        print("Lauf gestoppt. Bei unklarem Serverauftrag vor einem Neustart dessen Abschluss in der Toolbox prüfen.", file=sys.stderr)
    return 2 if fatal else (1 if failures else 0)


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Wert muss mindestens 1 sein.")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "Worshop scan", help="Bilderordner (auch Unterordner)")
    parser.add_argument("--output", type=Path, default=ROOT / "postits", help="Ausgabepfad ohne Endung")
    parser.add_argument("--model", default="gpt-5.6-luna", help="Bildfähige externe Toolbox-Modell-ID")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--limit", type=positive_int, help="Maximale Anzahl noch zu verarbeitender Bilder")
    parser.add_argument("--force", action="store_true", help="Auch bereits erkannte Bilder neu verarbeiten")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    token = os.getenv("KI_TOOLKIT_TOKEN", "").strip()
    if not token:
        parser.error("KI_TOOLKIT_TOKEN fehlt. Bitte in .env oder der Umgebung setzen.")
    try:
        # Verhindert mehrere gleichzeitige Skriptinstanzen, auch bei anderer Ausgabe.
        with (ROOT / ".scan_postits.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("Es läuft bereits eine Instanz dieses Skripts.", file=sys.stderr)
                return 2
            return run(args, token)
    except (OSError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
