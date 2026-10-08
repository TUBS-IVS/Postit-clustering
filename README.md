# Post-it clustering

Clean the 336 workshop post-it notes, embed them, project them to 3D and cluster them.

## Layout

```
WorkshopPostItScan/ the step before this project (own README): scan_postits.py read the post-its with the
                   KI-Toolbox, review_postits.py checked them and exported data/raw/postits_geprueft (2).json.
                   The 336 scans are in "Worshop scan/"; each note's `bild` path is relative to this folder.
                   Linux/macOS only (it uses fcntl); its .venv is a macOS/Linux environment.
data/raw/          source export, untouched (use the JSON, not the CSV; see docs)
data/processed/    postits_clean.json (canonical), postits_clean.xlsx (for review), spellcheck_report.csv
data/review/       LLM review: saved replies, the blind check, and the sheets to decide on
docs/              text_cleaning_rules.md: the cleaning spec
src/clean_text.py  cleaning, following the rules file; every rule asserts it matched
src/clean_basic.py stage 1 only: everything except the arrow rules; arrows left as written
src/spellcheck.py  report of words an English dictionary does not know
src/llm_review.py  LLM suggestions for translations and arrow meanings, for manual review
src/ki_toolbox.py  client for the KI-Toolbox chat API
src/merge_review.py compares the model's review with the blind check; builds the sheets to decide on
src/embed_cluster.py SBERT embeddings -> UMAP 3D -> HDBSCAN clusters with theme words; writes CSV + pages
src/plot_html.py   the interactive 3D pages (also redraws them from the saved CSVs)
```

## Run

```
pip install -r requirements.txt
python src/clean_text.py
python src/clean_basic.py   # optional: stage-1 output, postits_basic.json
python src/spellcheck.py
python src/embed_cluster.py                     # clusters + 3D pages (downloads the SBERT model once)
python src/embed_cluster.py --layout supervised # cleaner picture, but distances are less faithful
python src/plot_html.py                         # redraw the pages from the saved CSVs, no re-embedding
```

## View the clusters

**[data/processed/clusters_all-mpnet-base-v2_simple.html](data/processed/clusters_all-mpnet-base-v2_simple.html)**:
336 notes, SBERT `all-mpnet-base-v2`, 24 clusters + 50 noise (HDBSCAN min_cluster_size 5, min_samples 2). GitHub shows HTML as source, so download
the file (Raw → save, or clone the repo) and open it in a browser; it needs internet for plotly.js.

- drag to rotate, scroll to zoom, hover a dot to read the note
- click a cluster in the sidebar to focus it and list its notes; click a theme word to search it
- search box: highlights matching notes in the plot; toggles hide the noise and the labels

Theme words are each cluster's most typical words (class-based TF-IDF), not hand-written names.
Locally, `python src/embed_cluster.py` also writes `clusters_<model>.html`: the same page with source
details in the hover (German original, post-it address, scan) and a link to the scan. It is not in git
(4.6 MB, and the scans are not in the repo).

## LLM review (suggestions only)

The model (`gpt-6-astra`) runs on TU Braunschweig's KI-Toolbox (`src/ki_toolbox.py`).

1. `.env` holds the KI-Toolbox token in `OPENAI_API_KEY`, plus `LLM_MODEL` and `LLM_API_URL`. Never share or commit it.
2. Check what will be sent, then try a few notes:
   ```
   python src/llm_review.py translations --dry-run
   python src/llm_review.py translations --rows 74 87
   ```
3. Run both tasks: `translations` (82 notes whose English differs from the original) and `arrows` (104 arrows in 85 notes).
4. `python src/merge_review.py` compares the model's answers with an independent blind review by Claude agents
   (`data/review/claude/`), and adds a third review where the two disagree.
5. Open `data/review/*_checked.xlsx` and put `y` in the `accept` column of each suggestion you agree with.
   Accepted fixes are then added to the rule tables in `clean_text.py`. No model changes the data directly.

Valid replies are saved in `data/review/raw/` and reused, so a rerun only calls the API for notes not yet reviewed.
The API has no temperature, so a fresh run can answer differently. Every call also creates a chat in your
KI-Toolbox history, which is deleted after 14 days.

## Changes beyond the rules file

- **Arrows (step 11):** each arrow becomes the word for its meaning instead of always "leads to":
  cause → "leads to", explanation → "because", example → "e.g.", list → ";", heading → ":",
  attribute → ",", direction → "to". The meanings are in `ARROW_LABELS` in `clean_text.py`, from the
  LLM review. Only about half of the arrows mean "leads to". If the writer already wrote the word
  after the arrow (as in "-> e.g., no insurance"), it is not added a second time.
- **`text_tokens`** leaves out these arrow words, so it holds only the participants' own words
  ("leads" would otherwise be the most frequent token).
- **`REVIEW_FIXES`** in `clean_text.py` holds the meaning fixes accepted on 2026-10-08:
  - mistranslations in 10 notes (for example *Angstraum* → "Space that feels unsafe", *Klimakleber* →
    "climate activists gluing themselves to roads");
  - fixes in 11 notes after reading the scans (for example "zero cushion housing" → "zero emission housing",
    `entriefree` → "free entry", `autonimization` → "automation", "(PT)" → "(IT)" in row 162);
  - row 160's arrow, which names the idea before it, becomes "is".
- **The three "likely" fixes in Section C** were checked on the scans. `desle` is "desk"
  (singular); "insulation" and "through" are right.
- Row 200: `Eco- status` → `Eco-status` (failed the Section D hyphen check).
- **`SPELLCHECK_TYPOS`** in `clean_text.py`: 24 spell-checker hits with one obvious fix,
  including run-together compounds such as `Datacenters` → `data centers`.

The evidence for every review decision is in `data/review/` (`*_checked.xlsx` and `claude/`).

## Open items (left as written)

- `Care Spaces` (307): the scan confirms the words, but not what they mean.
- `FSS` (50) and `DB100` (278, probably the BahnCard 100): abbreviations, transcribed correctly.
- `(like MA31)` (38): refers to another cell of the workshop board.
