# The ten layers — what is checked, by which script, at what severity

The taxonomy orders problems by what they break. A lower layer is fixed before a higher one is read:
a duplicate row (layer 2) distorts every reliability coefficient (layer 6).

| # | Layer | Question | Stage |
|---|---|---|---|
| 0 | Provenance | Which file is the data, where did it come from, is it what its name says? | 1 |
| 1 | Structure | Are rows respondents and columns variables, and are they all accounted for? | 1 |
| 2 | Identity and linkage | One person = one row? Can rows be linked across files and waves? | 1 |
| 3 | Values vs dictionary | Is every value one the dictionary allows? | 1 |
| 4 | Cross-field logic | Do answers agree with routing and with each other? | 1 |
| 5 | Response quality | Did the respondent read the items (straight-lining, speed, inconsistency)? | 1 basic / 2 |
| 6 | Measurement | Do scales hold together; are reversed items reversed exactly once? | 2 |
| 7 | Levels and time | Waves, units, teams, sensor clocks, sampling rates | 2–3 |
| 8 | Privacy | Identifiers, quasi-identifiers, free text naming people | 1 partial / 3 |
| 9 | Reporting consistency | Do the N, means and decisions reported match the log? | 1 (N-flow) / 2 |

## Layer 0 — `intake.py`

