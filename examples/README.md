# Example data

`survey_research_2026_raw.csv` is a **synthetic** organisational survey (no real respondents), generated
for the data-analysis workshop for HR students at Sapir College. It contains deliberately planted
problems of the kinds data-audit looks for: test and empty rows, duplicates, label variants, missing-value
codes, out-of-range values, careless responders and more. `survey_research_2026_dictionary.xlsx` is its
data dictionary.

Practise by giving both files to Claude with the data-audit skill installed and asking it to audit the
file. The answer key is deliberately not published, so the file can still serve as an exercise.

The self-tests (`data-audit/tests/run_tests.sh`) also use these two files.
