#!/usr/bin/env python3
"""usage: score_sensor.py sensor.json manifest.json"""
import json
import sys

R = json.load(open(sys.argv[1], encoding="utf-8"))
M = json.load(open(sys.argv[2], encoding="utf-8"))
by = {}
for f in R["findings"]:
    by.setdefault(f["check"], []).append(f)


def n(check, var=None):
    return sum(f["count"] for f in by.get(check, []) if var is None or var in f["var"])


tests = [
    ("P1 overlapping exports with revised values (15 days)", n("duplicate_records_conflicting") == len(M["overlap_conflict_days"])),
    ("P1b identical overlap reported as duplicates (85 days)", n("duplicate_records_identical") == 100 - len(M["overlap_conflict_days"])),
    ("P2 15-day gap", n("gaps") >= 1),
    ("P3 sentinel stress codes (20)", n("sentinel_values", "averageStressLevel") == len(M["sentinel_days"])),
    ("P4 max HR 300 (3)", n("impossible_values", "maxHeartRate") == len(M["impossible_days"])),
    ("P5 stuck resting HR (7 days)", n("stuck_values", "restingHeartRate") >= 7),
    ("P6 travel time zone", n("timezone_changes") >= 1),
    ("P7 days not worn (8)", n("empty_records") == len(M["empty_days"])),
    ("P8 min HR above max (4)", n("logic_heart_rate") == len(M["min_gt_max_days"])),
    ("P9 distance in km (5)", n("logic_stride") == len(M["km_days"])),
    ("P10 identifier carried in every record", n("identifier_fields") >= 1),
    ("P11 partial days (10)", n("partial_coverage") == len(M["partial_days"])),
]
expected = {"duplicate_records_conflicting", "duplicate_records_identical", "gaps", "sentinel_values", "impossible_values",
            "stuck_values", "timezone_changes", "empty_records", "logic_heart_rate", "logic_stride", "identifier_fields",
            "partial_coverage"}
extra = sorted(set(by) - expected)
ok = True
for name, good in tests:
    ok &= good
    print(f"{'FOUND ' if good else 'missed'} {name}")
print(f"{'ok    ' if not extra else 'FALSE '} unexpected checks: {extra}")
ok &= not extra
print("SENSOR GATE:", "PASS" if ok else "FAIL")
