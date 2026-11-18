#!/usr/bin/env python3
"""Verification layer D: golden numbers - values known to be right from outside the pipeline
(the N the survey platform reports, linkage counts from the project log, a published mean).

  golden.py WORK goldens.json

goldens.json: a list of checks, each
  {"name": "N after cleaning", "kind": "rows", "version": "last", "expected": 154}
  {"name": "mean age", "kind": "mean", "var": "age", "expected": 31.2, "tol": 0.05}
  {"name": "women", "kind": "count_value", "var": "gender", "value": "2", "expected": 88}
  {"name": "unique IDs", "kind": "n_unique", "var": "id", "expected": 150}
  {"name": "missing tenure", "kind": "n_missing", "var": "tenure", "expected": 2}
  "version" is "last" (default), "first" or "vNN"; "source" says where the expected value comes from.
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import as_number, write_json  # noqa: E402


def main():
    work, gpath = sys.argv[1], sys.argv[2]
    m = json.load(open(os.path.join(work, "manifest.json"), encoding="utf-8"))
    vs = {v["version"]: v for v in m["versions"]}
    order = [v["version"] for v in m["versions"]]
    res = []
    for g in json.load(open(gpath, encoding="utf-8")):
        ver = {"last": order[-1], "first": order[0]}.get(g.get("version", "last"), g.get("version"))
        df = pd.read_csv(os.path.join(work, vs[ver]["file"]), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        k, var = g["kind"], g.get("var")
        if k == "rows":
            got = len(df)
        elif k == "mean":
            got = float(as_number(df[var]).mean())
        elif k == "count_value":
            got = int((df[var].str.strip() == str(g["value"])).sum())
        elif k == "n_unique":
            s = df[var].str.strip()
            got = int(s[s != ""].nunique())
        elif k == "n_missing":
            got = int((df[var].str.strip() == "").sum())
        else:
            raise ValueError(f"unknown kind {k}")
        tol = float(g.get("tol", 0))
        ok = abs(float(got) - float(g["expected"])) <= tol
        res.append({**g, "version": ver, "got": got, "passed": ok})
        print(f"{'ok  ' if ok else 'FAIL'} {g['name']}: expected {g['expected']}, got {round(got, 4) if isinstance(got, float) else got} ({ver})")
    write_json(os.path.join(work, "golden.json"), res)
    passed = all(r["passed"] for r in res)
    print("GOLDEN:", "PASS" if passed else "FAIL")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