| check | what it means | severity |
|---|---|---|
| `identical_files` | same bytes under two names | info |
| `same_content_different_file` | same cells, different file (format, extra sheet) | info: pick one |
| `row_subset` / `row_overlap` | one file's rows are inside another's | decision: which is master |
| `content_subset` | same answers, but IDs/dates/metadata changed — a working copy | decision |
| `id_subset` | all IDs of one file in another with the same columns | decision |
| `id_replaced_by_row_number` | an ID column elsewhere holds 1..n here — linkage lost | info / decision |
| `id_linkage_table` | which files share identifiers, through which columns, how many | info (linkage plan) |
| `name_count_mismatch` | the file name promises N rows, the file has another number | decision |
| `anonymous_name_but_identifiers` | an "anonymous" file has ID-like columns; verdict says whether the IDs match a raw export (confirmed leak) or only other working files (pseudonyms?) | blocking if confirmed |
| `dates_as_excel_serials` | dates stored as 45980.19… | decision (convert, document) |
| `duplicate_column_names` | one name, two columns (kept as NAME#2) | blocking for selection by name |
| `merged_cells` | decision | merged Excel cells: the value sits in the first cell only |
| `extra_sheets` | other sheets in a workbook — pivots, a codebook, another version; each is described | info: open them |
| `instrument_versions` | two files share ≥50% of columns: the differences are listed | info → mapping |
| `column_signature_groups` | which files have exactly the same columns | info |
| PII candidates | email/phone values, identifying names, question text naming a person | decision |

## Layers 1–5 — `scan.py`

| layer | check | severity | notes |
|---|---|---|---|
| 1 | `empty_rows` | blocking | judged on content columns; rows with only an ID/timestamp count |
| 1 | `duplicate_column_names`, `unnamed_columns` | blocking / decision | |
| 1 | `header_residue` | blocking | a row that repeats the header or question text |
| 1 | `test_rows` | blocking | test-like text, or IDs listed in a `test_ids` rule |
| 1 | `not_in_dictionary`, `missing_from_data` | decision / blocking if required | |
| 1 | `empty_column`, `constant_column` | info | |
| 1 | `scale_size_mismatch` | blocking | rules sheet `scale_size` ("WarE=10"): items in the dictionary vs the source instrument |
| 2 | `missing_id` | blocking | |
| 2 | `junk_id` | blocking | repeated digits, 0000…, letters or text inside a numeric ID |
| 2 | `id_length_mismatch` | decision | length differs from the common one; reports how many pass the Israeli ID check digit (a national ID typed instead of a personal number: privacy and linkage) |
| 2 | `duplicate_id_identical` / `duplicate_id_conflicting` | blocking | identical copies vs different answers (with completeness per copy) |
| 2 | `duplicate_rows` | blocking | identical on every content column, ID/time ignored |
| 2 | `near_duplicates` | decision | ≥95% of item answers equal |
| 3 | `text_answers` | decision | text with a known text→code map (one finding per pattern) |
| 3 | `non_numeric` | blocking | text with no map — one finding per item family |
| 3 | `missing_codes_present` | decision | 9/99/-1 would enter means |
| 3 | `out_of_range`, `non_integer` | blocking | against a confirmed or hypothesised range (stated) |
| 3 | `label_variant` | decision | "תפעול " vs "תפעול": spacing/spelling variants of an allowed label |
| 3 | `not_allowed` | blocking | |
| 3 | `bad_date`, `outside_window` | blocking / decision | |
| 3 | `date_order_ambiguous` / `date_order_mixed` | decision / blocking | all day/month ≤ 12 (ask the order) / two orders in one column |
| 3 | `partial_response`, `abandoned_block`, `item_missing` | decision / info | abandoned blocks: one finding, broken down by scale |
| 4 | `skip_logic_violation`, `required_missing` | decision | from the dictionary `condition` column |
| 4 | `rule_violation` | per rule | rules sheet: `row_rule` / `exclude` |
| 4 | `routed_blocks` | info | blocks answered either/or (≤2% answer both): routing, not abandonment; routes listed |
| 5 | `straightlining` | decision | the same answer on every item of ≥2 scales, or of a scale with reversed items |
| 5 | `straightlining_single` | info | one scale only, or a scale where >20% answer identically (a property of the scale) |

## Layers 5–7 — `measure.py`

| layer | check | severity | notes |
|---|---|---|---|
| 6 | `reverse_suspected` | decision | not marked reverse, item-rest r < −.1 and reversing raises α by ≥ .05 (or: two subscales) |
| 6 | `reverse_already_applied` | blocking | marked reverse, positive as the data stand, negative once reversed |
| 6 | `reverse_not_supported` | decision | marked reverse, still weak after reversal |
| 6 | `reverse_hypothesis` | decision | negative wording suggests reversal; α as is vs reversed, verdict |
| 6 | `mixed_item_ranges` | blocking if confirmed | items of a scale on different ranges |
| 6 | `low_reliability`, `weak_item` | decision / info | α < .60 with Feldt CI; item-rest r < .20 |
| 6 | `ranking_items` | info | ≥90% of rows give every item a different value: ipsative, no α |
| 5 | `careless_multi` / `careless_single` | decision / info | longstring (> Q3 + 3·IQR), Mahalanobis D² on items of reliable scales (p < .01), even-odd < .30 (≥3 reliable scales), < 2 s per item; flagged when ≥2 agree |
| 5 | `speeding` | decision | < 2 s per answered item |
| 7 | `unmatched_ids` | info | IDs in one wave only (duplicated IDs left out of linkage) |
| 7 | `stable_field_changed` | decision | a field that agrees for ≥90% of matched people changed for the rest |
| 7 | `small_groups`, `aggregation_weak` | decision | only with a group column and aggregation: groups < 3; ICC(1) < .05 or rwg(j) < .70 |
| 7 | `extreme_change` | decision | Σ z² of change over reliable scales ~ χ²(k), p < .001: two people under one ID? |

## Time series — `sensor.py`

| check | severity | notes |
|---|---|---|
| `bad_time`, `odd_utc_offset` | blocking | unparseable stamps; local − UTC not a whole quarter hour |
| `duplicate_records_identical` / `_conflicting` | decision / blocking | same entity + time; conflicting = revised re-export, two devices |
| `gaps`, `sampling_change` | decision | intervals > 3× the usual; usual interval changes by month |
| `timezone_changes` | info | several UTC offsets: DST, travel, wrong device zone |
| `impossible_values` | blocking | outside physical ranges by measure name (HR 25–230, SpO2 70–100, …) |
| `sentinel_values` | decision | −1/−2 in non-negative measures; 255/65535/9999 only when they spike above their neighbours |
| `stuck_values` | decision | runs longer than the measure's own repeat rate explains (n·p^(L−1) < .01) |
| `empty_records`, `partial_coverage` | decision / info | not worn (missing, not zero); < 90% of the period covered |
| `logic_heart_rate`, `logic_stride` | blocking / decision | min/resting > max; distance per step outside 0.3–2 m |
| `identifier_fields` | decision | profile keys, device ids, uuids in every record |
| `clock_offset` | blocking | with `--stream2`: the lag that best aligns two devices is not zero (nearest lag for periodic signals) |
| `low_ema_compliance` | decision | with `--ema`: answered prompts / scheduled below the project's minimum |

## Organisational data — `hr.py`

| check | severity | notes |
|---|---|---|
| `bad_date`, `exit_before_hire`, `implausible_birth`, `implausible_hire_age`, `implausible_age` | blocking | Excel serials and day-first dates handled |
| `age_disagrees_with_birth`, `tenure_disagrees_with_dates` | decision | > 1.5 years apart as of `--asof` |
| `tenure_exceeds_age`, `negative_tenure`, `non_positive_pay`, `self_manager`, `hierarchy_cycle` | blocking | |
| `manager_not_employee` | decision | external manager, former employee, typo |
| `pay_outside_grade` | decision | > 4 robust SDs from the grade median: monthly vs yearly, part time, wrong grade |
| `overlapping_records` | blocking | effective-dated rows of one person whose periods overlap |
| `stale_active_status` | decision | marked active after the exit date |
| `small_cells` | blocking | quasi-identifier combinations with fewer than k people (columns and k from the project) |
| `pii_in_text` | blocking (e-mail, phone, ID, GPS) / decision (name after a title, long numbers, URLs) | every free-text column |

## What the skill does not see yet

Factor structure and invariance (analysis, not audit), team/unit structure, sensor clocks and gaps,
organisational data rules, free-text privacy. Item meaning beyond the negation heuristic. Say so.

## Added 17.11.26 (open-datasets experiment)
- **special_value** (layer 3, blocking): Inf, NaN or spreadsheet errors (#NUM!, #DIV/0!) stored as data in a numeric variable — missing, not a number.
- **negative_in_positive** (layer 3, decision): a few negative values (≤2%) in a measure that is otherwise never negative.
- **extreme_value** (layer 3, decision): more than 6 robust SDs (MAD) from the median. A robustly right-skewed measure (Bowley skewness >0.2, non-negative: tenure, income, counts) is judged on log1p, so its natural tail is not flagged.
- **pair_disagreement** (layer 4, decision): two measures of one thing (Spearman ≥.85) that disagree in a few rows — swapped columns (Davis case 12), a unit error, or manually logged values. Columns identical in most rows: every departure counts, up to 10% of rows.
- **duplicate_empty_records** (layer 2, info): rows identical only because they are empty or all-zero (a device not worn) — not copies; separated from duplicate_rows.
- Reverse-key hypotheses in the draft come from the sign of the scale's first principal component, oriented to the majority of items. A corrected item-total r misleads when 2 of 5 items are reversed. In measure.py, other items are judged against the scale with a supported hypothesis applied.
- hr.py: the manager role excludes duration/count columns (YearsWithCurrManager is not a manager ID).
- sensor.py: entity count in info; empty_records judged on activity measures (steps, distance, active minutes), not calories (BMR is never zero), and reports days whose sedentary minutes fill the period.

## Added 17.11.26 (blind test 2)
- Loader: a title block above the header ("Data most recently refreshed on…") is skipped and kept in `meta.title_rows`; SPSS files are read with their user-missing codes kept as typed (`user_missing=True`), and the declared codes go into `missing_codes`.
- Dictionary draft: labels that are not a position on the scale (don't know, refusal, not applicable, hard to say — Hebrew, English, Polish, German, French), and labelled codes standing apart above a run of consecutive scale codes (1..5 then 7, 8; or 97..99), are proposed as missing codes, and min/max are computed without them.
- **date_mixed_excel_serials** (layer 3, blocking): Excel serial numbers beside written dates in one date column. A bare number is never parsed as a written date.
- **balance_hypothesis** (layer 4, decision): a + b = c in ≥70% of complete rows, both terms ≥1% of the total — an accounting identity nobody declared; the rows that break it are listed for the data's owner.
- **same_name_twice** (layer 2, decision): one name in several rows — one person counted twice, or two people with one name; decide the unit before counting.
- "Subject" alone is no longer an ID name (a school subject); subject_id, subj_3 still are.
