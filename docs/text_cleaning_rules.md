# Post-it text cleaning: knowledge file

**Goal:** clean `text_englisch` so each note can be embedded (sentence and word embeddings), projected to 3D and clustered.

**Source:** `postits_geprueft__2_.json` (336 records). Load with `json.load(open(path, encoding="utf-8-sig"))`.

- Do **not** use the CSV export. It went through Excel, and 4 notes starting with `-` were read as formulas and became `#NOM?` (rows 24, 27, 210, 308).
- One exception: the text for row 85 comes from the CSV, because it contains a manual correction that the JSON lacks (see Section A).

**Row key:** `bild` is unique. "Row" below means the 0-based position in the JSON list. Scan IDs are short forms of `Worshop scan/<id>.jpg`.

---

## Output columns

| Column | Content | Use |
|---|---|---|
| `text_raw` | Untouched `text_englisch` | Reference |
| `text_clean` | All rules below applied; keeps case and punctuation | Sentence embeddings (SBERT etc.) |
| `text_tokens` | `text_clean` lowercased, punctuation removed except intra-word `-` | Word embeddings |
| `n_arrows`, `starts_with_arrow`, `n_bullets` | Counted on `text_raw` | Optional extra features for clustering |

---

## Processing order (order matters)

1. **Row-specific fixes** from Section A, applied to the raw text (they rely on the `\n` positions).
2. **Join words split across lines** using Section B.
3. **Spelling fixes** from Section C, as whole-word replacements.
4. **Remove quote marks** `"`, `“` and `”`, keeping the quoted word.
5. **Bullets:** replace a line-start `-` with the separator `; `.
   - Pattern: `(?m)^[ \t]*-[ \t]+`
   - It requires whitespace after `-`, so `->` and `-30` are not touched.
   - There are 9 bullet lines in 7 rows.
6. **`&`:** replace with ` and ` (5×).
7. **`/`:** replace with ` or ` using the pattern `\s*/\s*`. Do this after the Section A exceptions.
8. **Arrows:** map all 7 forms to one canonical `→`.
   - Forms: `->`, `=>`, `→`, `↓`, `↘`, `↳`, `↪`
   - Pattern: `->` or `=>` or the character class `[→↓↘↳↪]`
9. **Line breaks:** replace `\n` with a space, collapse repeated whitespace, then strip.
10. **Leading markers:** remove any `→` or `; ` at the very start of a note. 15 notes start with an arrow, which has no antecedent.
11. **Remaining arrows:** replace `→` with ` leads to `.
12. **Tidy:**
    - No space before `, . ; : ! ? )` and none after `(`.
    - Collapse doubled separators.
13. **Validate** using Section D.

### Why arrows become "leads to"

In this data, most arrows mean cause → consequence ("War → political instability"). The arrows that clearly mean something else are fixed per row in Section A.

A possible later alternative is to split notes at the arrows into cause → effect pairs, giving knowledge units or graph edges.

---

## A. Row-specific fixes

