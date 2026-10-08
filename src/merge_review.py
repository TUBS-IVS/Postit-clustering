"""Compare the KI-Toolbox model's review with an independent blind review and build the
sheets for the final manual decision. Makes no model calls.

Inputs, all in data/review/:
  raw/<task>/*.json               the KI-Toolbox model's replies (src/llm_review.py)
  claude/<task>_blind.json        the same task done blind by Claude agents, with the same
                                  instructions and inputs
  claude/<task>_adjudicated.json  optional: where the two differ, a third review that saw both
                                  proposals without knowing which model wrote which
Outputs:
  <task>_checked.xlsx             the sheet to decide on: fill in `accept`
  claude/<task>_to_adjudicate.json  the disagreements, input for the third review

Usage:
    python src/merge_review.py
"""

import json
import sys
from collections import Counter

import pandas as pd

from clean_basic import fix_words
from clean_text import ARROW_RE, ROOT, SOURCE
from llm_review import ARROW_FIX_ROWS, RAW_DIR, REVIEW_DIR, has_arrow, is_translated, number_arrows, write_xlsx

CLAUDE_DIR = REVIEW_DIR / "claude"


def load_gpt(task):
    return {int(p.stem): json.loads(p.read_text(encoding="utf-8"))["result"] for p in (RAW_DIR / task).glob("*.json")}


def load_claude(name):
    path = CLAUDE_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def overlaps(text, a, b):
    """Whether two edits change the same part of the text."""
    ka, kb = text.find(a["find"]), text.find(b["find"])
    if ka >= 0 and kb >= 0:
        return ka < kb + len(b["find"]) and kb < ka + len(a["find"])
    fa, fb = a["find"].lower(), b["find"].lower()
    return fa in fb or fb in fa


def edit_status(text, gpt, claude):
    if not gpt and not claude:
        return "both: no change"
    if not claude:
        return "gpt only"
    if not gpt:
        return "claude only"
    same = (all(any(overlaps(text, g, c) for c in claude) for g in gpt)
            and all(any(overlaps(text, c, g) for g in gpt) for c in claude))
    return "both: same spot" if same else "both: different spots"


def fmt(edits):
    return "\n".join(f"{e['find']!r} -> {e['replace']!r}: {e['reason']}" for e in edits)


def translations(records):
    gpt = load_gpt("translations")
    claude = {r["row"]: r["edits"] for r in load_claude("translations_blind.json")}
    adjudicated = {a["row"]: a for a in load_claude("translations_adjudicated.json")}
    lines, todo, status_counts = [], [], Counter()
    for i, r in enumerate(records):
        if not is_translated(r):
            continue
        if i not in gpt or i not in claude:
            status_counts["missing"] += 1
            continue
        en, g, c = r["text_englisch"], gpt[i]["edits"], claude[i]
        status = edit_status(en, g, c)
        status_counts[status] += 1
        if status == "both: no change":
            continue
        a_is = "gpt" if i % 2 == 0 else "claude"  # alternate who is shown first, against position bias
        a, b = (g, c) if a_is == "gpt" else (c, g)
        todo.append({"row": i, "original": r["text"], "english": en, "proposal_a": a, "proposal_b": b, "a_is": a_is})
        adj = adjudicated.get(i, {})
        lines.append({"row": i, "bild": r["bild"], "text": r["text"], "text_englisch": en, "status": status,
                      "gpt_edits": fmt(g), "claude_edits": fmt(c),
                      "recommended_edits": fmt(adj.get("edits", [])),
                      "recommended_find_ok": all(en.count(e["find"]) == 1 for e in adj.get("edits", [])),
                      # text the existing rules (clean_text.py) already change, e.g. "Was" -> "War" in row 298
                      "already_changed_by_rules": "; ".join(e["find"] for e in adj.get("edits", [])
                                                            if e["find"] not in r["text_fixed"]),
                      "recommendation_reason": adj.get("reason", ""),
                      "check_problems": "\n".join(f"{c['lens']}: {c['note']}" for c in adj.get("checks", [])
                                                  if not c["ok"]),
                      "accept": ""})
    return lines, todo, status_counts


def arrows(records):
    gpt = load_gpt("arrows")
    claude = {r["row"]: {a["number"]: a for a in r["arrows"]} for r in load_claude("arrows_blind.json")}
    adjudicated = {(a["row"], a["number"]): a for a in load_claude("arrows_adjudicated.json")}
    lines, todo, status_counts = [], [], Counter()
    for i, r in enumerate(records):
        if not has_arrow(r):
            continue
        text = r["text_fixed"]
        g = {a["number"]: a for a in gpt.get(i, {}).get("arrows", [])}
        c = claude.get(i, {})
        for n, m in enumerate(ARROW_RE.finditer(text), 1):
            if n not in g or n not in c:
                status_counts["missing"] += 1
                continue
            agree = g[n]["label"] == c[n]["label"]
            status_counts["agree" if agree else "disagree"] += 1
            adj = adjudicated.get((i, n), {})
            if not agree:
                a_is = "gpt" if (i + n) % 2 == 0 else "claude"
                a, b = (g[n], c[n]) if a_is == "gpt" else (c[n], g[n])
                todo.append({"row": i, "number": n, "arrow": m.group(), "note": text, "numbered": number_arrows(text),
                             "label_a": a["label"], "reason_a": a["reason"],
                             "label_b": b["label"], "reason_b": b["reason"], "a_is": a_is})
            lines.append({"row": i, "bild": r["bild"], "text_fixed": text, "number": n, "arrow": m.group(),
                          "at_note_start": not text[:m.start()].strip(), "section_a_row": i in ARROW_FIX_ROWS,
                          "gpt_label": g[n]["label"], "gpt_reason": g[n]["reason"],
                          "claude_label": c[n]["label"], "claude_reason": c[n]["reason"],
                          "status": "agree" if agree else "disagree",
                          "final_label": g[n]["label"] if agree else adj.get("label", ""),
                          "final_reason": "" if agree else adj.get("reason", ""),
                          "votes": ", ".join(adj.get("votes", [])), "accept": ""})
    return lines, todo, status_counts


def keep_accept(df, path, keys):
    """Carry the `accept` decisions over from the existing sheet, so a rebuild keeps them."""
    if not path.exists() or df.empty:
        return df
    old = pd.read_excel(path)
    if "accept" in old:
        decided = {tuple(k): v for k, v in zip(old[keys].values.tolist(), old["accept"]) if pd.notna(v)}
        df["accept"] = [decided.get(tuple(k), "") for k in df[keys].values.tolist()]
    return df


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    records = json.load(open(SOURCE, encoding="utf-8-sig"))
    for r, fixed in zip(records, fix_words([r["text_englisch"] for r in records])):
        r["text_fixed"] = fixed
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    for task, build, keys in (("translations", translations, ["row"]), ("arrows", arrows, ["row", "number"])):
        lines, todo, status_counts = build(records)
        (CLAUDE_DIR / f"{task}_to_adjudicate.json").write_text(
            json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
        path = REVIEW_DIR / f"{task}_checked.xlsx"
        write_xlsx(keep_accept(pd.DataFrame(lines), path, keys), path)
        print(f"{task}: {dict(status_counts)}; {len(todo)} to adjudicate -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
