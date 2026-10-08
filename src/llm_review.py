"""Ask an LLM to review notes where meaning is easy to lose. Suggestions only.

Two tasks, each writing a review sheet to data/review/ for a person to accept or reject:
  translations  the 82 notes whose English differs from the original `text`: minimal edits
                where `text_englisch` loses or distorts the meaning of the original
  arrows        the 85 notes with arrows: what each arrow means (cause, direction, ...)

The model runs on TU Braunschweig's KI-Toolbox (src/ki_toolbox.py). Nothing here changes
the cleaned data. Accepted suggestions go into the rule tables in clean_text.py, so the
cleaning pipeline itself stays deterministic and offline. The API has no temperature, so
answers vary between runs: every valid reply is saved under data/review/raw/ and reused on
the next run, so a rerun only calls the API for notes not yet reviewed. Delete
data/review/raw/<task>/ to start over.

Setup: OPENAI_API_KEY (the KI-Toolbox token), LLM_MODEL and LLM_API_URL in .env, then
    python src/llm_review.py translations --dry-run   # print the first request, no API call
    python src/llm_review.py translations --limit 5   # try a few notes first
    python src/llm_review.py translations --rows 74 87
    python src/llm_review.py arrows
"""

import argparse
import json
import os
import sys

import jsonschema
import pandas as pd
import requests
from dotenv import load_dotenv

from clean_basic import fix_words
from clean_text import ARROW_RE, ROOT, ROW_FIXES, SOURCE
from ki_toolbox import call_ki_toolbox

REVIEW_DIR = ROOT / "data" / "review"
RAW_DIR = REVIEW_DIR / "raw"
MAX_TRIES = 3  # per note, when a reply is not valid JSON in the required form

CONTEXT = ("The notes are handwritten post-its from a workshop about the future of cities "
           "(mobility, delivery, housing, energy, work). They are short, fragmentary and informal.")

TRANSLATION_REPLY = ('{"edits": [{"find": "<exact text from the English>", "replace": "<corrected text>", '
                     '"reason": "<one sentence>"}]}')
TRANSLATION_PROMPT = f"""You check English translations of workshop notes. {CONTEXT}

Propose the smallest edits that restore meaning the English loses or distorts: mistranslated idioms and compound nouns, false friends, wrong words. Where a literal translation would mislead, translate the meaning.

Do not fix style, grammar, capitalization or spelling, do not turn fragments into sentences, do not add information, and do not touch arrows (->, =>, →, ↓, ↘, ↳, ↪), dashes or line breaks.

Each `find` must be copied exactly from the English text, including line breaks (written as \\n in JSON), and occur there only once. Keep it as short as possible.

Reply with only a JSON object in this form, no other text:
{TRANSLATION_REPLY}
If the English keeps the meaning, reply {{"edits": []}}."""

ARROW_LABELS = ["cause", "explanation", "direction", "example", "list", "attribute", "heading", "other"]
ARROW_REPLY = '{"arrows": [{"number": <arrow number>, "label": "<one label>", "reason": "<one sentence>"}]}'
ARROW_PROMPT = f"""You label the arrows in workshop notes. {CONTEXT}

People use arrows (->, =>, →, ↓, ↘, ↳, ↪) for different things. For each numbered arrow, choose one label:
- cause: what comes before leads to, causes or results in what comes after
- explanation: what comes after explains or gives the reason for what comes before
- direction: movement or flow from one place or actor to another
- example: introduces examples of what comes before
- list: marks a list item or sub-point rather than a consequence
- attribute: marks a property of the thing before it
- heading: introduces what follows, as a colon does
- other: none of these; say what it means in the reason

An arrow at the very start of a note has nothing before it; label it by what it most likely means.

Reply with only a JSON object in this form, no other text, with one entry per numbered arrow:
{ARROW_REPLY}"""


def obj(**props):
    """JSON schema object: every field required, no others."""
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


TRANSLATION_SCHEMA = obj(edits={"type": "array", "items": obj(
    find={"type": "string"}, replace={"type": "string"}, reason={"type": "string"})})
ARROW_SCHEMA = obj(arrows={"type": "array", "items": obj(
    number={"type": "integer"}, label={"type": "string", "enum": ARROW_LABELS}, reason={"type": "string"})})

# Rows whose arrows Section A already fixes by hand; the labels there are a check on the model.
ARROW_FIX_ROWS = {row for row, fixes in ROW_FIXES.items() if any(ARROW_RE.search(f) for f, _ in fixes)}


def is_translated(r):
    return r["text"].strip() != r["text_englisch"].strip()


def translation_request(r):
    return f"Original:\n{r['text']}\n\nEnglish:\n{r['text_englisch']}"


def translation_rows(i, r, result):
    en = r["text_englisch"]
    return [{"row": i, "bild": r["bild"], "text": r["text"], "text_englisch": en,
             "find": e["find"], "replace": e["replace"], "reason": e["reason"],
             "find_ok": en.count(e["find"]) == 1, "accept": ""}
            for e in result["edits"]]


# The arrow task reads `text_fixed`: the English after the word fixes (see main), so the model
# sees "high-rise building" rather than "[unreadable]". Those fixes leave every arrow in place.
def has_arrow(r):
    return bool(ARROW_RE.search(r["text_fixed"]))


