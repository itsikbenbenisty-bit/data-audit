---
name: "data-audit"
description: "Audit, document and clean any research or organisational data file (surveys, biographical/HR records, sensor exports) and build its data dictionary, with every change logged and verified. Use on \"בדוק את הקובץ\", \"ניקוי נתונים\", \"מילון נתונים\", \"data audit\", \"codebook\"."
---

# data-audit (layers 0–9, verification A–F; surveys, HR records, sensors)

A careful research assistant for data, not a cleaning robot. It **finds** problems, **explains** them
with evidence, **proposes** fixes, and **lets the researcher decide**. What it does not understand it
asks about, or states as a hypothesis with the test that would settle it. Nothing is changed silently,
and every change is checked by code that did not make it.

Callable on its own or from other skills (an analysis skill can call it before its analysis steps).

## Principles (they override convenience)

1. **The raw file is never touched.** Work on a copy; every version is `vNN.csv` with a sha256 in
   `manifest.json`; `raw_unchanged` is checked at every step.
2. **Load as text.** Values stay strings until the dictionary says what they are — no silent coercion
   of "7 - מסכים מאוד", "0000" or "2025-11-19T05:10" into something else.
3. **Every row keeps its key** `_row` (its line in the source). Rows are removed only through the log.
4. **The dictionary is the contract.** Every check reads it; every inferred entry is `status=hypothesis`
   with its evidence until the researcher or a source confirms it.
