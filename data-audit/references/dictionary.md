# The data dictionary — the contract every check reads

A workbook with three sheets: `variables`, `rules` and `legend`, written RTL when the language is
Hebrew. Column headers exist in Hebrew and English; `profile_dict.py import` maps either.

## `variables` sheet — one row per variable

| column | meaning | example |
|---|---|---|
| `var` | analysis name (stable across files) | `OIS_1` |
| `source_names` | where it lives: `file::column; file::column` | `wave1::Q71_1; wave2::Q73_1` |
| `label` | short label | identity — positive feelings |
| `item_text` | full wording as asked | |
| `source_item`, `source_ref` | original item and the scale's source | Mael & Ashforth (1992) |
| `type` | `id, datetime, likert, integer, numeric, binary, categorical, text, constant, empty` | `likert` |
| `unit` | for physical and sensor measures | bpm |
| `min`, `max` | valid range | 1, 7 |
| `allowed` | for categorical: `code=label; code=label` | `1=גבר; 2=אישה` |
| `text_map` | text → code: `text=>code; …` | `7 - מסכים מאוד=>7` |
| `missing_codes` | values that mean missing | `99; -1` |
| `scale`, `subscale` | membership | `OIS` |
| `reverse` | `yes` / `no` / `?` (suspected) | |
| `required` | must be answered when asked | `yes` |
| `available_in` | which files/waves carry it | `T1; T2` |
| `condition` | routing: when the item is asked (pandas expression on `var` names) | `gender == 2` |
| `privacy` | `direct`, `indirect`, `sensitive`, empty | `direct` |
| `status` | `hypothesis` or `confirmed` | |
| `evidence` | why the entry says what it says (counts, source, who confirmed) | |
| `notes` | | |

## `rules` sheet — checks that span variables

| column | meaning |
|---|---|
| `rule_id` | R01… |
| `kind` | `test_ids` (expression = list of IDs), `row_rule` (must be true), `exclude` (rows to drop if true), `scale_size` (`WarE=10; OIS=11`: items per scale in the source instrument) |
| `expression` | pandas expression on `var` names, e.g. `tenure_years <= age - 16` |
| `severity` | blocking / decision / info |
| `description`, `status` | as above |

## Hypothesis → confirmed

The draft marks everything `hypothesis` and writes its evidence: counts, observed range ("from data,
not from a source"), the label variants it saw, isolated values that look like missing codes,
negative item-total correlations that suggest reversal. Confirmation needs a source: the
questionnaire, the platform export settings, the researcher's word (recorded with date). Ask only
about what changes the result; batch the rest.

What the draft must never do: set a range from the data alone and call it valid, decide that a
reverse item is reversed, decide that 9 means missing, or merge two files' columns because their
codes match. Codes collide (Q73 = identity in one family, aesthetics in another); `match.py`
compares the question text, item by item.

## Several sources, one dictionary

For multi-file studies, one row per concept, `source_names` listing every file::column that carries
it. A concept asked with different wording in two versions gets two rows unless the researcher rules
they are the same. Different response formats for one item (text in T1, numbers in T2) are recorded
per source in `text_map`/`evidence`.

## Sensors and organisational data (stage 3)

Same contract, more columns used: `unit`, sampling rate and clock in `notes`, `privacy` for HR fields
(salary, health, performance ratings are `sensitive`).
