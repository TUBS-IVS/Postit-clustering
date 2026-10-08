"""Clean post-it `text_englisch` for embedding and clustering.

Implements docs/text_cleaning_rules.md. Section letters (A-D) and step numbers
in the comments refer to that file. Every rule asserts that it matched what the
rules file says it should, so a rule that silently stops matching fails loudly.

Two decisions from the review on 2026-10-08 change the rules file (see README):
REVIEW_FIXES adds meaning fixes, and each arrow becomes the word for its meaning
(ARROW_LABELS, ARROW_WORDS) instead of always "leads to".

Usage:
    python src/clean_text.py
"""

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "raw" / "postits_geprueft (2).json"
OUT_JSON = ROOT / "data" / "processed" / "postits_clean.json"

N_RECORDS = 336
ARROW = "→"
ARROW_RE = re.compile(r"->|=>|[→↓↘↳↪]")
BULLET_RE = re.compile(r"(?m)^[ \t]*-[ \t]+")
STRAY_HYPHEN_RE = re.compile(r"(?<!\w)-|-(?!\w)")

# A. Row-specific fixes on the raw text: row -> [(find, replace), ...].
# Each find must occur exactly once in its row.
ROW_FIXES = {
    0: [("from producer ->", "from producer to"),  # arrow means direction
        ("travels -> so", "travels, so")],  # "so" already expresses the consequence
    22: [("traffic-\nvolume,\n without\nmanagement -\nnecessi -\nty",
          "traffic volume, without management necessity")],
    27: [('\n- "[illegible]"', "")],
    34: [("↳ since", "since")],  # arrow introduces an explanation
    85: [("↘ expensive", "(expensive)"),  # arrow marks an attribute
         ("Rest in [unreadable]", "Rest in high-rise building")],  # manual correction from the CSV
    165: [("Goals for future city\n->", "Goals for future city:")],
    167: [("(->outdated)", "(outdated)")],
    176: [("\n+ Eco", "\nand Eco")],
    198: [("(enery/pax)", "(energy per passenger)")],
    200: [("Eco- status", "Eco-status")],  # not in the rules file; caught by the Section D hyphen check
    202: [("Rich / Poor", "rich versus poor")],
    207: [("Tiny - House", "Tiny House")],
    268: [("-30", "minus 30"), ("+40", "plus 40")],
    274: [("(→ full", "(full")],
    279: [("↳ drop off\npoints\n↳ no Home", "drop off points; no home")],
    290: [("↓\n- policy", "→ policy")],
    292: [("(non/semi-urban)", "(non-urban or semi-urban)")],
    298: [("Was", "War")],  # translation error; the German `text` says "War"
    309: [("eco -friendly", "eco-friendly")],
    326: [("brands →", "brands")],  # arrow means "e.g."
}