5. **Findings carry evidence and options**, and a severity: `blocking` (analysis is wrong if ignored),
   `decision` (a researcher's call, set in advance where possible), `info`.
6. **The researcher decides.** The skill recommends; it never picks a fix for a `decision`.
7. **Every change is verified** (layer A: the diff equals the log; layer B: invariants). A failed check
   stops the pipeline — fix the cause, never the check.
8. **Privacy first.** Identifying data stay on the machine they came from; nothing is uploaded, shared
   or published without the researcher's explicit yes. Files named "anonymous" are tested, not trusted.
9. **One pattern, one finding.** The same recode on 40 items is one decision, not 40.
10. **No outcome-driven cleaning.** A better α, a cleaner p, or a nicer mean is never a reason to drop,
    reverse or exclude anything. Reverse keys come from the instrument; the data may only question them.
11. **The project decides what is project-specific.** Some checks are general (duplicates, ranges,
    verification). Others belong to one study: which ID links files, whether teams are aggregated (ICC/
    rwg), which columns are quasi-identifiers and what k the ethics approval requires, effective-dated HR
    records, the date order, preregistered rules, the Method's audience. These live in `project.json`;
    a check that needs an unset setting is skipped and reported as "not checked — ask", never run on a
    guessed default. Never carry one study's rules (its ID column, its instrument quirks) into another.

## Start of a run — ask once

Ask (one AskUserQuestion round, skip what the caller already said):
- **Language** of reports and dictionary: עברית / English (both are built in; default עברית).
- **What the data are** and what the researcher already knows (a codebook? a questionnaire? a
  collection window? which column is the ID?). Existing documentation is imported, not re-guessed.
- **Where the data must stay** (local only? may a copy go to the cloud?). Default: local only.
- Then `python $SC/project.py init RUN --lang he` and fill the project-specific settings the data need
  (`project.py show RUN` lists them, each with its meaning). Ask only about settings this dataset touches;
  record who answered. Re-ask when a later file raises a new one (a team column appears, a new wave).

## Workflow

### Where the code lives

The scripts and references sit beside this file, in `scripts/` and `references/`. `$SC` = the
`scripts/` folder; find it once with `find / -path "*data-audit/scripts/scan.py" 2>/dev/null`. The skill
folder may be read-only: write every output to a run folder (`RUN`) in the working directory, never
inside the skill and never next to the raw data.
- **Python 3.10+** with pandas, openpyxl, scipy, and pyreadstat for SPSS files. If one is missing, install
  it (`pip install pyreadstat`, adding `--break-system-packages` where pip asks for it); if it cannot be
  installed, ask the researcher to export the `.sav` file to `.xlsx` or `.csv`.
- **Where the data are processed.** In Claude on the web or the app, files attached to the chat are
  processed in Claude's cloud workspace. Before the first file, say so, and ask the researcher to remove
  identifiers (names, ID numbers, phone numbers, e-mails) or to use a de-identified copy, unless their
  institution's rules allow otherwise. When Claude runs on the researcher's own computer (Claude Code, or
  the desktop app with a linked folder), run everything there, so the data never leave it.
- At the end, hand the researcher the run folder's deliverables (listed under Step 5).

**Step 0 — Intake and provenance (layer 0).** For a file or a whole folder:
`python $SC/intake.py <files or folders> --out RUN/intake --lang he`
It hashes every file and every sheet, and reports: identical files, cell-identical files in different
formats, row subsets and content subsets (a working copy with IDs or dates changed), files whose name
contradicts their content ("75 respondents" with 62 rows, "anonymous" with real IDs), Excel-serial
dates, duplicate column names, extra sheets, identifier columns and which files share identifiers
(linkage table, unlinked files, stray shared IDs), and versions of one instrument. **Gate:** the
researcher confirms which file is the master of each wave before anything else — present the intake
findings and ask.

**Step 1 — Dictionary.** Import theirs if it exists, otherwise draft one:
`python $SC/profile_dict.py import THEIR_DICT.xlsx --out RUN/dict.xlsx` or
`python $SC/profile_dict.py draft DATA --label NAME --out RUN/dict.xlsx`
The draft infers type, range, labels→codes (labelled end points, and fully labelled scales such as
"בכלל לא מסכים … מסכים מאוד" ordered by intensity words and checked against the anchors in the
question), missing codes (an SPSS file's declared user-missing codes are carried over as a source;
labels such as "don't know", "refusal", "hard to say", or a labelled code standing apart above the
scale — 1..5 then 7, 8 — are proposed as missing, so they never enter a mean as the scale's extreme),
scale families, ranking items, and reverse items suspected from the data (the
sign of the scale's first principal component, oriented to the majority of items — not a corrected
item-total r, which misleads when 2 of 5 items are reversed) or from negative wording — all as
hypotheses with evidence.
**Gate:** walk the researcher through the hypotheses that matter (ID, scales and ranges, missing
codes, reverse items, routing conditions); confirmed entries become `status=confirmed` with a source.
See `references/dictionary.md`.

**Step 2 — Cross-file matching** (several files or waves):
`python $SC/match.py FILES... --out RUN/match [--id FILE=COL ...]`
Same code with different question text (never merge on the code alone), a single code in one file
that is an item-family prefix in another (Q140 vs Q140_1…), shared codes with no text to compare,
same question under a different code, ID linkage between files.

**Step 3 — Scan (layers 1–5 basic):**
`python $SC/scan.py DATA --dict RUN/dict.xlsx --out RUN/scan [--id COL] [--window a,b] --lang he`
→ `findings.json`, `findings_<lang>.md`, `decisions_template.csv`. Routed (either/or) blocks are
recognised and not counted as abandonment. Numeric checks include special values stored as data (Inf,
NaN, #DIV/0!), a few negatives in an otherwise positive measure, values more than 6 robust SDs from the
median (judged on a log scale when the measure is right-skewed, so tenure or income tails are not
flagged), and two measures of one thing that disagree in a few rows (swapped columns, unit errors,
hand-logged values). Rows identical only because they are empty or all-zero (a device not worn) are
reported apart from true duplicates. Also: Excel serial numbers beside written dates in one column,
accounting identities nobody declared (a + b = c in most complete rows, with the rows that break it),
and one name in several rows (one person counted twice). A title block above an Excel or CSV header is
skipped by the loader and kept in the metadata. The checks and their fix options are in
`references/layers.md` and `references/fixes.md`.
**Gate:** every `blocking` finding gets a decision; `decision` findings get a decision or an explicit
"later, by rule X". Ask interactively, a few findings at a time, with the options as choices. Record the
answers in a copy of `decisions_template.csv` (`RUN/decisions.csv`), then
`python $SC/decisions.py --findings RUN/scan/findings.json [--findings RUN/measure/measure.json] --decisions RUN/decisions.csv --out RUN`
→ `decisions_status.json`: every finding as decided / deferred / open. It exits 1 while a blocking finding
is open; analysis skills (stat-analysis) read this file and will not run until it is clean.

**Step 3b — Measurement and response quality (layers 5–7):**
`python $SC/measure.py DATA --dict RUN/dict.xlsx --out RUN/measure [--id COL] [--duration COL]
[--t2 WAVE2 --t2-dict DICT2 --t2-id COL2] --lang he`
Reliability per scale as the data stand and as the dictionary scores them (α with Feldt CI, item-rest r,
α if deleted); reverse keying tested both ways — an unmarked item that correlates negatively, an item
marked reverse that already looks reversed in the file (reversing again would break the scale), and
wording-based reverse hypotheses arbitrated by α; items of one scale on different ranges; ranking
(ipsative) items recognised and left out of α. Careless responding by several indices (longstring,
Mahalanobis D² on items of reliable scales, even-odd consistency, seconds per item): a respondent is
flagged when two or more agree, or when one index is at a strong level on its own — longstring at least
twice the longest scale (straightlining), or D² on all items p < .001 by the exact Beta distribution
(Wilks, 1963; only when n ≥ 2 × items) for random responding. Each flag names its type, because
straightliners and random responders are caught by different indices (researcher's decision, 17.11.26). Two waves: linkage, fields that should be stable but changed,
change that is extreme on all reliable scales at once (two people under one ID), test-retest r. Nested
data (`--group COL` or project `nested` + `aggregate_to_group`): ICC(1), ICC(2), rwg(j) and group sizes,
only when the study aggregates to groups.
Run it on the raw file and again on the final version (`RUN/work/vNN.csv`): on a version it reads the log,
so an item the dictionary marks reverse that the log already reversed is not reversed again, and a log
reversal the dictionary does not support (or a partial one) is a blocking `reverse_log_conflict`;
`measure.json` records `reverse_state`, the file's sha256 and its version.
**Gate:** reverse keys are decided from the source instrument; the data only arbitrate. Exclusion
rules for careless responding are set before results are seen.

**Step 3c — Time series and sensors (wearables, loggers, daily summaries):**
`python $SC/sensor.py FILES_OR_FOLDERS --out RUN/sensor [--time COL] [--entity COL] [--glob "UDSFile*"]`
Stacks all files with their source, so overlapping exports are compared, not merged: duplicate time
stamps (identical vs conflicting = revised re-exports), gaps and sampling changes, local-vs-UTC
offsets (travel, DST, corrupted times), physically impossible values by measure, sentinel codes
(-1/-2, a spike at 255), runs of identical values that the measure's own repeat rate does not explain,
days not worn or partly covered, heart-rate and stride logic, identifiers carried in every record.
With `--stream2 FILES` it finds a clock offset between two devices; with `--ema COMPLETED SCHEDULED`
it reports EMA compliance below the project's minimum (`--ema-min`).

**Step 3d — Organisational / HR / biographical data:**
`python $SC/hr.py DATA --out RUN/hr [--map role=column ...] [--asof DATE] [--quasi COLS --k K] [--project RUN]`
Finds roles by column name (Hebrew or English): dates that contradict each other (exit before hire,
hired at 5, stated age or tenure vs the dates), reporting lines (managers who are not employees,
self-managers, loops), pay outside its grade (robust), effective-dated records that overlap, people
still marked active after leaving, a privacy scan of every free-text column (e-mails, phones, numbers
passing the Israeli ID check digit, URLs, a name after a title), and small cells: combinations of
quasi-identifiers shared by fewer than k people (k-anonymity; columns and k from the project).

**Step 4 — Apply decisions, version by version.** In Python:
```python
import sys; sys.path.insert(0, SC)
from versioning import init, load_version, save_version
init(RAW, work=RUN+"/work", dict_path=RUN+"/dict.xlsx")            # v01 = raw as text + _row
df = load_version(RUN+"/work")
df2 = df[~df["_row"].isin([1, 112])]
save_version(df2, RUN+"/work", [{"action": "remove_rows", "rows": [1, 112], "finding": "F001",
    "rationale": "test row and empty row", "source": "researcher 17.11"}], note="2.1")
```
`save_version` writes the next version, appends to `log.jsonl` and runs layers A–B; it raises if the
change and the log disagree. One logical step per version. Log actions: `remove_rows, recode,
set_missing, set_value, reverse, add_column, drop_column, rename_column`. **Every entry carries its
payload** so the log alone can rebuild the data: `recode` → `"map"`, `set_value` → `"value"`,
`reverse` → `"min"`, `"max"`, `add_column` → `"values"`. Format: `references/verification.md`.

**Step 5 — Verify the whole history (layers C–F), then report.**
- C: `python $SC/replay.py RUN/work --strict --trace 15` rebuilds every version from the log with a
  second, independent implementation and compares cell by cell.
- D: `python $SC/golden.py RUN/work goldens.json` — numbers known from outside (the platform's N, a
  linkage count in the project log, a published mean). Ask the researcher for them.
- E: two fresh reviewer agents with `references/reviewer_prompt.md` — one gets the report + data +
  dictionary, the other the report + log + N-flow; paths, never your summary.
- F: the trace sheet from C (`trace_<lang>.md`): the researcher checks 15 random respondents by hand.
- `python $SC/verify.py RUN/work --all --nflow --lang he` → the N-flow table (`nflow_<lang>.md` and `nflow.json`) and the verification record.

- Method draft: `python $SC/method.py RUN/work --measure RUN/measure/measure.json --project RUN --lang he`
  → a data-preparation paragraph and a Van den Broeck et al. (2005) table, every number from the log;
  what the log cannot know is left in brackets [לאישור: …] for the researcher.

Deliver: the cleaned file, the dictionary, the decisions file, the N-flow, the log, the results of C–F,
and the Method draft.

## What to say, and how

- Lead with what blocks analysis, then decisions, then information. Numbers, not adjectives.
- Every finding: what, where (rows/IDs/columns), evidence, why it matters, options.
- Hypotheses are stated as such, with the test: "Q80_x looks like rank fields typed as text in 20 rows —
  check the questionnaire: was a number expected?"
- Never claim a file is clean. Say which checks ran, on what, and what they cannot see.

## Limits (say so when relevant)

Not built: high-frequency signal quality (artefacts within a heartbeat series), names in free text
without a title before them, PVQ-style centring identities (check them when the project centres scores).
Items per scale can be checked against the source instrument with a `scale_size` rule ("WarE=10").
Factor structure and measurement invariance are analysis, not audit: point to them, do not run them
here. Item meaning is
read by you, not by the scripts — the negation heuristic misses reversals like "מתעלמים מהדעות שלי";
read every item of a scale before confirming its keys.

## References (beside this file)

- `references/layers.md` — the ten-layer taxonomy and every check implemented, with severity.
- `references/dictionary.md` — dictionary columns, rules sheet, hypothesis → confirmed.
- `references/fixes.md` — the fix catalogue per check, and when each is defensible.
- `references/verification.md` — log format, layers A–F, N-flow, how the tests prove the checks work.
- `references/reviewer_prompt.md` — the blind reviewer (layer E).
- `references/chatgpt.md` — how to run the same method in ChatGPT (handover notes); the GPT's
  instructions and knowledge files are in the `chatgpt/` folder of the package.
