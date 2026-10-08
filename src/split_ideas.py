"""Split the cleaned notes into one record per idea, for embedding and clustering.

Text: data/processed/postits_clean.json (`text_clean`, from src/clean_text.py).
Where to split: data/raw/postits_clean.xlsx, the same notes hand-edited by a colleague
(`text_clean_edit`); a ";" there separates two ideas on one post-it. Only her split decisions
are used, not her other wording changes.

Our text has ";" where an arrow meant a list. She kept it where it separates ideas and rewrote it
where it did not (row 165 to "leads to", rows 279 and 303 to ","), so those three stay whole.
Where she split a note that has no ";" in our text, SPLIT_AT says where her split falls.

Output: data/processed/ideas.json, one record per idea:
  row        the post-it (row in data/raw/postits_geprueft (2).json)
  idea       1, 2, ... within the post-it;  n_ideas  how many ideas the post-it has
  id         "19" for a single idea, "19.1" / "19.2" for split ones
  text_clean the idea's text (what gets embedded);  note  the whole post-it
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
CLEAN = ROOT / "data" / "processed" / "postits_clean.json"
EDITED = ROOT / "data" / "raw" / "postits_clean.xlsx"
OUT = ROOT / "data" / "processed" / "ideas.json"
SPLIT = ";"
# her split in our wording, for the notes she split where our text has no ";" (her edit in the comment)
SPLIT_AT = {
    12: " (",         # Cycling is cheap compared to riding a car ; social view status symbol
    19: " or ",       # Skilled labor shortage ; Demographics
    46: ", ",         # no illnesses anymore; AI can detect illnesses
    99: ", ",         # Protection against hacker attacks ; natural disasters
    149: " leads to ",  # coordinated traffic signal control ; Backup for routes
    176: " and ",     # Eco-friendly public spaces ; Eco-friendly mindset from everyone
    282: " or ",       # cooking ; climate change
}
# words the cleaning put in for arrows (see ARROW_WORDS in clean_text.py); not theme words
ARROW_WORDS_RE = re.compile(r"\b(leads to|because|e\.\s?g\.?|to)(?!\w)", re.IGNORECASE)


def tokens(text):
    return " ".join(re.findall(r"[a-z0-9][a-z0-9'-]*", ARROW_WORDS_RE.sub(" ", text.lower())))


def split(row, text, n_wanted):
    """Our text cut where she split it; an idea that was in brackets loses the closing one."""
    if n_wanted == 1:
        return [text]
    sep = SPLIT_AT.get(row, SPLIT)
    ideas = [i.strip() for i in text.split(sep)]
    ideas = [i[:-1] if i.endswith(")") and i.count(")") > i.count("(") else i for i in ideas]
    assert len(ideas) == n_wanted and all(ideas), f"row {row}: {text!r} split at {sep!r} gives {ideas}"
    return ideas


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    clean = json.load(open(CLEAN, encoding="utf-8"))
    edited = pd.read_excel(EDITED).set_index("row")["text_clean_edit"]
    assert len(edited) == len(clean) == 336, "expected the 336 post-its"
    n_ideas = {row: str(t).count(SPLIT) + 1 for row, t in edited.items()}
    assert set(SPLIT_AT) <= {row for row, n in n_ideas.items() if n > 1}, "SPLIT_AT row she did not split"
    records = []
    for r in clean:
        ideas = split(r["row"], r["text_clean"], n_ideas[r["row"]])
        for k, idea in enumerate(ideas, 1):
            records.append({"row": r["row"], "idea": k, "n_ideas": len(ideas),
                            "id": str(r["row"]) if len(ideas) == 1 else f"{r['row']}.{k}",
                            "bild": r["bild"], "adresse": r["adresse"],
                            "text_clean": idea, "note": r["text_clean"], "text_tokens": tokens(idea)})
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    n_split = sum(n > 1 for n in n_ideas.values())
    print(f"{len(clean)} post-its -> {len(records)} ideas ({n_split} post-its split) -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
