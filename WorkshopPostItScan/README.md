# Post-it-Scan

Das Skript transkribiert die JPG-/PNG-Fotos in `Worshop scan` über die externe
KI der TU-Braunschweig-Toolbox. Die Bilder werden unverändert hochgeladen;
Texterkennung, Adresserkennung und Übersetzung ins Englische erfolgen dort,
zusammen in einer Anfrage je Bild. Standardmodell ist
`gpt-5.6-luna`. Die API-Anbindung folgt `OAS3-CHAT.yaml`.

## Einrichtung und Start

Python ab 3.10 unter Linux/macOS, im Projektinterpreter:

```bash
python -m pip install -r requirements.txt
```

In der lokalen `.env` steht der Schlüssel (bereits eingerichtet):

```dotenv
KI_TOOLKIT_TOKEN=dein_token
```

Zwei noch nicht verarbeitete Bilder testen:

```bash
python scan_postits.py --limit 2
```

Alle Bilder digitalisieren beziehungsweise einen Lauf fortsetzen:

```bash
python scan_postits.py
```

Weitere Optionen:

```bash
python scan_postits.py --input "Worshop scan" --output postits --workers 1
python scan_postits.py --model gpt-6-astra --limit 2 --output postits_vergleich
python scan_postits.py --force --limit 2
```

`--output` bezeichnet den Dateipfad **ohne** Endung. `--limit` zählt die noch
zu verarbeitenden Bilder. Unveränderte Bilder mit Status `ok` oder `prüfen`
werden anhand von SHA-256, Modell und Prompt-Version übersprungen. Geänderte
Bilder, ein geändertes Modell/Prompt und Fehlerdatensätze werden erneut verarbeitet.
Alte Ergebnisse ohne englische Übersetzung werden automatisch neu verarbeitet;
dafür genügt der normale Aufruf ohne `--force`. Nach einem Abbruch werden die
bereits mit dem neuen Prompt verarbeiteten Bilder beim Fortsetzen übersprungen.
`--force` erzwingt die Verarbeitung. Bereits gespeicherte Datensätze anderer
Bilder bleiben erhalten; gleiche Adressen werden nicht zusammengelegt.

## Ergebnisse

`postits.json` und `postits.csv` enthalten je Bild den relativen Dateipfad,
die gelesene Rohadresse, die normalisierte Adresse, Seite, Zeile, Spalte,
Teilposition, Originaltext (`text`), englische Übersetzung (`text_englisch`),
Status und Prüf-/Fehlerhinweise. SHA-256, Modell und Prompt-Hash
(`prompt_sha256`) ermöglichen die Wiederaufnahme. In CSV sind leere Werte leere Felder;
JSON verwendet `null`. CSV verwendet Semikolon und UTF-8 mit BOM für Excel.

Eine gültige Adresse wie `M A 3 2` wird `MA32`. Seite ist `M`, `S` oder `U`,
Zeile `A` bis `F`, Spalte `1` bis `5`, Teilposition `1` oder `2`.
Die Bedeutung links/rechts ist noch nicht festgelegt; die Zahl bleibt erhalten.
Fehlende oder unvollständige Adressen werden nicht ergänzt. Der separate
Adresscode gehört nicht zum Zetteltext. Zeilenumbrüche und Sprache bleiben
erhalten, unleserliche Stellen erscheinen als `[unleserlich]`.
In `text_englisch` werden alle nicht englischen Stellen übersetzt; bereits
englischer Text bleibt unverändert. Zeilenumbrüche und Pfeile bleiben erhalten.
Unleserliche Stellen werden als `[unreadable]` markiert. Die Originalfassung
wird nicht durch die Übersetzung ersetzt. Während eines fortgesetzten Laufs
enthält der Export auch noch alte Datensätze, bis diese neu verarbeitet sind.

Status `prüfen` bedeutet, dass Adresse oder Text manuell kontrolliert werden
sollten. Die automatische Erkennung kann auch bei Status `ok` falsch liegen.

## Parallelität und Fehler

Es laufen höchstens zwei Anfragen gleichzeitig, einschließlich Upload,
Streaming und Wiederholungen. Ein Worker wird erst nach Abschluss frei.
Eine lokale Prozesssperre verhindert parallele Skriptinstanzen aus diesem
Projekt. Andere Anwendungen, die denselben Token nutzen, werden nicht erfasst.

Ergebnisse werden nach jedem fertigen Bild gespeichert. Einzelne HTTP- oder
Antwortfehler stehen im Export. HTTP 429 wird höchstens zweimal wiederholt,
wobei `Retry-After` beachtet wird. Authentifizierungsfehler, Redirects,
Timeouts und unvollständige Streams stoppen neue Anfragen; bereits laufende
Antworten werden noch abgewartet. Timeout: 15 Sekunden beim Verbindungsaufbau,
180 Sekunden ohne Datenempfang. Bei einem unklaren Serverauftrag zuerst in
der Toolbox prüfen, ob er beendet ist, bevor das Skript erneut gestartet wird.
Es löscht keine Chats auf dem Server.