def number_arrows(text):
    numbers = iter(range(1, 100))
    return ARROW_RE.sub(lambda m: f"[{next(numbers)} {m.group()}]", text)


def arrow_request(r):
    return f"Note:\n{r['text_fixed']}\n\nThe same note with each arrow numbered:\n{number_arrows(r['text_fixed'])}"


def check_arrows(r, result):
    n = len(ARROW_RE.findall(r["text_fixed"]))
    numbers = sorted(a["number"] for a in result["arrows"])
    if numbers != list(range(1, n + 1)):
        raise ValueError(f"labels for arrows {numbers}, expected 1-{n}")


def arrow_rows(i, r, result):
    text = r["text_fixed"]
    labels = sorted(result["arrows"], key=lambda a: a["number"])
    return [{"row": i, "bild": r["bild"], "text_fixed": text, "number": a["number"], "arrow": m.group(),
             "at_note_start": not text[:m.start()].strip(), "label": a["label"], "reason": a["reason"],
             "section_a_row": i in ARROW_FIX_ROWS, "accept": ""}
            for m, a in zip(ARROW_RE.finditer(text), labels)]


# A `find` that is not in the English is not retried: the suggestion is still worth reading,
# and the sheet flags it in `find_ok`.
TASKS = {
    "translations": dict(select=is_translated, request=translation_request, rows=translation_rows,
                         check=lambda r, result: None, system=TRANSLATION_PROMPT, schema=TRANSLATION_SCHEMA),
    "arrows": dict(select=has_arrow, request=arrow_request, rows=arrow_rows,
                   check=check_arrows, system=ARROW_PROMPT, schema=ARROW_SCHEMA),
}


def parse_reply(text, schema):
    """The JSON object in a reply, checked against the task's schema."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the reply")
    result = json.loads(text[start:end + 1])
    jsonschema.validate(result, schema)
    return result


def review_note(record, task, model, token, url):
    """Ask until the reply is valid, up to MAX_TRIES times. Returns what goes into the cache."""
    user = task["request"](record)
    for tries in range(1, MAX_TRIES + 1):
        try:
            reply, done = call_ki_toolbox(user, task["system"], model, token, url)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                sys.exit(f"The KI-Toolbox rejected the token (HTTP {e.response.status_code}); "
                         "check OPENAI_API_KEY in .env.")
            raise
        try:
            result = parse_reply(reply, task["schema"])
            task["check"](record, result)
        except (ValueError, jsonschema.ValidationError) as e:
            error = str(e).splitlines()[0]
            continue
        return {"model": model, "system": task["system"], "user": user, "reply": reply,
                "result": result, "done": done, "tries": tries}
    raise ValueError(f"no valid reply after {MAX_TRIES} tries ({error})")


def write_xlsx(df, path):
    """Write a review sheet. Not CSV: Excel reads cells starting with "-" or "=" as formulas."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
        for row in writer.sheets["Sheet1"].iter_rows():
            for cell in row:
                if cell.data_type == "f":  # openpyxl also treats strings starting with "=" as formulas
                    cell.data_type = "s"


def main():
    parser = argparse.ArgumentParser(description="LLM review of tricky notes (suggestions only)")
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("--limit", type=int, help="review only the first N notes")
    parser.add_argument("--rows", type=int, nargs="+", help="review only these rows")
    parser.add_argument("--dry-run", action="store_true", help="print the first request; no API call")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")

    task = TASKS[args.task]
    records = json.load(open(SOURCE, encoding="utf-8-sig"))
    for r, fixed in zip(records, fix_words([r["text_englisch"] for r in records])):
        r["text_fixed"] = fixed
    rows = [i for i, r in enumerate(records) if task["select"](r) and (not args.rows or i in args.rows)]
    rows = rows[:args.limit]
    if args.dry_run:
        print(f"{len(rows)} notes to review. First request:\n\n--- system ---\n{task['system']}\n\n"
              f"--- user ---\n{task['request'](records[rows[0]])}")
        return

    token, model, url = os.getenv("OPENAI_API_KEY"), os.getenv("LLM_MODEL"), os.getenv("LLM_API_URL")
    if not (token and model and url):
        sys.exit("Set OPENAI_API_KEY (the KI-Toolbox token), LLM_MODEL and LLM_API_URL in .env")

    out, failed, tokens = [], [], 0
    for n, i in enumerate(rows, 1):
        cache = RAW_DIR / args.task / f"{i:03}.json"
        if not cache.exists():
            try:
                entry = review_note(records[i], task, model, token, url)
            except ValueError as e:
                print(f"  {n}/{len(rows)}  row {i}: {e}")
                failed.append(i)
                continue
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"row": i, **entry}, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  {n}/{len(rows)}  row {i}" + (f" ({entry['tries']} tries)" if entry["tries"] > 1 else ""))
        entry = json.loads(cache.read_text(encoding="utf-8"))
        tokens += entry["done"].get("totalTokens", 0)
        out += task["rows"](i, records[i], entry["result"])

    path = REVIEW_DIR / f"{args.task}_review.xlsx"
    write_xlsx(pd.DataFrame(out), path)
    print(f"{len(rows) - len(failed)} notes reviewed, {len(out)} suggestions -> {path.relative_to(ROOT)}"
          f" ({tokens} tokens)")
    if failed:
        print(f"no valid reply for rows {failed}; rerun to retry them")


if __name__ == "__main__":
    main()
