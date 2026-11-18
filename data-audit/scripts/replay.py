#!/usr/bin/env python3
"""Verification layers C and F.

C - independent recomputation: rebuild every version from the previous one using ONLY the log
    (a second, deliberately simple implementation) and compare cell by cell with the saved version.
    Layer A proves every change was declared; C proves the declared change is the change that was made.
F - sampling: a trace sheet of N random respondents, raw value -> every version, with the log entries
    that touched them, for a person to check by hand.

  replay.py WORK [--strict] [--trace 15] [--seed 1] [--lang he|en]

Log entries are replayable when they carry their payload:
  recode: "map": {"old": "new", ...}     set_value: "value"     reverse: "min", "max"
  set_missing / remove_rows / drop_column / rename_column ("from","to"): no payload needed
  add_column: "values" ({_row: value}) or it is reported as not replayable
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import ROWKEY, to_str, write_json  # noqa: E402


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _load(work, rec):
    df = pd.read_csv(os.path.join(work, rec["file"]), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df[ROWKEY] = df[ROWKEY].astype(int)
    return df.set_index(ROWKEY, drop=False)


def apply_entry(Q: pd.DataFrame, e: dict, actual: pd.DataFrame, unrep: list) -> pd.DataFrame:
    rows = e.get("rows", [])
    sel = Q.index if rows == "all" else [r for r in map(int, rows) if r in Q.index]
    a = e["action"]
    if a == "remove_rows":
        return Q.drop(index=[r for r in sel if r in Q.index])
    if a == "drop_column":
        return Q.drop(columns=[v for v in e.get("vars", []) if v in Q.columns])
    if a == "rename_column":
        return Q.rename(columns={e["from"]: e["to"]})
    if a == "add_column":
        for v in e.get("vars", []):
            if "values" in e:
                Q[v] = pd.Series({int(k): to_str(x) for k, x in e["values"].items()}).reindex(Q.index).fillna("")
            else:
                Q[v] = actual[v].reindex(Q.index).fillna("") if v in actual.columns else ""
                unrep.append(f"{e.get('step', '?')}: add_column {v} has no 'values'")
        return Q
    for v in e.get("vars", []):
        if v not in Q.columns:
            continue
        if a == "set_missing":
            Q.loc[sel, v] = ""
        elif a == "set_value":
            if "value" not in e:
                unrep.append(f"{e.get('step', '?')}: set_value {v} has no 'value'")
                Q.loc[sel, v] = actual.loc[[r for r in sel if r in actual.index], v]
            else:
                Q.loc[sel, v] = to_str(e["value"])
        elif a == "recode":
            if "map" not in e:
                unrep.append(f"{e.get('step', '?')}: recode {v} has no 'map'")
                Q.loc[sel, v] = actual.loc[[r for r in sel if r in actual.index], v]
            else:
                m = {str(k): to_str(x) for k, x in e["map"].items()}
                Q.loc[sel, v] = Q.loc[sel, v].map(lambda x: m.get(x, x))
        elif a == "reverse":
            lo, hi = _num(e.get("min")), _num(e.get("max"))
            if lo is None or hi is None:
                unrep.append(f"{e.get('step', '?')}: reverse {v} has no 'min'/'max'")
                Q.loc[sel, v] = actual.loc[[r for r in sel if r in actual.index], v]
            else:
                Q.loc[sel, v] = Q.loc[sel, v].map(lambda x: to_str(lo + hi - _num(x)) if _num(x) is not None else x)
    return Q


def replay(work: str, strict: bool = False) -> dict:
    m = json.load(open(os.path.join(work, "manifest.json"), encoding="utf-8"))
    log = [json.loads(x) for x in open(os.path.join(work, "log.jsonl"), encoding="utf-8") if x.strip()]
    vs = m["versions"]
    out = []
    for p, c in zip(vs, vs[1:]):
        P, C = _load(work, p), _load(work, c)
        E = [e for e in log if e.get("version_from") == p["version"] and e.get("version_to") == c["version"]]
        unrep = []
        Q = P.copy()
        for e in E:
            Q = apply_entry(Q, e, C, unrep)
        mism = []
        if set(Q.index) != set(C.index):
            mism.append(f"rows differ: replay-only {sorted(set(Q.index) - set(C.index))[:10]}, "
                        f"saved-only {sorted(set(C.index) - set(Q.index))[:10]}")
        if set(Q.columns) != set(C.columns):
            mism.append(f"columns differ: {sorted(set(Q.columns) ^ set(C.columns))[:10]}")
        rows = sorted(set(Q.index) & set(C.index))
        cols = [x for x in C.columns if x in Q.columns and x != ROWKEY]
        diff = Q.loc[rows, cols].astype(str) != C.loc[rows, cols].astype(str)
        cells = [(int(r), col, Q.at[r, col], C.at[r, col]) for col in cols for r in diff.index[diff[col]]]
        if cells:
            mism.append(f"{len(cells)} cells differ between the replay of the log and the saved version, e.g. "
                        + "; ".join(f"row {r} {col}: log gives '{q}', file has '{s}'" for r, col, q, s in cells[:6]))
        ok = not mism and (not strict or not unrep)
        out.append({"from": p["version"], "to": c["version"], "passed": ok, "mismatches": mism,
                    "not_replayable": unrep})
    return {"passed": all(x["passed"] for x in out), "strict": strict, "transitions": out}


def trace(work: str, n: int, seed: int, lang: str) -> str:
    he = lang == "he"
    m = json.load(open(os.path.join(work, "manifest.json"), encoding="utf-8"))
    log = [json.loads(x) for x in open(os.path.join(work, "log.jsonl"), encoding="utf-8") if x.strip()]
    V = [(v["version"], _load(work, v)) for v in m["versions"]]
    first, last = V[0][1], V[-1][1]
    changed = sorted({col for _, D in V[1:] for col in D.columns
                      if col in first.columns and col != ROWKEY
                      and (D[col] != first.loc[D.index, col]).any()} | {c for c in last.columns if c not in first.columns})
    removed = sorted(set(first.index) - set(last.index))
    random.seed(seed)
    pick = sorted(random.sample(sorted(last.index), min(n, len(last))) + random.sample(removed, min(3, len(removed))))
    L = ["# " + ("דף מעקב לבדיקה ידנית (שכבה F)" if he else "Manual trace sheet (layer F)"), "",
         (f"נבחרו באקראי {len(pick)} משיבים (seed={seed}), כולל {min(3, len(removed))} שהוסרו. לכל אחד: הערך בקובץ הגולמי, "
          "בכל גרסה, ורשומות היומן שנגעו בו. יש לסמן ✓ אחרי השוואה לקובץ הגולמי עצמו." if he else
          f"{len(pick)} respondents drawn at random (seed={seed}), including {min(3, len(removed))} removed ones. "
          "Raw value, every version, and the log entries that touched it. Tick ✓ after comparing with the raw file itself."), ""]
    for r in pick:
        ents = [e for e in log if e.get("rows") == "all" or r in [int(x) for x in e.get("rows", [])]]
        L.append(f"## _row {r}")
        if r in removed:
            why = [e for e in ents if e["action"] == "remove_rows"]
            L.append(("הוסר: " if he else "Removed: ") + "; ".join(f"{e['version_to']} ({e.get('rationale', '')})" for e in why))
        L.append("| " + " | ".join(["משתנה" if he else "Variable"] + [v for v, _ in V] + ["✓"]) + " |")
        L.append("|" + "---|" * (len(V) + 2))
        for col in changed:
            vals = [to_str(D.at[r, col]) if (r in D.index and col in D.columns) else "—" for _, D in V]
            if len(set(vals)) > 1:
                L.append(f"| {col} | " + " | ".join(vals) + " | ☐ |")
        for e in ents:
            if e["action"] != "remove_rows":
                L.append(f"- {e['version_to']} `{e['action']}` {e.get('vars', '')}: {e.get('rationale', '')}")
        L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--trace", type=int, default=0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    a = ap.parse_args()
    res = replay(a.work, a.strict)
    write_json(os.path.join(a.work, "replay.json"), res)
    for t in res["transitions"]:
        print(f"{t['from']} -> {t['to']}: {'passed' if t['passed'] else 'FAILED'}"
              + (f" | {t['mismatches']}" if t["mismatches"] else "")
              + (f" | not replayable: {t['not_replayable']}" if t["not_replayable"] else ""))
    if a.trace:
        p = os.path.join(a.work, f"trace_{a.lang}.md")
        open(p, "w", encoding="utf-8").write(trace(a.work, a.trace, a.seed, a.lang))
        print(p)
    print("REPLAY:", "PASS" if res["passed"] else "FAIL")
    sys.exit(0 if res["passed"] else 1)


if __name__ == "__main__":
    main()