# Not in the rules file: meaning fixes from the review (data/review/), accepted 2026-10-08.
# Same format and check as ROW_FIXES, applied after them.
REVIEW_FIXES = {
    # translations that lost meaning (data/review/translations_checked.xlsx)
    29: [("remain", "remain / become")],  # "bleiben / werden": "become" was dropped
    39: [("data about traffic", "traffic"),  # "über Daten" means via data
         ("record", "via data"),  # "erfassen" was translated twice
         ("LSA", "traffic lights")],  # Lichtsignalanlage
    47: [("nice", "toll")],  # written in English: a city toll, not German "toll" (great)
    48: [("cost-effective", "low-cost")],  # "kostengünstig" means inexpensive
    74: [("Anxiety room", "Space that feels unsafe")],  # "Angstraum"
    81: [("Clear", "Strong")],  # "Starke Trennung"
    84: [("lost", "low")],  # "günstigen": low-cost
    87: [("adhesive", "activists gluing themselves to roads")],  # "Klimakleber"
    92: [("Wheels", "Bikes")],  # "Räder" means bikes here
    332: [("made", "financed")],  # "eigenwirtschaftlich"
    # read on the scans by three readers (data/review/claude/scan_check.json)
    50: [("Use of", "Installation of")],  # the German says "Einbau von", not "Einsatz von"
    109: [("autonimization", "automation")],  # written "automization"
    154: [("HDM\n", "")],  # "HDM" is the board-cell label MD11, not part of the note
    157: [("expensive do", "expensive to")],  # the writer's slip
    162: [("anywhere", "everywhere"), ("(PT)", "(IT)")],
    255: [("to much", "too much")],  # the scan says "too"
    269: [("to safe", "to save")],  # German speaker's safe/save mix-up
    283: [("to hot", "too hot")],  # the writer's slip
    288: [("cushion", "emission")],  # "zero emission housing"
    305: [("oiltete", "oil etc")],  # written "oil + etc"
    335: [("entriefree", "free entry")],  # written "entreefree"
    # from the final check of all notes (data/review/claude/final_check.json), flagged by both checkers
    21: [("maintenance", "housing")],  # the scan says "Wohnung", not "Wartung"
    35: [("DRT", "DRT (demand-responsive transport)")],
    65: [("comes the norm", "becomes the norm")],  # "be-" was lost at the line break
    75: [("for\n↓\n", "for\n")],  # one phrase; the arrow only carries it to the next line
    85: [("Rest in high-rise building", "the rest in high-rise buildings")],  # German "der Rest", plural
    90: [("increasingly", "increased")],  # "vermehrte"
    93: [("(DB)", "(Deutsche Bahn railway)")],
    151: [("all critical", "and critical")],
    156: [("15 min in town", "15 min town")],
    158: [("work/house", "of work and house")],  # "/" joins, it is not "or"
    161: [("increase\npublic\nin Transit", "increase\nin public\nTransit")],
    209: [("pt-service", "public transport service")],
    234: [("cant", "can't")],
    235: [("Cant", "Can't")],
    241: [("move", "more")],
    243: [("management /\nfocus", "management;\nfocus")],
    287: [("Fresh", "Freak")],
    # the one arrow labelled "other": it names the idea before it
    160: [("\n→ ruralisation", " is ruralisation")],
}

# B. Words split across lines. Each must occur at least once.
LINE_SPLITS = {
    "traffic-\nvolume": "traffic volume",
    "nation-\nwide": "nationwide",
    "power-\nplants": "power plants",
    "just-in-\ntime": "just-in-time",
    "Eco-\nfriendly": "Eco-friendly",
}

# C. Spelling, as case-sensitive whole-word replacements: typo -> (fix, rows it occurs in).
TYPOS = {
    "resscource": ("resource", {0}),
    "affort": ("afford", {121}),
    "damand": ("demand", {127, 165}),
    "fueles": ("fuels", {137}),
    "trafic": ("traffic", {149}),
    "Oppostite": ("Opposite", {160}),
    "Sufficant": ("Sufficient", {188}),
    "livestyle": ("lifestyle", {219}),
    "enviroment": ("environment", {273}),
    "sill": ("still", {289}),
    "Climat": ("Climate", {321}),
    "pedilecs": ("pedelecs", {128}),
    "sparsly": ("sparsely", {157}),
    "barries": ("barriers", {253}),
    "cheep": ("cheap", {208}),
    "V.R": ("VR", {43}),
    "Statussymbol": ("status symbol", {24}),
    "commutation": ("commuting", {163}),
}

# Probable fixes the rules file says to confirm against the scan. Confirmed on the scans on
# 2026-10-08 (data/review/claude/scan_check.json): "desle" is "desk" (singular), 137 says
# "insulation", 162 says "trought". Set APPLY_LIKELY_TYPOS = False to leave them as written.
APPLY_LIKELY_TYPOS = True
LIKELY_TYPOS = {
    "desle": ("desk", {14}),
    "isolation": ("insulation", {137}),
    "though network": ("through network", {162}),
}

