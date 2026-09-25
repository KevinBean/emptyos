---
name: eos-mine-archive
description: Run one round of the D-drive knowledge-mining loop — pull the highest-value unindexed documents from the fileindex queue, extract their text, distil each into a KB note that keeps the numbers and flags what the source got wrong, then record the judgment (with its mandatory note) across every mirror copy and export the ledger to the vault. Use when the user says "继续开采", "mine the archive", "next batch", "continue indexing", "开采下一批", or asks to keep working through the file-indexing project. NOT for the initial scan or exclusion-rule tuning (that is `python scripts/fileindex_scan.py scan` + editing fileindex.toml by hand), NOT for reading one specific document the user names (just extract and answer), and NOT for generic vault note creation (use vault-note-factory / vault-source-digest).
---

# Mine the Archive

One round of the coverage loop behind the D-drive knowledge-index project
(`{vault}/10_Projects/file-indexing/file-indexing.md`). The ledger says what
has been mined; this skill mines the next batch and writes back what it learned.

**This skill is a coverage ledger consumer, not an extractor.** The value is in
the judgment — what is worth a KB note, what the source got wrong, what the
mirror copies mean. Do not try to automate that half.

## Preconditions

- `data/fileindex/index.db` exists (if not: `python scripts/fileindex_scan.py scan`)
- The user has a live daemon, or you work purely from the CLI (both write the
  same ledger — `tests/personal/test_fileindex_ledger.py` pins them together)

## The loop

### 1. Pick the batch

```bash
python scripts/fileindex_scan.py queue -n 25 --corpus archieve
```

Filter with `--ext pdf,doc` when the round has a theme. Read the paths, not
just the scores: **pick documents that form a coherent topic**, so the round
produces one strong KB note rather than five thin ones.

**Themed round** ("mine everything about X" — e.g. the 2026-07-17 AI-chatbot
round): the queue is score-ordered and has no keyword filter, so sweep the
index DB directly, then treat each hit-cluster's *directory* as the unit:

```python
import sqlite3, re
db = sqlite3.connect("data/fileindex/index.db")
rows = db.execute("SELECT path, corpus, size, status FROM files").fetchall()
pat = re.compile(r"chat[\s_-]?bot|聊天机器人|(?<![a-z])llm(?![a-z])", re.I)
hits = [r for r in rows if pat.search(r[0].rsplit("/", 1)[-1])]
```

Two traps: short keywords need word boundaries (`gpt`/`rag`/`agent`/`bot`
substring-match garbage — "storage", "robot", university "agent" forms), and
a filename hit is a **doorway, not the batch** — list the whole containing
directory from the DB next (`WHERE path LIKE '%<dir>%'`); the siblings without
the keyword (analysis workbooks, output CSVs, flowcharts) are usually where
the value is.

Before extracting, check the duplicate family — most reports exist 3–13× across
mirror trees (`archieve` / `OneDrive/immigration` / `OneDrive/Career`):

```bash
python scripts/fileindex_scan.py dups --corpus archieve -n 15
```

### 2. Extract

```bash
python scripts/doc_extract.py "<path>" --out "$SCRATCH/name.md"
python scripts/doc_extract.py "<path>" --grep "制造商|载流量" --context 1   # targeted
```

Extensions lie in this corpus — `.doc` is variously RTF, Word97-OLE, or a
disguised docx; the extractor sniffs magic bytes. Legacy `.xls`/`.ppt` are
refused loudly (resave, or use the markitdown plugin). A scanned PDF yields
page markers and no text → that is a `待定` + an OCR note, not a failure.

Two PDF refinements (2026-07-17):

- **Always run doc_extract before parking a big PDF as "needs OCR"** — a
  21MB/185-page lecture deck assumed scanned turned out born-digital (54k
  chars in one second). Size and page count predict nothing; only the probe
  does. OCR (`marker_single` in the `marker-ocr-3.12` venv) is the fallback
  for genuine screenshot/scan PDFs, and even then extract the text layer
  first as the comparison baseline.
- **Slide decks with incremental builds** repeat each slide N times with one
  bullet added per page (185 physical → 109 logical pages). Dedup before
  reading: split on page markers, then keep only the last page of each
  consecutive prefix-run (`content.startswith(prev[:60])`). Beamer bold also
  renders as per-char triplets ("聊 聊 聊天 天 天") — collapse with
  `re.sub(r"(\S) \1 \1", r"\1", text)`.

### 3. Distil into a KB note

Write to the personal engineering KB (`{vault}/30_Resources/KB/<domain>/<kind>s/`),
following the `kind` conventions (`case` / `concept` / `formula` / `lesson`).

What makes these notes worth writing:

- **Keep every number.** The engineering value is in the quantities — circulating
  currents, spacings, cost per km, insulation thicknesses. A note that
  paraphrases without numbers is a summary, not knowledge.
