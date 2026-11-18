#!/usr/bin/env python3
"""Synthetic wearable daily summaries (two overlapping exports) with planted problems.
usage: make_sensor_fixture.py OUT_DIR [--seed 3]   -> exportA/*.json, exportB/*.json, manifest.json"""
import argparse
import json
import os

import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--seed", type=int, default=3)
a = ap.parse_args()
rng = np.random.default_rng(a.seed)
days = pd.date_range("2024-01-01", periods=500, freq="D")


def offset(d):
    return 3 if 3 <= d.month <= 10 else 2          # Israel-like DST


recs = []
for i, d in enumerate(days):
    rhr = int(rng.normal(58, 3))
    steps = int(max(0, rng.normal(9000, 2500)))
    off = offset(d)
    recs.append({"calendarDate": d.strftime("%Y-%m-%d"), "userProfilePK": 12345678,
                 "wellnessStartTimeGmt": (d - pd.Timedelta(hours=off)).strftime("%Y-%m-%dT%H:%M:%S.0"),
                 "wellnessStartTimeLocal": d.strftime("%Y-%m-%dT%H:%M:%S.0"),
                 "durationInMilliseconds": 86400000, "minHeartRate": rhr - int(rng.integers(3, 8)),
                 "restingHeartRate": rhr, "maxHeartRate": int(rng.normal(150, 15)), "totalSteps": steps,
                 "totalDistanceMeters": round(steps * rng.normal(0.75, 0.04)),
                 "allDayStress": {"averageStressLevel": int(rng.integers(15, 45))}})
R = pd.DataFrame(recs)
man = {}
GAP = set(range(120, 135))
idx = lambda k, lo, hi: sorted(rng.choice(np.array([i for i in range(lo, hi) if i not in GAP and not 200 <= i < 207]),
                                          k, replace=False).tolist())
# P3 sentinels
s1, s2 = idx(20, 0, 500), None
man["sentinel_days"] = s1
for j, i in enumerate(s1):
    recs[i]["allDayStress"]["averageStressLevel"] = -1 if j < 14 else -2
# P4 impossible max HR
p4 = idx(3, 0, 500); man["impossible_days"] = p4
for i in p4:
    recs[i]["maxHeartRate"] = 300
# P5 stuck resting HR for 7 days
for i in range(200, 207):
    recs[i]["restingHeartRate"] = 61
    recs[i]["minHeartRate"] = 55
man["stuck_days"] = list(range(200, 207))
# P6 travel: 5 days at UTC+9
for i in range(250, 255):
    d = days[i]
    recs[i]["wellnessStartTimeGmt"] = (d - pd.Timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S.0")
# P7 not worn
p7 = idx(8, 0, 500); man["empty_days"] = p7
for i in p7:
    recs[i].update(totalSteps=0, totalDistanceMeters=0, minHeartRate=None, restingHeartRate=None, maxHeartRate=None)
# P8 min > max
p8 = [i for i in idx(12, 0, 500) if i not in p7 + p4][:4]; man["min_gt_max_days"] = p8
for i in p8:
    recs[i]["minHeartRate"] = 190
# P9 distance in km
p9 = [i for i in idx(12, 0, 500) if i not in p7][:5]; man["km_days"] = p9
for i in p9:
    recs[i]["totalDistanceMeters"] = round(recs[i]["totalDistanceMeters"] / 1000, 2)
# P11 partial coverage
p11 = [i for i in idx(15, 0, 500) if i not in p7][:10]; man["partial_days"] = p11
for i in p11:
    recs[i]["durationInMilliseconds"] = 8 * 3600000
A = [r for i, r in enumerate(recs) if i < 400 and not (120 <= i < 135)]        # P2 gap in A (not covered by B)
B = [json.loads(json.dumps(r)) for i, r in enumerate(recs) if i >= 300]
p1 = sorted(rng.choice(np.arange(300, 400), 15, replace=False).tolist()); man["overlap_conflict_days"] = p1
for i in p1:
    B[i - 300]["totalSteps"] += 1234                                           # P1 revised values in B
man["gap_days"] = list(range(120, 135))
man["overlap_days"] = [300, 399]
for name, rows in (("exportA", A), ("exportB", B)):
    os.makedirs(os.path.join(a.out, name), exist_ok=True)
    for k in range(0, len(rows), 100):
        json.dump(rows[k:k + 100], open(os.path.join(a.out, name, f"UDSFile_{k:04d}.json"), "w"))
json.dump(man, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
print("ok")
