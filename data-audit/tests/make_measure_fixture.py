#!/usr/bin/env python3
"""Synthetic two-wave survey with planted measurement problems (layers 5-7), and its manifest.

usage: make_measure_fixture.py OUT_DIR [--seed 7]
Writes t1.csv, t2.csv, dict.xlsx, manifest.json. Fully synthetic: no real person behind any row.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from da_common import write_dictionary  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()
rng = np.random.default_rng(a.seed)
os.makedirs(a.out, exist_ok=True)

N, N2_NEW = 300, 20
SCALES = {"A": (5, 0.75), "B": (5, 0.75), "C": (5, 0.75), "D": (5, 0.75), "E": (5, 0.12)}   # top, loading
K = 6


def items(latent, top, load, n):
    """Skewed toward agreement, as real Likert answers usually are."""
    raw = load * latent[:, None] + np.sqrt(1 - load ** 2) * rng.standard_normal((n, K)) + 0.6
    cuts = np.linspace(-1.2, 1.4, top - 1)
    return np.digitize(raw, cuts) + 1


lat = {s: rng.standard_normal(N) for s in SCALES}
cols, data = [], {}
for s, (top, load) in SCALES.items():
    X = items(lat[s], top, load, N)
    for j in range(K):
        data[f"{s}{j + 1}"] = X[:, j]
df = pd.DataFrame(data)
# P1: B3 is reverse-keyed in the questionnaire, the dictionary does not say so
df["B3"] = 6 - df["B3"]
# P2: C2 is marked reverse in the dictionary, but the export already reversed it (positive with the scale)
# (nothing to do: C2 is generated positively keyed)
# P3: D6 sits on 1-7 while D1-D5 are 1-5
df["D6"] = np.clip(np.round((df["D6"] - 1) * 1.5 + 1 + rng.integers(0, 2, N)), 1, 7).astype(int)
ids = rng.choice(np.arange(1000000, 9999999), N + N2_NEW, replace=False).astype(str)
df.insert(0, "id", ids[:N])
df.insert(1, "gender", rng.choice(["1", "2"], N))
nitems = len(SCALES) * K
df.insert(2, "duration", (rng.uniform(5, 15, N) * nitems).round())
# P5: 12 random responders, fast
rand_rows = rng.choice(np.arange(N), 12, replace=False)
for s, (top, _) in SCALES.items():
    for j in range(K):
        c = f"{s}{j + 1}"
        t = 7 if c == "D6" else top
        df.loc[rand_rows, c] = rng.integers(1, t + 1, len(rand_rows))
df.loc[rand_rows, "duration"] = (rng.uniform(0.6, 1.5, len(rand_rows)) * nitems).round()
# P6: 8 straight-liners (all 4s), fast
rest = np.setdiff1d(np.arange(N), rand_rows)
sl_rows = rng.choice(rest, 8, replace=False)
for c in [f"{s}{j + 1}" for s in SCALES for j in range(K)]:
    df.loc[sl_rows, c] = 4
df.loc[sl_rows, "duration"] = (rng.uniform(0.5, 1.2, len(sl_rows)) * nitems).round()
df.to_csv(os.path.join(a.out, "t1.csv"), index=False, encoding="utf-8-sig")

# wave 2: 280 of the 300 return, 20 new; latent stable with noise
ret = np.setdiff1d(np.arange(N), np.concatenate([rand_rows, sl_rows]))[:280]
lat2 = {s: 0.8 * lat[s][ret] + 0.6 * rng.standard_normal(len(ret)) for s in SCALES}
# P8: 5 IDs whose wave-2 person is someone else entirely (extreme change on every scale)
swap = rng.choice(np.arange(len(ret)), 5, replace=False)
for s in SCALES:
    lat2[s][swap] = -np.sign(lat[s][ret][swap]) * 2.8
d2 = {}
for s, (top, load) in SCALES.items():
    X = items(lat2[s], top, load, len(ret))
    for j in range(K):
        d2[f"{s}{j + 1}"] = X[:, j]
t2 = pd.DataFrame(d2)
t2["B3"] = 6 - t2["B3"]
t2["D6"] = np.clip(np.round((t2["D6"] - 1) * 1.5 + 1 + rng.integers(0, 2, len(ret))), 1, 7).astype(int)
t2.insert(0, "id", df.loc[ret, "id"].values)
t2.insert(1, "gender", df.loc[ret, "gender"].values)
# P7: 6 gender values flipped between waves
flip = rng.choice(np.setdiff1d(np.arange(len(ret)), swap), 6, replace=False)
t2.loc[flip, "gender"] = np.where(t2.loc[flip, "gender"] == "1", "2", "1")
t2.insert(2, "duration", (rng.uniform(5, 15, len(ret)) * nitems).round())
new = pd.DataFrame({"id": ids[N:], "gender": rng.choice(["1", "2"], N2_NEW),
                    "duration": (rng.uniform(5, 15, N2_NEW) * nitems).round()})
for c in d2:
    new[c] = rng.integers(1, 6, N2_NEW)
t2 = pd.concat([t2, new], ignore_index=True)
t2.to_csv(os.path.join(a.out, "t2.csv"), index=False, encoding="utf-8-sig")

rows = [{"var": "id", "type": "id", "status": "confirmed"},
        {"var": "gender", "type": "categorical", "allowed": "1=גבר; 2=אישה", "status": "confirmed"},
        {"var": "duration", "type": "numeric", "unit": "seconds", "status": "confirmed"}]
for s, (top, _) in SCALES.items():
    for j in range(K):
        v = f"{s}{j + 1}"
        rows.append({"var": v, "type": "likert", "min": 1, "max": 7 if v == "D6" else top, "scale": s,
                     "reverse": "yes" if v == "C2" else "no", "status": "confirmed"})
write_dictionary(os.path.join(a.out, "dict.xlsx"), pd.DataFrame(rows))
man = {
    "P1_reverse_unmarked": "B3", "P2_reverse_already_applied": "C2", "P3_mixed_ranges": "D",
    "P4_low_reliability": "E",
    "P5_random_fast_rows": [int(r) + 1 for r in rand_rows], "P6_straightline_fast_rows": [int(r) + 1 for r in sl_rows],
    "P7_gender_flip_t2_rows": [int(r) + 1 for r in flip], "P8_swapped_t2_rows": [int(r) + 1 for r in swap],
    "n_t1_only": N - len(ret), "note": "row numbers are _row (1-based data rows)",
}
json.dump(man, open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8"), indent=1)
print("ok")
