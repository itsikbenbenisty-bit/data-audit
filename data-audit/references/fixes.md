# Fix catalogue — options per finding, and when each is defensible

The skill offers options; the researcher chooses. Prefer a rule decided before looking at results
(pre-registered or written into the decisions file before analysis) over a case-by-case choice.
Every applied fix becomes a log entry with `rationale` and `source`.

| finding | options | defensible when | note |
|---|---|---|---|
| empty / test / header-residue rows | remove | always, with the rows listed | |
| exact duplicate rows | keep first copy, remove the rest | identical content | report how many |
| duplicate ID, identical answers | keep one | | |
| duplicate ID, conflicting answers | keep most complete (tie → earliest); keep both and flag; exclude both | a rule set in advance | never average two people |
| junk ID | set to missing, keep the row; recover from another file; drop | missing if the row is used only cross-sectionally | junk IDs block linkage |
| ID of unusual length | check which identifier was asked for; re-link by hand; keep as another valid form with a mapping | a national ID typed for a personal number is also a privacy problem | |
| near duplicates | inspect; keep both | | could be friends answering together |
| text answers with a map | recode with the documented map | map from the questionnaire or export settings | typo variants listed and mapped explicitly |
| free text in a numeric field | recode by hand with a documented map; set to missing | when the intended number is unambiguous | log every manual value |
| missing codes present | convert to missing | the code is documented | never leave 99 in a mean |
| out of range | set to missing; correct from source; keep and document | correction only from a source | |
| label variants | map to the allowed label | spacing/spelling only | |
| bad dates / outside window | parse with an explicit format; exclude outside the collection | the window is documented | |
| partial / abandoned | flag; apply the missing-item rule; exclude below a threshold | threshold set in advance (e.g. ≥2/3 of items for a scale mean) | |
| straight-lining | flag and report the share; exclude by a rule across several scales | a rule set in advance; reverse items contradict identical answers | one scale alone is weak evidence |
| skip-logic violation | set to missing; check the survey routing | | |
| file is a copy / subset | choose the master by a rule ("the file with the most complete records") and mark the others superseded | | never delete the others |
| "anonymous" file with real IDs | treat as identifying; rename or scrub before sharing | | flag in the project log |
| duplicate column names | select by position and item text; rename to semantic names | | never select by code alone |
| code collision across files | map by question text; rename to semantic names before merging | | |

## Never

- Impute, winsorise or drop outliers as "cleaning" — those are analysis decisions with their own report.
- Change a value without a log entry, or fix the check instead of the cause.
- Choose a fix for a `decision` finding on the researcher's behalf.