Exitcode: `0` abgeschlossen (auch mit Prüfhinweisen), `1` einzelne Bildfehler,
`2` Lauf gestoppt oder Einrichtung fehlerhaft. `Strg+C` stoppt neue Anfragen
und wartet laufende Antworten ab, um Ergebnisse zu sichern.

Tests ohne API-Zugriff:

```bash
python -m unittest discover -s tests -v
```

## Post-its im Browser prüfen und bearbeiten

Der Prüfserver nutzt ausschließlich die Python-Standardbibliothek. Er benötigt
keinen Token und kann parallel zum Scanner laufen. In einem zweiten Terminal:

```bash
.venv/bin/python review_postits.py
```

Auf diesem Rechner `http://127.0.0.1:8000` im Browser öffnen. Von anderen
Rechnern `http://<IP-des-Server-PCs>:8000` verwenden, beispielsweise
`http://134.169.42.204:8000` für die derzeitige Kabelnetz-Adresse dieses PCs.
`0.0.0.0` ist die Bind-Adresse und nicht die Adresse zum Weitergeben.
Nach einer Änderung der Bind-Adresse den Prüfserver neu starten; der Scanner
kann weiterlaufen. Links den Zettel auswählen,
das Foto vergrößern und rechts Adresse, Originaltext und englische Übersetzung
korrigieren. „Als geprüft markieren“ anhaken und „Speichern“ oder
„Speichern & nächster“ verwenden. Die Markierung wird erst beim Speichern
übernommen. Strg/Cmd+S speichert; Vor/Zurück navigiert zwischen Zetteln.
Suchfeld und Filter helfen, ungeprüfte Zettel und Scan-Probleme zu finden.
Die Seite aktualisiert die Scan-Ergebnisse alle fünf Sekunden, ohne
ungespeicherte Eingaben zu überschreiben.

`postits.json` wird **nicht bearbeitet**. Alle Korrekturen und Prüfmarkierungen
stehen in `postits_korrekturen.json`. Ändert der Scanner einen bearbeiteten
Datensatz, bleibt deine Korrektur erhalten, wird aber als veraltet markiert.
Unter „Aktueller Scan zum Vergleich“ kannst du die neue Erkennung ansehen.
Bei ungespeicherten Eingaben bestätige die neue Grundlage mit „Neue Scan-Daten
geprüft“, markiere den Zettel gegebenenfalls erneut als geprüft und speichere.
Bei einer bestehenden veralteten Korrektur genügt das erneute Prüfen und
Speichern. Speicherkonflikte zwischen Browser-Tabs werden gemeldet; „Daten neu
laden“ übernimmt den aktuellen Stand und verwirft nach Rückfrage offene Eingaben.

„JSON exportieren“ und „CSV exportieren“ laden den zusammengeführten Stand
als `postits_geprueft.json` beziehungsweise `postits_geprueft.csv` herunter.
Der Export enthält **alle** aktuell im Scan vorhandenen Zettel, unabhängig vom
Filter. Die bisherigen Felder bleiben erhalten, ergänzt um `geprueft`,
`manuell_geaendert` und `pruefung_veraltet`. Der ursprüngliche Scan-`status`
bleibt erhalten; `geprueft` beschreibt die manuelle Prüfung. Die Rohadresse
`adresse_roh` bleibt die KI-Lesung, während `adresse` die Korrektur enthält.
Originalbilddateien werden nicht verändert; es erfolgt keine automatische
Neuübersetzung auf der Prüfseite.

Andere Pfade oder Port:

```bash
.venv/bin/python review_postits.py --input postits.json --corrections postits_korrekturen.json --port 8001
```

Bei einem anderen Bilderordner zusätzlich `--image-root /pfad/zum/bilderordner`
verwenden. Bildpfade aus der Scan-JSON werden wie beim Scanner relativ zum
Projektordner aufgelöst. Es werden nur referenzierte JPG-/PNG-Dateien innerhalb
des erlaubten Bilderordners ausgeliefert. Der Server bindet standardmäßig
an `0.0.0.0` (alle IPv4-Adressen). Mit `--host 127.0.0.1` lässt sich der
Zugriff wieder auf diesen Rechner beschränken. Erreichbare Teilnehmer können
über denselben Server gemeinsam prüfen; Firewall und Netzwerk müssen Port
8000 zulassen. Eine Prozesssperre verhindert mehrere Prüfserver mit derselben
Korrekturdatei. Mit Strg+C beenden; gespeicherte Änderungen bleiben bestehen.
Bei ungültiger Korrekturdatei startet der Server nicht und überschreibt sie
nicht. Ist die Scan-Datei zeitweise unlesbar, bleibt die letzte gültige Ansicht
erhalten; Speichern und Export sind bis zur nächsten gültigen Version gesperrt.

`.env`, die gespeicherte Kontoseite und Standard-Ergebnisdateien sind in
`.gitignore` eingetragen. Für andere Ausgabepfade gegebenenfalls eigene
Ignore-Einträge ergänzen.
