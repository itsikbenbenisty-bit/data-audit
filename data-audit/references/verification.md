# Verification — how every change is proven, and how the skill itself is tested

## Versions and log

`work/` holds `v01.csv, v02.csv, …`, `manifest.json` (raw path and sha256, each version's sha256,
rows, note, verified) and `log.jsonl`, one JSON object per change:

```json
{"step": "2.1", "version_from": "v01", "version_to": "v02", "action": "remove_rows",
 "rows": [1, 112], "vars": [], "finding": "F001",
 "rationale": "test row and a row with no answers", "source": "researcher, 17.11.2026"}
```

`action` ∈ `remove_rows, recode, set_missing, set_value, reverse, add_column, drop_column,
rename_column` (rename: `"from"`, `"to"`). Payloads make the log replayable (layer C):
`recode` → `"map": {"old": "new"}`, `set_value` → `"value"`, `reverse` → `"min"`, `"max"`,
`add_column` → `"values": {_row: value}`. `rows` is a list of `_row` values or `"all"`;
`vars` lists the columns touched. `save_version` fills `version_from/to` and refuses unknown actions
or a frame without `_row`.

## Six layers of verification

| layer | question | how | stage |
|---|---|---|---|
| A — diff ↔ log | did exactly what the log says happen? | cell diff between versions on `_row`: every removed row declared, every changed cell covered by an entry, every entry changed something, columns dropped/added as declared, raw sha256 unchanged | 1 |
| B — invariants | is the result still coherent? | N arithmetic, `_row` unique, removed rows never return, no item reversed twice, every entry has rationale and source, cleaning introduced no new dictionary violations | 1 |
| C — independent recomputation | is the declared change the change that was made? | `replay.py`: rebuild every version from the previous one using only the log, with a second simple implementation, and compare cell by cell; scale scores can also be recomputed against the project's own syntax | 2 |
| D — golden numbers | do we reproduce numbers known to be right? | `golden.py` + `goldens.json`: N per wave from the platform, linkage counts from the project log, published means | 2 |
| E — blind reviewer | does someone who did not do the work agree? | a fresh agent with `reviewer_prompt.md`, given paths, never the cleaning summary | 2 |
| F — sampling | do random rows survive a manual trace? | `replay.py --trace 15` writes a sheet: raw → every version → log entries, for 15 random rows and 3 removed ones | 2 |

A failed check stops the pipeline (`RuntimeError`). The fix is in the cleaning step, never in the check.

## N-flow

`verify.py WORK --nflow` → a table of rows per version with the step, the rows removed and the
reason. It is the source of every N in a methods section.

## How the skill is tested (`tests/run_all.sh`)

1. **Planted problems (mutation testing).** A synthetic survey with a manifest of 31 planted problems
   (blank row, test row, duplicates, label variants, missing items, quitters…) and a second file with
   out-of-range values and straight-liners. Gate: 100% of blocking problems found, ≥90% of minor ones,
   false alarms reported (each must be a real feature of the data, not an error).
2. **Sabotage.** Three deliberately wrong cleaning steps — an unlogged cell change, a declared
   reversal not done, a silent row removal — must each be caught by layer A/B. A fourth — a change
   that is declared, but with a different mapping than the one written — passes A/B by design and
   must be caught by C.
3a. **Measurement fixture.** A synthetic two-wave survey (300/300) with planted problems: an unmarked
   reverse item, an item marked reverse but already reversed, mixed item ranges, a scale of noise, 12
   fast random responders, 8 fast straight-liners, 6 gender values changed between waves, 5 IDs whose
   wave-2 person is someone else. Gate passed on four random seeds with no false alarms.
3. **Real data with documented problems.** A copy of a real multi-wave study whose team documented
   its data problems by hand (inventory + decision log). The skill's outputs are scored against that
   list. Stage-1 result: 28 of 30 documented issues found. Missed: a cleaned
   subset whose values and IDs were both changed (no shared rows or IDs left to link), and a cohort
   mislabelled in file names (needs outside knowledge).
   Caveat: the checks were refined on this set, so it is now a development set.
4. **Held-out real files** (three later files of the same study), answer key frozen from the project
   documents before the first run: 8/11 on the first, truly blind run, with routed DIT blocks
   misread as abandonment. After the fixes 11/11 — so this set is now a development set too.
5. **Real data, layer 6.** Reverse keys documented by the project (Sense of Power 2/4/6, aesthetics
   6–10): 19 of 24 item-waves found as hypotheses; for one wave, where the project found the SoP
   reversal unsupported (α .70 as is vs .33 reversed), the skill reports .698 vs .328 and questions it.

The maintainer runs all thirteen regression gates after every change to a script. The gates that use
real research data are not published; the synthetic ones are in `tests/` (`bash data-audit/tests/run_tests.sh`).

## Files written to cloud-synced folders

Synced folders (OneDrive) can truncate a file written while syncing. After copying scripts, compare
sha256 with the source before running them.

## Gate 11 — open datasets with problems documented by others (17.11.26)
Six public datasets: Davis (carData), bfi (psych), sat.act (psych), dirty_iris (de Jonge & van der Loo), IBM HR Attrition, Fitabase daily activity. The key comes from each dataset's documentation, computed with pandas. First blind run: 10/14. One miss was an error in the key itself (bfi.dictionary keys N1–N5 relative to emotional stability; the documented keys list has 7 reversed items), corrected from the documentation. The three real gaps (special values, Fitbit entity count and non-wear days, identical twin columns) and three false alarms (bfi reverse contamination ×2, IBM YearsWithCurrManager read as a manager) were fixed. A skewed-tenure false alarm the change introduced in gate 1 was caught by the regression run and fixed (log scale). After fixes: 14/14; extras: Davis case 17 (92 vs 101 kg reported — a real gap worth checking) and dirty_iris row 35 (petal 23 cm — a real error). All gates 1–11 PASS; gate 7 rose from 19/24 to 21/24.

## Gate 12 — blind test 2 (17.11.26)
Four datasets of kinds the earlier gates lacked: janitor's dirty_data.xlsx (messy Excel), validate::retailers (business rules), a real CBOS SPSS survey (declared missing codes, non-answer labels), careless::careless_dataset (simulated careless responding). Key written from documentation before the first run. First blind run: 7/18 — the loader read the title row as the header (six misses cascaded from it), SPSS missing codes were lost on loading, non-answer codes (7 = hard to say, 8 = refusal) stood inside the scale's range, identities were not looked for, and a negative in 24 values fell under the n ≥ 30 floor. The scorer was corrected once, making it stricter: it had credited "teachers listed twice" from a different finding, which also exposed two false blocking findings ("Subject" read as an ID). After fixes: 17/18. The regression run then caught a new false alarm (a count column, TrainingTimesLastYear, read as a date), fixed. All gates 1–12 PASS. Open: careless responding — straightliners and random responders are caught by different indices, so the "two indices agree" rule flags neither (0 of the documented ~20%); a decision for the researcher.
Decision 17.11.26 (researcher): careless responding flags a respondent when two indices agree OR one index is at a strong level — longstring at least twice the longest scale (straightlining), or Mahalanobis D² on all items p < .001 by the exact Beta distribution of the sample D² (Wilks, 1963), run only when n ≥ 2 × items (random responding). Each flag names its type. Gate 12 now 18/18 (42 of 200 flagged vs the documented ~20%). On the real study's files the all-items test does not run (n < 2 × items), so their flags are unchanged; the measurement fixture stays at ~20 of 300.