# Not in the rules file: words flagged by src/spellcheck.py that have one obvious fix.
# Ambiguous flags are left as written and listed in the README.
SPELLCHECK_TYPOS = {
    "ressources": ("resources", {15}),
    "irrelevent": ("irrelevant", {65}),
    "polictical": ("political", {112}),
    "impared": ("impaired", {197}),
    "staires": ("stairs", {217}),
    "bying": ("buying", {222}),
    "Streelights": ("Streetlights", {247}),
    "Insuficant": ("Insufficient", {270}),
    "Weater": ("Weather", {287}),
    "combustions": ("combustion", {289}),
    "independend": ("independent", {305}),
    "fossiles": ("fossils", {305}),
    "Concret": ("Concrete", {306}),
    "catroshopies": ("catastrophes", {318}),
    "autonomes": ("autonomous", {48}),  # German leftover
    "Autonomes": ("Autonomous", {63}),
    "Eifeltower": ("Eiffel Tower", {233}),
    # run-together compounds, out of vocabulary for word embeddings
    "homeoffice": ("home office", {56}),
    "Homeoffice": ("Home office", {193}),
    "Doctoroffices": ("doctor offices", {73}),
    "Datacenters": ("data centers", {126}),
    "Landuse": ("land use", {215}),
    "landuse": ("land use", {256}),
    "Selfcare": ("Self-care", {228}),
    "PT": ("public transport", {3, 101, 116, 165, 201, 273, 292}),  # read as part-time otherwise
}

# Not in the rules file (step 11): what each arrow becomes, by its meaning. Decided 2026-10-08,
# because only about half the arrows mean "leads to".
ARROW_WORDS = {"cause": " leads to ", "explanation": " because ", "example": " e.g. ", "list": "; ",
               "heading": ": ", "attribute": ", ", "direction": " to "}
NO_ARROW_WORDS = dict.fromkeys(ARROW_WORDS, " ")  # for text_tokens: only the writers' own words

# The meaning of each arrow left after the row fixes: row -> one label per arrow, in order.
# From the review (data/review/arrows_checked.xlsx), accepted 2026-10-08; row 47 was voted again
# after its translation fix ("nice" -> "toll"). Arrows at the start of a note are removed in
# step 10 whatever their label.
ARROW_LABELS = {
    3: ('attribute',), 4: ('explanation',), 8: ('cause',), 9: ('cause',), 11: ('cause', 'cause'),
    14: ('cause',), 18: ('example',), 21: ('cause', 'cause'), 30: ('explanation',), 34: ('example',),
    36: ('cause', 'example'), 39: ('cause',), 43: ('cause',), 47: ('heading',), 53: ('cause',),
    59: ('cause',), 60: ('cause',), 62: ('cause', 'cause'), 69: ('cause', 'cause'),
    77: ('explanation',), 80: ('cause',), 85: ('cause',), 91: ('cause', 'cause'), 94: ('list',),
    106: ('cause',), 113: ('list', 'list'), 118: ('cause',), 121: ('heading',), 126: ('cause',),
    127: ('cause',), 128: ('cause',), 129: ('list',), 137: ('cause',), 149: ('cause',), 158: ('cause',),
    161: ('cause',), 162: ('heading',), 163: ('list',), 165: ('list', 'list'), 168: ('cause', 'cause'),
    173: ('list',), 184: ('explanation',), 188: ('cause',), 190: ('example',), 202: ('heading',),
    204: ('attribute', 'attribute'), 205: ('list',), 208: ('list',), 209: ('cause',), 211: ('explanation',),
    213: ('cause',), 214: ('attribute', 'attribute'), 219: ('heading',), 223: ('example',), 225: ('heading',),
    227: ('heading',), 232: ('cause',), 251: ('cause',), 264: ('cause',), 273: ('explanation',),
    275: ('attribute', 'cause'), 277: ('list',), 288: ('list', 'list'), 289: ('cause',), 290: ('heading',),
    291: ('cause',), 298: ('cause',), 299: ('cause',), 303: ('list',), 304: ('attribute',),
    307: ('list', 'list'), 309: ('attribute',), 319: ('attribute',), 321: ('cause',), 322: ('cause',),
    332: ('cause',), 333: ('list',), 334: ('list',),
}

# Characters allowed in text_clean besides letters, digits and spaces (Section D).
ALLOWED_PUNCT = set(".,;:!?()'-")