| Row | Scan | Find | Replace | Reason |
|---|---|---|---|---|
| 0 | 095056 | `from producer ->` | `from producer to` | Arrow means direction, not cause |
| 0 | 095056 | `travels -> so` | `travels, so` | "so" already expresses the consequence |
| 22 | 095344 | `traffic-\nvolume,\n without\nmanagement -\nnecessi -\nty` | `traffic volume, without management necessity` | Split word and stray dashes |
| 27 | 095418 | `\n- "[illegible]"` | *(delete)* | Placeholder line |
| 34 | 095512 | `↳ since` | `since` | Arrow introduces an explanation, not a consequence |
| 85 | 100129 | `↘ expensive` | `(expensive)` | Arrow marks an attribute |
| 85 | 100129 | `Rest in [unreadable]` | `Rest in high-rise building` | Manual correction taken from the CSV |
| 165 | 101005 | `Goals for future city\n->` | `Goals for future city:` | Arrow works as a heading marker |
| 167 | 101015 | `(->outdated)` | `(outdated)` | |
| 176 | 101108 | `\n+ Eco` | `\nand Eco` | `+` means "and" |
| 198 | 101332 | `(enery/pax)` | `(energy per passenger)` | `/` means "per"; also a typo |
| 202 | 101354 | `Rich / Poor` | `rich versus poor` | |
| 207 | 101424 | `Tiny - House` | `Tiny House` | |
| 268 | 102020 | `-30` and `+40` | `minus 30` and `plus 40` | Temperature signs; protects them from symbol stripping |
| 274 | 102057 | `(→ full` | `(full` | |
| 279 | 102125 | `↳ drop off\npoints\n↳ no Home` | `drop off points; no home` | Arrows used as list markers |
| 290 | 102228 | `↓\n- policy` | `→ policy` | Bullet directly after an arrow |
| 292 | 102241 | `(non/semi-urban)` | `(non-urban or semi-urban)` | |
| 298 | 102313 | `Was` | `War` | Translation error; the German `text` says "War" |
| 309 | 102412 | `eco -friendly` | `eco-friendly` | |
| 326 | 102545 | `brands →` | `brands` | Arrow means "e.g." |

## B. Words split across lines

| Find | Replace |
|---|---|
| `traffic-\nvolume` | `traffic volume` |
| `nation-\nwide` | `nationwide` |
| `power-\nplants` (2×) | `power plants` |
| `just-in-\ntime` | `just-in-time` |
| `Eco-\nfriendly` | `Eco-friendly` |

After this step, assert that the pattern `\w-\n\w` matches nothing.

## C. Spelling

These were spotted during review and the list is not exhaustive, so run a spell checker afterwards. Typos hurt word embeddings most, because misspelled words become out-of-vocabulary tokens.

**Clear typos**

| Typo | Fix | Row(s) |
|---|---|---|
| resscource | resource | 0 |
| affort | afford | 121 |
| damand | demand | 127, 165 |
| fueles | fuels | 137 |
| trafic | traffic | 149 |
| Oppostite | Opposite | 160 |
| Sufficant | Sufficient | 188 |
| livestyle | lifestyle | 219 |
| enviroment | environment | 273 |
| sill | still | 289 |
| Climat | Climate | 321 |
| pedilecs | pedelecs | 128 |
| sparsly | sparsely | 157 |
| barries | barriers | 253 |
| cheep | cheap | 208 |
| V.R | VR | 43 |
| Statussymbol | status symbol | 24 |
| commutation | commuting | 163 |

**Likely, so confirm against the scan**

| Text | Probable fix | Row |
|---|---|---|
| desle | desks | 14 |
| isolation | insulation | 137 |
| though network | through network | 162 |

**Unclear, so check the scan**

| Text | Row |
|---|---|
| Care Spaces | 307 |
| zero cushion housing | 288 |

**Keep as written:** "Claude ban" (row 77) is what the participant wrote (German original: "Claude-verbot").

## D. Validation

- `text_clean` contains only letters, digits, spaces and these characters: `. , ; : ! ? ( ) ' -`
- No `\n`, no double spaces and no empty strings; all 336 rows are non-empty.
- `-` appears only inside words, matching the pattern `\w-\w`.
- For `text_tokens`, do **not** remove negations or quantifiers as stopwords: keep *no, not, less, more, without, only*. They carry the meaning in this data ("no cars", "less automation").

## Examples

| Raw | `text_clean` |
|---|---|
| `Houses are expensive\n↓\nExclusive!` | `Houses are expensive leads to Exclusive!` |
| `- Individual motorized transport\n- No public transport connection\n- "[illegible]"` | `Individual motorized transport; No public transport connection` |
| `Extra Weather\n-30 Degrees\nand +40 Degrees` | `Extra Weather minus 30 Degrees and plus 40 Degrees` |
| `→ public\ncarsharing\n(cheep)` | `public carsharing (cheap)` |
