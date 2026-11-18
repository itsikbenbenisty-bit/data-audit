# Verification layer E — two blind reviewers

The session that cleaned the data is the worst judge of it. Before the cleaned file is delivered,
a fresh agent that did not do the work reviews it. Spawn it with the Agent tool (in ChatGPT: a new
chat with only these files). Give it **paths, not conclusions**: the raw file, the final version,
`log.jsonl`, `manifest.json`, the dictionary and the decisions file — never the cleaning session's
summary. Identifying data stay where they are: the reviewer runs on the same machine.

## Two reviewers, two views

Run two reviewers, each blind to the other and to the cleaning session:
- **Reviewer 1 — the data:** the report, RAW, FINAL and the dictionary. Recompute the headline numbers
  (N, scale means, α) from FINAL and check them against the report.
- **Reviewer 2 — the trail:** the report, the log, the manifest and the N-flow. Check that the trail
  explains every number in the report and every difference between RAW and FINAL.
A defect found by either one is a defect. Agreement of both is the accept.

## Prompt (adapt the file list to the reviewer)

> You are auditing a data-cleaning job you did not do. Your task is to find what is wrong, not to
> approve it. Files: RAW=…, FINAL=…, LOG=…, MANIFEST=…, DICTIONARY=…, DECISIONS=….
> The scripts in SCRIPTS=… are available (replay.py, verify.py, golden.py, scan.py, measure.py).
>
> 1. Run `replay.py WORK --strict` and `verify.py WORK --all`. Report every failure verbatim.
> 2. Pick 10 rows at random yourself (not from a list you were given). For each, open the raw file
>    and the final file and compare every column; explain every difference from the log alone.
> 3. For every decision in DECISIONS, check that the log applies it exactly as decided — same rows,
>    same rule, nothing extra.
> 4. Re-run `scan.py` on FINAL with the dictionary. Any blocking finding left is a defect unless the
>    decisions file says why it stays.
> 5. Check the N-flow: raw N − every removal = final N, and every removal has a reason.
> 6. List anything you could not check and why.
>
> Answer as JSON: {"verdict": "accept"|"reject", "defects": [{"where", "what", "evidence"}],
> "unchecked": [...]}. "accept" requires zero defects.

## After the review

A `reject` goes back to the cleaning step that caused it; the fix becomes a new version with its own
log entry, and the review runs again with a **new** reviewer (one that has not seen its own earlier
verdict). Record each review's verdict and defects in the decisions file.