# Worked examples from the rules file: raw -> expected text_clean.
EXAMPLES = {
    "Houses are expensive\n↓\nExclusive!": "Houses are expensive leads to Exclusive!",
    '- Individual motorized transport\n- No public transport connection\n- "[illegible]"':
        "Individual motorized transport; No public transport connection",
    "Extra Weather\n-30 Degrees\nand +40 Degrees": "Extra Weather minus 30 Degrees and plus 40 Degrees",
    "→ public\ncarsharing\n(cheep)": "public carsharing (cheap)",
}


def row_fixes():
    """ROW_FIXES then REVIEW_FIXES: row -> fixes in the order they are applied."""
    merged = {row: list(fixes) for row, fixes in ROW_FIXES.items()}
    for row, fixes in REVIEW_FIXES.items():
        merged.setdefault(row, []).extend(fixes)
    return merged


def apply_row_fixes(texts):
    for row, fixes in row_fixes().items():
        for find, repl in fixes:
            n = texts[row].count(find)
            if n != 1:
                raise ValueError(f"A: row {row}: {find!r} found {n}x, expected 1x")
            texts[row] = texts[row].replace(find, repl)


def apply_line_splits(texts):
    for find, repl in LINE_SPLITS.items():
        if not any(find in t for t in texts):
            raise ValueError(f"B: {find!r} not found")
        texts[:] = [t.replace(find, repl) for t in texts]
    leftover = [i for i, t in enumerate(texts) if re.search(r"\w-\n\w", t)]
    if leftover:
        raise ValueError(f"B: words still split across lines in rows {leftover}")


def apply_typos(texts, typos):
    for typo, (fix, rows) in typos.items():
        pattern = re.compile(rf"\b{re.escape(typo)}\b")
        found = {i for i, t in enumerate(texts) if pattern.search(t)}
        if found != rows:
            raise ValueError(f"C: {typo!r} found in rows {sorted(found)}, expected {sorted(rows)}")
        texts[:] = [pattern.sub(fix, t) for t in texts]


def arrow_word(m, words):
    """The word for the arrow marker in match `m`, unless the writer already wrote it after the
    arrow, as in "-> e.g., no insurance" (row 36)."""
    word = words[m.group(1)]
    written = word.strip() and re.match(re.escape(word.strip()) + r"(?!\w)", m.string[m.end():], re.IGNORECASE)
    return " " if written else word


def normalize(text, labels, words=ARROW_WORDS):
    """Steps 4-12: symbol handling, whitespace and tidying.

    `labels` holds the meaning of each arrow in `text`, in order; each arrow becomes
    the word for its label in `words`.
    """
    text = re.sub(r'["“”]', "", text)  # 4. quote marks
    text = BULLET_RE.sub("; ", text)  # 5. bullets
    text = text.replace("&", " and ")  # 6.
    text = re.sub(r"\s*/\s*", " or ", text)  # 7.
    marks = iter(labels)  # 8. each arrow becomes a marker for its label, e.g. "⟨cause⟩"
    text = ARROW_RE.sub(lambda m: f" ⟨{next(marks)}⟩ ", text)
    text = re.sub(r"\s+", " ", text).strip()  # 9. line breaks and whitespace
    text = re.sub(r"^(?:(?:⟨\w+⟩|;)\s*)+", "", text)  # 10. leading markers: nothing comes before them
    text = re.sub(r"\s*⟨(\w+)⟩\s*", lambda m: arrow_word(m, words), text)  # 11.
    # 12. tidy
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
    return text


