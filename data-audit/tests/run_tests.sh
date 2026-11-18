#!/usr/bin/env bash
# Self-test of data-audit on synthetic data only (no real research data is read).
#   bash data-audit/tests/run_tests.sh        (about one to two minutes)
# Exits non-zero if any gate fails.
set -u
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; SC="$(cd "$T/../scripts" && pwd)"; EX="$(cd "$T/../../examples" && pwd)"
O="$(mktemp -d)"; fail=0; export DA_SC="$SC"

echo "== A. versioning and verification (layers A-D), including four sabotage attempts"
python3 -W ignore "$SC/profile_dict.py" import "$EX/survey_research_2026_dictionary.xlsx" --out "$O/dict.xlsx" >/dev/null 2>&1
(cd "$T" && python3 -W ignore test_verify.py "$EX/survey_research_2026_raw.csv" "$O/dict.xlsx" 2>&1 | tail -1) | tee "$O/ga"
grep -q "ALL TESTS OK" "$O/ga" || fail=1

echo "== B. measurement and response quality (layers 5-7), four seeds"
for seed in 7 11 23 42; do M="$O/measure_$seed"; python3 -W ignore "$T/make_measure_fixture.py" "$M" --seed $seed >/dev/null
  python3 -W ignore "$SC/measure.py" "$M/t1.csv" --dict "$M/dict.xlsx" --label t1 --id id --t2 "$M/t2.csv" --t2-label t2 --out "$M/out" >/dev/null 2>&1
  python3 "$T/score_measure.py" "$M/out/measure.json" "$M/manifest.json" | tail -1 | sed "s/^/seed $seed: /" | tee -a "$O/gb"; done
grep -q "PASS" "$O/gb" && ! grep -q "FAIL" "$O/gb" || fail=1

echo "== C. sensors: overlapping exports, four seeds"
for seed in 3 5 9 13; do M="$O/sensor_$seed"; python3 "$T/make_sensor_fixture.py" "$M" --seed $seed >/dev/null
  python3 -W ignore "$SC/sensor.py" "$M/exportA" "$M/exportB" --glob "UDSFile*" --out "$M/out" >/dev/null 2>&1
  python3 "$T/score_sensor.py" "$M/out/sensor.json" "$M/manifest.json" | tail -1 | sed "s/^/seed $seed: /" | tee -a "$O/gc"; done
grep -q "PASS" "$O/gc" && ! grep -q "FAIL" "$O/gc" || fail=1

echo "== D. organisational (HR) data, four seeds"
for seed in 2 4 8 15; do M="$O/hr_$seed"; python3 "$T/make_hr_fixture.py" "$M" --seed $seed >/dev/null
  python3 -W ignore "$SC/hr.py" "$M/hr.csv" --out "$M/out" --asof 2026-11-17 >/dev/null 2>&1
  python3 "$T/score_hr.py" "$M/out/hr.json" "$M/manifest.json" | tail -1 | sed "s/^/seed $seed: /" | tee -a "$O/gd"; done
grep -q "PASS" "$O/gd" && ! grep -q "FAIL" "$O/gd" || fail=1

echo "== E. contract with downstream analysis (reverse state, nflow.json, decision status)"
python3 -W ignore "$T/test_contract.py" "$O/contract" 2>&1 | tail -1 | tee "$O/ge"
grep -q "CONTRACT GATE: PASS" "$O/ge" || fail=1

echo; [ $fail = 0 ] && echo "ALL SELF-TESTS PASS" || echo "SOME SELF-TEST FAILED (outputs in $O)"
exit $fail