- **Record the ORIGIN, not just the conclusion.** "West-Northwest 220kV was
  found to have excessive sheath circulating current in service" is why the
  phase-sequence study exists — that framing is worth more than the result.
- **Flag what the source got wrong.** These are competition drafts and vendor-
  adjacent reports: placeholder text, conclusion figures that contradict the
  body, copy-pasted parameter tables, product marketing dressed as analysis.
  End every note with a **源文档状态** section saying which parts are trustworthy.
- **Add what the source didn't notice.** Cross-check against the existing KB —
  e.g. the optimum phase sequence minimises sheath current but *maximises* earth
  current, which the report never discusses.
- **Link into the existing graph.** Run `python scripts/kb_link_audit.py --root
  "30_Resources/KB"` afterwards; `related:` links must resolve (0 broken).
- **Cross-post career/NIW evidence when a mined win warrants it.** These archives
  are Kevin's own award-winning engineering work. When a batch surfaces a design-
  competition win, an excellent-design award (国网优秀工程设计申报), or a named
  science-technology award (e.g. the 2018 国网经研体系科技进步一等奖 for the
  盾构钢筋接地 project), add a `[[wikilink]]` evidence bullet to
  `10_Projects/NIW-Application/NIW-Application.md` (`### Evidence`) and/or an entry
  to `20_Areas/Career/interview-story-bank.json` — the KB note stays the primary
  artifact; the career note cites it. State the award name/prize/role **factually
  and flag it for verification** (rankings, dates, author position) — never invent
  a placement (`feedback_portfolio_no_invent`). Many awards were already translated
  for the immigration folder but never logged in the evidence ledger; the mine is
  where they resurface. Watch for **CNKI `.caj` papers** — `doc_extract` now refuses
  them loudly (open in CAJViewer → export PDF); park as `待定`, but note the paper
  exists as publication evidence.

### 4. Record the judgment

Every mirror copy gets the same verdict — use `--all`, and **name the copy count
in the note** so a later reader knows it was a batch call:

```bash
python scripts/fileindex_scan.py status "<path substring>" --all \
  --set 完全索引 \
  --note "全文已提取入KB: <what was taken>。<what remains / what is unreliable>" \
  --vault-ref "30_Resources/KB/<domain>/<kind>s/<slug>.md"
```

Status discipline (the one rule the whole ledger rests on):

| Status | When | Note |
|---|---|---|
| `完全索引` | 原文或全部信息已入金库 | say what was taken |
| `部分索引` | 部分入库 | **mandatory** — say what was taken AND what remains |
| `排除` | 无知识价值 | **mandatory** — say why |
| `待定` | 需人工判断 (扫描件待 OCR, 隐私边界未定) | say what is blocking |

An unexplained status is a lie. The server and the CLI both refuse a bare
`部分索引` / `排除`.

**Third-party personal data is a hard boundary, not a judgment call.** These
work/research trees contain other people's data: student assignment
submissions + grade books (UniSA ChatGPT-grading `data/`), customer chat
transcripts (Tawk/ClickConnector histories), volunteer/need-seeker contact
sheets (capstone CERRG). The *methodology* around them (prompts, schemas,
category aggregates, Kevin's own per-conversation verdicts) is minable; the
third-party content itself is `排除` with a note naming it as personal data —
**never extracted into the KB, not even as examples**. Watch for it hiding
inside otherwise-minable files: a prompt template that embeds a real student
essay as its baseline anchor gets its *architecture* extracted with the essay
omitted.

Beware generic filenames (`二、主要技术经济指标汇总表.doc`) — `--all` matches by
substring and these repeat across unrelated projects. Confirm the family list
before batching. (The app's judge modal shows it; the CLI prints it when the
match is ambiguous.)

### 5. Close the round

```bash
python scripts/fileindex_scan.py export       # judgments → vault ledger.jsonl
python scripts/fileindex_scan.py report       # refresh the dashboard
```

Paste the report into the **状态仪表盘** section of the project note, and append
each new KB note to its **台账** list. Recovery contract: `rescan +
import-ledger == full state` — the ledger must always be exported before the
session ends.

## Report to the user

Lead with **what was learned**, not what was processed. The mining is
bookkeeping; the engineering insight is the product. Then: the ledger delta
(完全/部分/待定 counts), and any decision the user owes you (an OCR batch, a
privacy boundary for `.eml`, a corpus worth promoting).

## When NOT to use this

- **Scanned PDFs in bulk** — they need an OCR pass (`plugins/ocr`, marker-pdf
  venv), which is its own round with its own cost. Park them as `待定`.
- **`.eml` (7,192 emails)** — the privacy boundary has not been decided. Ask
  first; never bulk-ingest mail.
- **`.dwg` (14,207 drawings)** — no local text to extract. These are indexed by
  filename/project only.
- A corpus where you cannot tell knowledge from noise — fix the exclusion rules
  in `fileindex.toml` first, then mine.