def tokenize(text):
    """text_tokens: lowercase, punctuation removed except intra-word hyphens.

    Apostrophes and dots inside words are dropped rather than split on, so
    "can't" -> "cant" (the negation stays one token) and "e.g." -> "eg".
    No stopwords are removed here.
    """
    text = text.lower()
    text = re.sub(r"(?<=\w)['.](?=\w)", "", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    text = STRAY_HYPHEN_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def validate(df):
    """Section D, plus checks on the counts the rules file states."""
    errors = []
    for i, t in df["text_clean"].items():
        bad = sorted({c for c in t if not (c.isalnum() or c == " " or c in ALLOWED_PUNCT)})
        if bad:
            errors.append(f"row {i}: disallowed characters {bad}: {t!r}")
        if not t or t != t.strip() or "\n" in t or "  " in t:
            errors.append(f"row {i}: empty, unstripped, line break or double space: {t!r}")
        if STRAY_HYPHEN_RE.search(t):
            errors.append(f"row {i}: '-' outside a word: {t!r}")
    for i, t in df["text_tokens"].items():
        if not t or not re.fullmatch(r"[^\W_]+(?:-[^\W_]+)*(?: [^\W_]+(?:-[^\W_]+)*)*", t):
            errors.append(f"row {i}: malformed text_tokens: {t!r}")

    bullet_rows = int((df["n_bullets"] > 0).sum())
    if (df["n_bullets"].sum(), bullet_rows) != (9, 7):
        errors.append(f"expected 9 bullet lines in 7 rows, got {df['n_bullets'].sum()} in {bullet_rows}")
    n_amp = int(df["text_raw"].str.count("&").sum())
    if n_amp != 5:
        errors.append(f"expected 5 '&', got {n_amp}")
    if df["starts_with_arrow"].sum() != 15:
        errors.append(f"expected 15 notes starting with an arrow, got {df['starts_with_arrow'].sum()}")
    for raw, expected in EXAMPLES.items():
        got = df.loc[df["text_raw"] == raw, "text_clean"].tolist()
        if got != [expected]:
            errors.append(f"example {raw!r}: expected {expected!r}, got {got}")
    if errors:
        raise ValueError("Validation failed:\n" + "\n".join(errors))


def main():
    records = json.load(open(SOURCE, encoding="utf-8-sig"))
    if len(records) != N_RECORDS or len({r["bild"] for r in records}) != N_RECORDS:
        raise ValueError(f"expected {N_RECORDS} records with unique `bild`")

    raws = [r["text_englisch"] for r in records]
    texts = list(raws)
    apply_row_fixes(texts)
    apply_line_splits(texts)
    apply_typos(texts, TYPOS)
    if APPLY_LIKELY_TYPOS:
        apply_typos(texts, LIKELY_TYPOS)
    apply_typos(texts, SPELLCHECK_TYPOS)
    labels = [ARROW_LABELS.get(i, ()) for i in range(len(texts))]
    for i, (t, labs) in enumerate(zip(texts, labels)):
        if len(ARROW_RE.findall(t)) != len(labs) or not set(labs) <= set(ARROW_WORDS):
            raise ValueError(f"row {i}: arrows {ARROW_RE.findall(t)} do not match ARROW_LABELS {labs}")
        if re.search(rf"(?:{ARROW_RE.pattern})\s*$", t):
            raise ValueError(f"row {i}: arrow at the end of the note, pointing at nothing: {t!r}")
    clean = [normalize(t, labs) for t, labs in zip(texts, labels)]
    plain = [normalize(t, labs, NO_ARROW_WORDS) for t, labs in zip(texts, labels)]

    meta = ["bild", "adresse", "seite", "zeile", "spalte", "teilposition"]
    df = pd.DataFrame([{k: r[k] for k in meta} for r in records])
    df.index.name = "row"
    df["text_raw"] = raws
    df["text_clean"] = clean
    df["text_tokens"] = [tokenize(t) for t in plain]  # without arrow words: "leads" would dominate
    df["n_arrows"] = [len(ARROW_RE.findall(t)) for t in raws]
    df["starts_with_arrow"] = [bool(ARROW_RE.match(t.lstrip())) for t in raws]
    df["n_bullets"] = [len(BULLET_RE.findall(t)) for t in raws]
    validate(df)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index().to_json(OUT_JSON, orient="records", force_ascii=False, indent=2)
    print(f"{len(df)} notes cleaned, validation passed")
    print(f"  arrows: {df['n_arrows'].sum()} in {(df['n_arrows'] > 0).sum()} notes, "
          f"{df['starts_with_arrow'].sum()} notes start with one")
    print(f"  likely typos applied: {APPLY_LIKELY_TYPOS}")
    print(f"  -> {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
