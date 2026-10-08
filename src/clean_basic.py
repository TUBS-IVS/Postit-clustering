"""Stage 1 of the post-it cleaning: simple preprocessing, arrows left untouched.

Applies the parts of docs/text_cleaning_rules.md that do not involve arrows:
row fixes (A, minus the arrow ones), split words (B), spelling (C), and steps
4-7, 9, 10 and 12. Every arrow (-> => → ↓ ↘ ↳ ↪) stays where it was written, so
the arrow rules can be decided separately. The rule tables are imported from
clean_text.py, so the two scripts cannot drift apart.

Usage:
    python src/clean_basic.py
"""

import json
import re
import sys
from collections import Counter

import pandas as pd

from clean_text import (
    ALLOWED_PUNCT, APPLY_LIKELY_TYPOS, ARROW_RE, BULLET_RE, LIKELY_TYPOS, N_RECORDS, ROOT,
    SOURCE, SPELLCHECK_TYPOS, STRAY_HYPHEN_RE, TYPOS, apply_line_splits, apply_typos, row_fixes,
)

OUT_JSON = ROOT / "data" / "processed" / "postits_basic.json"
OUT_XLSX = ROOT / "data" / "processed" / "postits_basic.xlsx"

# A. Row fixes (and the review's fixes) whose find text contains no arrow. The arrow ones
# (rows 0, 34, 85, 160, 165, 167, 274, 279, 290, 326) are left for the arrow stage.
BASIC_ROW_FIXES = {
    row: [(find, repl) for find, repl in fixes if not ARROW_RE.search(find)]
    for row, fixes in row_fixes().items()
}

# The worked examples from the rules file, with the arrows still in place.
EXAMPLES = {
    "Houses are expensive\n↓\nExclusive!": "Houses are expensive ↓ Exclusive!",
    '- Individual motorized transport\n- No public transport connection\n- "[illegible]"':
        "Individual motorized transport; No public transport connection",
    "Extra Weather\n-30 Degrees\nand +40 Degrees": "Extra Weather minus 30 Degrees and plus 40 Degrees",
    "→ public\ncarsharing\n(cheep)": "→ public carsharing (cheap)",
}


def apply_row_fixes(texts, row_fixes):
    for row, fixes in row_fixes.items():
        for find, repl in fixes:
            n = texts[row].count(find)
            if n != 1:
                raise ValueError(f"A: row {row}: {find!r} found {n}x, expected 1x")
            texts[row] = texts[row].replace(find, repl)


def fix_words(raws):
    """Steps 1-3: row fixes (minus the arrow ones), split words, spelling.

    Line breaks and arrows stay as written, so llm_review.py also uses this for the
    text it shows the model.
    """
    texts = list(raws)
    apply_row_fixes(texts, BASIC_ROW_FIXES)
    apply_line_splits(texts)
    apply_typos(texts, TYPOS)
    if APPLY_LIKELY_TYPOS:
        apply_typos(texts, LIKELY_TYPOS)
    apply_typos(texts, SPELLCHECK_TYPOS)
    return texts


def normalize_basic(text):
    """Steps 4-7, 9, 10 and 12. Arrows pass through unchanged."""
    text = re.sub(r'["“”]', "", text)  # 4. quote marks, keeping the quoted word
    text = BULLET_RE.sub("; ", text)  # 5. line-start "- " becomes a separator; "->" and "-30" need no space so are safe
    text = text.replace("&", " and ")  # 6.
    text = re.sub(r"\s*/\s*", " or ", text)  # 7.
    text = re.sub(r"\s+", " ", text).strip()  # 9. line breaks and repeated whitespace
    text = re.sub(r"^(?:;\s*)+", "", text)  # 10. leading separator; leading arrows stay for the arrow stage
    # 12. tidy
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)  # no space before closing punctuation
    text = re.sub(r"\(\s+", "(", text)  # none after "("
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)  # collapse doubled separators
    return text


def validate(df):
    """Section D, with arrows masked out, plus a check that no arrow was touched."""
    errors = []
    for i, raw, t in zip(df.index, df["text_raw"], df["text_basic"]):
        if not t or t != t.strip() or "\n" in t or "  " in t:
            errors.append(f"row {i}: empty, unstripped, line break or double space: {t!r}")
        if ARROW_RE.findall(t) != ARROW_RE.findall(raw):
            errors.append(f"row {i}: arrows changed: {ARROW_RE.findall(raw)} -> {ARROW_RE.findall(t)}")
        masked = ARROW_RE.sub(" ", t)
        bad = sorted({c for c in masked if not (c.isalnum() or c == " " or c in ALLOWED_PUNCT)})
        if bad:
            errors.append(f"row {i}: disallowed characters {bad}: {t!r}")
        if STRAY_HYPHEN_RE.search(masked):
            errors.append(f"row {i}: '-' outside a word: {t!r}")
    for raw, expected in EXAMPLES.items():
        got = df.loc[df["text_raw"] == raw, "text_basic"].tolist()
        if got != [expected]:
            errors.append(f"example {raw!r}: expected {expected!r}, got {got}")
    if errors:
        raise ValueError("Validation failed:\n" + "\n".join(errors))


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # the summary prints arrow characters
    records = json.load(open(SOURCE, encoding="utf-8-sig"))
    if len(records) != N_RECORDS or len({r["bild"] for r in records}) != N_RECORDS:
        raise ValueError(f"expected {N_RECORDS} records with unique `bild`")

    raws = [r["text_englisch"] for r in records]
    basic = [normalize_basic(t) for t in fix_words(raws)]

    meta = ["bild", "adresse", "seite", "zeile", "spalte", "teilposition"]
    df = pd.DataFrame([{k: r[k] for k in meta} for r in records])
    df.index.name = "row"
    df["text_raw"] = raws
    df["text_basic"] = basic
    df["n_arrows"] = [len(ARROW_RE.findall(t)) for t in basic]
    validate(df)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index().to_json(OUT_JSON, orient="records", force_ascii=False, indent=2)
    df.to_excel(OUT_XLSX)
    forms = Counter(a for t in basic for a in ARROW_RE.findall(t))
    print(f"{len(df)} notes cleaned (stage 1), validation passed")
    print(f"  arrows left for the arrow stage: {sum(forms.values())} in {(df['n_arrows'] > 0).sum()} notes, "
          + ", ".join(f"{a} {n}" for a, n in forms.most_common()))
    print(f"  likely typos applied: {APPLY_LIKELY_TYPOS}")
    print(f"  -> {OUT_JSON.relative_to(ROOT)}, {OUT_XLSX.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
