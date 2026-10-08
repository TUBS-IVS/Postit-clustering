"""List words in text_tokens that an English dictionary does not know.

Report only; nothing is changed. Confirmed typos go into TYPOS in clean_text.py.
Run after clean_text.py:
    python src/spellcheck.py
"""

from collections import defaultdict
from pathlib import Path

import pandas as pd
from spellchecker import SpellChecker

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "processed" / "postits_clean.json"
OUT = ROOT / "data" / "processed" / "spellcheck_report.csv"


def main():
    df = pd.read_json(SOURCE)
    spell = SpellChecker()
    rows = defaultdict(list)
    for row, tokens in zip(df["row"], df["text_tokens"]):
        for token in tokens.split():
            # A hyphenated token is fine if all of its parts are known words.
            parts = [p for p in token.split("-") if not p.isdigit()]
            if spell.unknown(parts):
                rows[token].append(row)

    report = pd.DataFrame(
        [{"word": w, "count": len(r), "rows": " ".join(map(str, sorted(set(r)))),
          "suggestion": spell.correction(w) or ""}
         for w, r in rows.items()]
    ).sort_values(["word"])
    report.to_csv(OUT, index=False, encoding="utf-8")
    print(f"{len(report)} unknown words -> {OUT.relative_to(ROOT)}")
    print(report.to_string(index=False))


if __name__ == "__main__":
    main()
