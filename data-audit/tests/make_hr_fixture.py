#!/usr/bin/env python3
"""Synthetic HR file (Hebrew headers) with planted problems. Names and numbers are invented.
usage: make_hr_fixture.py OUT_DIR [--seed 2]  -> hr.csv, manifest.json"""
import argparse
import json
import os

import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--seed", type=int, default=2)
a = ap.parse_args()
rng = np.random.default_rng(a.seed)
N, ASOF = 400, pd.Timestamp("2026-11-17")
ids = [str(x) for x in rng.choice(np.arange(10000, 99999), N, replace=False)]
grades = rng.choice(["1", "2", "3", "4"], N, p=[.4, .3, .2, .1])
base = {"1": 9000, "2": 13000, "3": 18000, "4": 26000}
birth = [ASOF - pd.Timedelta(days=int(rng.uniform(22, 60) * 365.25)) for _ in range(N)]
hire = [b + pd.Timedelta(days=int(rng.uniform(21, max(22, (ASOF - b).days / 365.25 - 0.5)) * 365.25)) for b in birth]
hire = [min(h, ASOF - pd.Timedelta(days=30)) for h in hire]
exit_ = [""] * N
for i in rng.choice(N, 60, replace=False):
    exit_[i] = (hire[i] + pd.Timedelta(days=int(rng.uniform(60, max(61, (ASOF - hire[i]).days))))).strftime("%d/%m/%Y")
mgr = [""] + [ids[int(rng.integers(0, max(1, i // 4 + 1)))] for i in range(1, N)]
df = pd.DataFrame({
    "מספר_עובד": ids, "תאריך_לידה": [b.strftime("%d/%m/%Y") for b in birth],
    "תחילת_עבודה": [h.strftime("%d/%m/%Y") for h in hire], "סיום_עבודה": exit_, "מנהל_ישיר": mgr,
    "גיל": [int((ASOF - b).days / 365.25) for b in birth], "ותק": [0.0] * N, "דרגה": grades,
    "שכר": [round(base[g] * rng.normal(1, .08)) for g in grades], "מחלקה": rng.choice(["תפעול", "כספים", "שיווק"], N),
    "הערות": [""] * N})
for i in range(N):
    end = pd.to_datetime(df.at[i, "סיום_עבודה"], dayfirst=True) if df.at[i, "סיום_עבודה"] else ASOF
    df.at[i, "ותק"] = round((end - hire[i]).days / 365.25, 1)
for i in range(1, N):                     # no manager may be their own (planted below)
    if df.at[i, "מנהל_ישיר"] == ids[i]:
        df.at[i, "מנהל_ישיר"] = ids[0]
pick = iter(rng.choice(np.arange(5, N), 80, replace=False).tolist())
take = lambda k: [next(pick) for _ in range(k)]
m = {}
m["exit_before_hire"] = take(3)
for i in m["exit_before_hire"]:
    df.at[i, "סיום_עבודה"] = (hire[i] - pd.Timedelta(days=200)).strftime("%d/%m/%Y")
    df.at[i, "ותק"] = round((ASOF - hire[i]).days / 365.25, 1)
m["hire_age"] = take(3)                    # day/month or year typo: hired at age ~5
for i in m["hire_age"]:
    h = birth[i] + pd.Timedelta(days=5 * 365)
    df.at[i, "תחילת_עבודה"] = h.strftime("%d/%m/%Y")
m["age_mismatch"] = take(4)
for i in m["age_mismatch"]:
    df.at[i, "גיל"] = int(df.at[i, "גיל"]) + 7
m["tenure_mismatch"] = take(4)
for i in m["tenure_mismatch"]:
    df.at[i, "ותק"] = round(float(df.at[i, "ותק"]) + 6, 1)
m["self_manager"] = take(2)
for i in m["self_manager"]:
    df.at[i, "מנהל_ישיר"] = ids[i]
m["orphan_manager"] = take(3)
for i in m["orphan_manager"]:
    df.at[i, "מנהל_ישיר"] = "77777"
c = take(3)                                # a reporting loop a -> b -> c -> a
df.at[c[0], "מנהל_ישיר"], df.at[c[1], "מנהל_ישיר"], df.at[c[2], "מנהל_ישיר"] = ids[c[1]], ids[c[2]], ids[c[0]]
m["cycle"] = c
m["pay_units"] = take(4)                   # yearly instead of monthly
for i in m["pay_units"]:
    df.at[i, "שכר"] = int(df.at[i, "שכר"]) * 12
m["bad_date"] = take(2)
for i in m["bad_date"]:
    df.at[i, "תחילת_עבודה"] = "31/02/2019"
m["pii_text"] = take(5)
notes = ["לתאם עם רס\"ן אבירם לגבי הקידום", "טלפון 052-1234567", "ת.ז. 000000018 צריך לעדכן", "שיחה עם המנהלת נועה כהן",
         "noa@example.com לשלוח מסמכים"]
for i, t in zip(m["pii_text"], notes):
    df.at[i, "הערות"] = t
clean_notes = take(5)
for i in clean_notes:
    df.at[i, "הערות"] = "עובד מצטיין, להמשיך מעקב"
df["סטטוס"] = np.where(df["סיום_עבודה"] == "", "פעיל", "עזב")
m["stale_active"] = [i for i in take(6) if df.at[i, "סיום_עבודה"] == ""][:0]   # placeholder, filled below
cands = [i for i in range(5, N) if df.at[i, "סיום_עבודה"] and i not in sum(m.values(), [])][:3]
for i in cands:
    df.at[i, "סטטוס"] = "פעיל"
m["stale_active"] = cands
ov = [i for i in range(5, N) if not df.at[i, "סיום_עבודה"] and i not in sum(m.values(), []) and i not in clean_notes][:3]
extra = df.loc[ov].copy()
for i in ov:                                   # a second assignment that opens before the first one closed
    extra.at[i, "תחילת_עבודה"] = (hire[i] + pd.Timedelta(days=400)).strftime("%d/%m/%Y")
    extra.at[i, "ותק"] = round((ASOF - hire[i] - pd.Timedelta(days=400)).days / 365.25, 1)
df = pd.concat([df, extra], ignore_index=True)
m["overlap"] = ov + list(range(N, N + len(ov)))
os.makedirs(a.out, exist_ok=True)
df.to_csv(os.path.join(a.out, "hr.csv"), index=False, encoding="utf-8-sig")
json.dump({k: [int(x) + 1 for x in v] for k, v in m.items()} | {"asof": str(ASOF.date())},
          open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
print("ok")
