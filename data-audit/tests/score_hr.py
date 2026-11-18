#!/usr/bin/env python3
"""usage: score_hr.py hr.json manifest.json"""
import json
import sys

R = json.load(open(sys.argv[1], encoding="utf-8"))
M = json.load(open(sys.argv[2], encoding="utf-8"))


def rows(*checks):
    return {r for f in R["findings"] if f["check"] in checks for r in f["rows"]}


plan = [("exit_before_hire", ("exit_before_hire",)), ("hire_age", ("implausible_hire_age",)),
        ("age_mismatch", ("age_disagrees_with_birth",)), ("tenure_mismatch", ("tenure_disagrees_with_dates",)),
        ("self_manager", ("self_manager",)), ("orphan_manager", ("manager_not_employee",)),
        ("cycle", ("hierarchy_cycle",)), ("pay_units", ("pay_outside_grade",)), ("bad_date", ("bad_date",)),
        ("pii_text", ("pii_in_text",)), ("stale_active", ("stale_active_status",)),
        ("overlap", ("overlapping_records",))]
ok, planted = True, set()
for key, checks in plan:
    want = set(M[key])
    planted |= want
    got = rows(*checks)
    good = want <= got
    ok &= good
    print(f"{'FOUND ' if good else 'missed'} {key}: {len(want & got)}/{len(want)}")
# false alarms: flagged rows that carry no planted problem (tenure follows hire-date changes, so the
# rows of hire_age / bad_date / exit_before_hire may legitimately show up in tenure checks too)
fa = rows(*{c for _, cs in plan for c in cs}) - planted
print(f"{'ok    ' if len(fa) <= 2 else 'FALSE '} rows flagged without a planted problem: {len(fa)} {sorted(fa)[:10]}")
ok &= len(fa) <= 2
print("HR GATE:", "PASS" if ok else "FAIL")
