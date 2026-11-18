"""Numbered versions of the data, a manifest, and the decision log.

CLI:
  python versioning.py init RAW_FILE --work WORKDIR [--dict dict.xlsx] [--label NAME] [--sheet S]
      -> WORKDIR/v01.csv (raw values + _row), manifest.json, log.jsonl (empty)

Python (inside the analysis code the AI writes, one change per call):
  from versioning import load_version, save_version
  df = load_version(WORK)                       # latest version, all values as text
  df = df[~df["_row"].isin([1, 109])]
  save_version(df, WORK, [{"step": "2.1", "action": "remove_rows", "rows": [1, 109],
                           "finding": "F001", "rationale": "...", "source": "...",
                           "decided_by": "researcher", "tag": "method"}])
      -> writes the next vNN.csv, appends the log, then runs verification layers A-B.
         A failed verification raises; the version stays on disk marked failed so the
         researcher can see what happened, and nothing downstream may use it.

Log actions: remove_rows (rows) · recode / set_missing / set_value / reverse (vars, rows or "all")
            add_column (vars) · drop_column (vars) · rename_column (from, to)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import ROWKEY, load_table, sha256_file, to_str, write_json  # noqa: E402

ACTIONS = {"remove_rows", "recode", "set_missing", "set_value", "reverse",
           "add_column", "drop_column", "rename_column"}


def _manifest(work):
    return json.load(open(os.path.join(work, "manifest.json"), encoding="utf-8"))


def init(raw, work, dict_path=None, label=None, sheet=None):
    os.makedirs(work, exist_ok=True)
    L = load_table(raw, sheet)
    path = os.path.join(work, "v01.csv")
    L.df.to_csv(path, index=False, encoding="utf-8-sig")
    m = {"label": label or os.path.basename(raw), "raw_path": os.path.abspath(raw),
         "raw_sha256": sha256_file(raw), "dictionary": os.path.abspath(dict_path) if dict_path else "",
         "load_meta": {k: v for k, v in L.meta.items() if k not in ("question_text", "column_labels", "value_labels")},
         "versions": [{"version": "v01", "file": "v01.csv", "sha256": sha256_file(path), "rows": len(L.df),
                       "cols": len(L.df.columns) - 1, "parent": None, "created": datetime.now().isoformat(timespec="seconds"),
                       "note": "raw values as read; _row = data-row number in the source", "verified": True}]}
    write_json(os.path.join(work, "manifest.json"), m)
    open(os.path.join(work, "log.jsonl"), "a", encoding="utf-8").close()
    print(f"v01: {len(L.df)} rows x {len(L.df.columns) - 1} cols -> {path}")
    return m


def work_of(path):
    """(work folder, version) when `path` is a version file listed in a work folder's manifest, else (None, None)."""
    d = os.path.dirname(os.path.abspath(path))
    mp = os.path.join(d, "manifest.json")
    if not os.path.exists(mp):
        return None, None
    try:
        m = json.load(open(mp, encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(m, dict) or not isinstance(m.get("versions"), list):   # another file's manifest (a test key)
        return None, None
    rec = next((x for x in m["versions"] if isinstance(x, dict) and x.get("file") == os.path.basename(path)), None)
    return (d, rec["version"]) if rec else (None, None)


def applied_reversals(work, version=None) -> dict:
    """Columns reversed in the data by the log, up to and including `version` (default: latest).

    Returns {column: {"full": n, "partial": n}}. The dictionary's `reverse` describes the instrument;
    the log says what was already done to the file. A consumer that scores items must reverse an item
    only if the instrument says so AND the file has not already been reversed (odd count = reversed).
    """
    m = _manifest(work)
    order = [x["version"] for x in m["versions"]]
    upto = order.index(version or order[-1])
    keep = set(order[:upto + 1])
    out = {}
    lp = os.path.join(work, "log.jsonl")
    if not os.path.exists(lp):
        return out
    for line in open(lp, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e.get("action") != "reverse" or e.get("version_to") not in keep:
            continue
        vs = e.get("vars") or []
        vs = [vs] if isinstance(vs, str) else vs
        kind = "full" if e.get("rows") in (None, "all") else "partial"
        for v in vs:
            out.setdefault(v, {"full": 0, "partial": 0})[kind] += 1
    return out


def load_version(work, version=None) -> pd.DataFrame:
    m = _manifest(work)
    v = version or m["versions"][-1]["version"]
    rec = next(x for x in m["versions"] if x["version"] == v)
    df = pd.read_csv(os.path.join(work, rec["file"]), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df[ROWKEY] = df[ROWKEY].astype(int)
    return df


def save_version(df: pd.DataFrame, work: str, entries: list[dict], note: str = "", lang: str = "he"):
    if ROWKEY not in df.columns:
        raise ValueError("_row was dropped: the stable row key must travel with every version")
    for e in entries:
        if e.get("action") not in ACTIONS:
            raise ValueError(f"unknown action {e.get('action')!r}; use one of {sorted(ACTIONS)}")
    m = _manifest(work)
    parent = m["versions"][-1]["version"]
    nv = f"v{int(parent[1:]) + 1:02d}"
    out = df.copy()
    for c in out.columns:
        if c != ROWKEY:
            out[c] = out[c].map(to_str)
    path = os.path.join(work, f"{nv}.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    stamp = datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(work, "log.jsonl"), "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps({**e, "version_from": parent, "version_to": nv, "time": stamp},
                               ensure_ascii=False) + "\n")
    m["versions"].append({"version": nv, "file": f"{nv}.csv", "sha256": sha256_file(path), "rows": len(out),
                          "cols": len(out.columns) - 1, "parent": parent, "created": stamp,
                          "note": note, "verified": None})
    write_json(os.path.join(work, "manifest.json"), m)
    from verify import verify_transition
    res = verify_transition(work, parent, nv, lang=lang)
    m = _manifest(work)
    m["versions"][-1]["verified"] = res["passed"]
    write_json(os.path.join(work, "manifest.json"), m)
    if not res["passed"]:
        bad = [c for c in res["checks"] if not c["ok"]]
        raise RuntimeError(f"{nv} FAILED verification: " + "; ".join(f"{c['check']}: {c['detail']}" for c in bad))
    print(f"{nv}: {len(out)} rows x {len(out.columns) - 1} cols — verification passed")
    return nv


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("raw"); i.add_argument("--work", required=True); i.add_argument("--dict")
    i.add_argument("--label"); i.add_argument("--sheet")
    a = ap.parse_args()
    if a.cmd == "init":
        init(a.raw, a.work, a.dict, a.label, a.sheet)


if __name__ == "__main__":
    main()
