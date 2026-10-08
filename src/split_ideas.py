"""Split the hand-edited notes into one record per idea, for embedding and clustering.

Input: data/raw/postits_clean.xlsx, the cleaned notes with a hand-edited column `text_clean_edit`
(edited by a colleague after reading the clusters). Where one post-it holds two ideas, the edit
separates them with ";" (e.g. row 19 "Skilled labor shortage ; Demographics"). A ";" that did not
separate ideas was rewritten in the edit (row 165 to "leads to", rows 279 and 303 to ","), so
every ";" left is a split.

Output: data/processed/ideas.json, one record per idea:
  row        the post-it (row in data/raw/postits_geprueft (2).json)
  idea       1, 2, ... within the post-it;  n_ideas  how many ideas the post-it has
  id         "19" for a single idea, "19.1" / "19.2" for split ones
  text_clean the idea's text (what gets embedded);  note  the whole edited post-it
  text_tokens lowercase words without the arrow words, for the cluster theme words

Usage:
    python src/split_ideas.py
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "raw" / "postits_clean.xlsx"
OUT = ROOT / "data" / "processed" / "ideas.json"
SPLIT = ";"
# words the cleaning put in for arrows (see ARROW_WORDS in clean_text.py); not theme words
ARROW_WORDS_RE = re.compile(r"\b(leads to|because|e\.\s?g\.?|imply|to)(?!\w)", re.IGNORECASE)


def tokens(text):
    return " ".join(re.findall(r"[a-z0-9][a-z0-9'-]*", ARROW_WORDS_RE.sub(" ", text.lower())))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    df = pd.read_excel(SOURCE)
    assert len(df) == 336 and df["row"].is_unique, "expected the 336 post-its, one row each"
    assert df["text_clean_edit"].notna().all(), "every post-it needs a text_clean_edit"
    records = []
    for _, r in df.sort_values("row").iterrows():
        note = " ".join(str(r["text_clean_edit"]).split())
        ideas = [i.strip() for i in note.split(SPLIT)]
        assert all(ideas), f"row {r['row']}: empty idea in {note!r}"
        for k, idea in enumerate(ideas, 1):
            records.append({"row": int(r["row"]), "idea": k, "n_ideas": len(ideas),
                            "id": str(r["row"]) if len(ideas) == 1 else f"{r['row']}.{k}",
                            "bild": r["bild"], "adresse": r["adresse"],
                            "text_clean": idea, "note": note, "text_tokens": tokens(idea)})
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    split = df[df["text_clean_edit"].str.contains(SPLIT, regex=False)]
    print(f"{len(df)} post-its -> {len(records)} ideas ({len(split)} post-its split) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
