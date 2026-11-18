# Example data

`survey_research_2026_raw.csv` is a **synthetic** organisational survey (no real respondents), generated
for a data-analysis workshop for HR students. It contains deliberately planted
problems of the kinds data-audit looks for: test and empty rows, duplicates, label variants, missing-value
codes, out-of-range values, careless responders and more. `survey_research_2026_dictionary.xlsx` is its
data dictionary.

**About the questionnaire items.** The item wordings in the dictionary were written anew for this example.
They measure the same aspects as the cited instruments — interdepartmental connectedness (Jaworski &
Kohli, 1993) and identification with the organisation: commitment, importance, superiority and deference
(Roccas et al., 2008) — but they are not the original items, which are deliberately not reproduced here.
Two connectedness items (6 and 7) are worded negatively on purpose, so the exercise still contains reverse
items. Because the data are synthetic, these wordings have no tested validity or reliability; researchers
who want to use the instruments should take them from the original publications. The dictionary's sheet
"על נוסח הפריטים" records the same note in Hebrew.

Practise by giving both files to Claude with the data-audit skill installed and asking it to audit the
file. The answer key is deliberately not published, so the file can still serve as an exercise.

The self-tests (`data-audit/tests/run_tests.sh`) also use these two files.
