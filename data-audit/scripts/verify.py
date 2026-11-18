"""Verification layers A-B and the N-flow.

  python verify.py WORKDIR                 # last transition
  python verify.py WORKDIR --all           # every transition in the chain
  python verify.py WORKDIR --nflow         # N-flow table from the manifest + log

Layer A — diff-to-log reconciliation. The actual difference between two versions is
computed cell by cell on the stable _row key, and every difference must be declared in
the log; every declaration must have happened. A change without an entry fails; an entry
without a change fails. The check is mechanical, so it does not depend on the AI
remembering to document.

Layer B — invariants. N arithmetic, unique _row, no row reappearing after removal,
no variable reversed twice, no new dictionary violations introduced by cleaning, every
log entry carrying a rationale and a source, and the raw file unchanged since intake.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import ROWKEY, as_number, read_dictionary, sha256_file, split_list, write_json  # noqa: E402


def _load(work, rec):
    df = pd.read_csv(os.path.join(work, rec["file"]), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df[ROWKEY] = df[ROWKEY].astype(int)
    return df


def _log(work):
    p = os.path.join(work, "log.jsonl")
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()] if os.path.exists(p) else []


def _rows(e):
    r = e.get("rows", [])
    return "all" if r == "all" else {int(x) for x in r}


def verify_transition(work, v_from, v_to, lang="he"):
    m = json.load(open(os.path.join(work, "manifest.json"), encoding="utf-8"))
    vs = {v["version"]: v for v in m["versions"]}
    P, C = _load(work, vs[v_from]), _load(work, vs[v_to])
    log = _log(work)
    E = [e for e in log if e.get("version_from") == v_from and e.get("version_to") == v_to]
    checks = []

    def chk(layer, name, ok, detail):
        checks.append({"layer": layer, "check": name, "ok": bool(ok), "detail": detail})

    # ---------------- layer A
    removed = set(P[ROWKEY]) - set(C[ROWKEY])
    added = set(C[ROWKEY]) - set(P[ROWKEY])
    decl_removed = set().union(*[_rows(e) for e in E if e["action"] == "remove_rows"]) if E else set()
    chk("A", "rows_removed_match_log", removed == decl_removed,
        f"removed {sorted(removed)[:20]} ({len(removed)}); declared {sorted(decl_removed)[:20]} ({len(decl_removed)})"
        + ("" if removed == decl_removed else
           f"; unexplained {sorted(removed - decl_removed)[:20]}; declared-not-removed {sorted(decl_removed - removed)[:20]}"))
    chk("A", "no_rows_added", not added, f"added rows: {sorted(added)[:20]}" if added else "none")
    renames = {e["from"]: e["to"] for e in E if e["action"] == "rename_column"}
    pc = [c for c in P.columns if c != ROWKEY]
    cc = [c for c in C.columns if c != ROWKEY]
    dropped = set(pc) - set(cc) - set(renames)
    new = set(cc) - set(pc) - set(renames.values())
    decl_drop = {v for e in E if e["action"] == "drop_column" for v in e.get("vars", [])}
    decl_add = {v for e in E if e["action"] == "add_column" for v in e.get("vars", [])}
    chk("A", "columns_dropped_match_log", dropped == decl_drop, f"dropped {sorted(dropped)}; declared {sorted(decl_drop)}")
    chk("A", "columns_added_match_log", new == decl_add, f"added {sorted(new)}; declared {sorted(decl_add)}")
    # cell diff on common rows/cols
    Pk, Ck = P.set_index(ROWKEY), C.set_index(ROWKEY).rename(columns={v: k for k, v in renames.items()})
    common_rows = sorted(set(Pk.index) & set(Ck.index))
    common_cols = [c for c in pc if c in Ck.columns]
    a, b = Pk.loc[common_rows, common_cols], Ck.loc[common_rows, common_cols]
    diff = (a != b)
    cover = {}
    for e in E:
        if e["action"] in ("recode", "set_missing", "set_value", "reverse"):
            for v in e.get("vars", []):
                cover.setdefault(v, []).append(_rows(e))
    unexplained, noop = {}, []
    for c in common_cols:
        changed = set(diff.index[diff[c]])
        if not changed:
            continue
        allowed = cover.get(c, [])
        if any(x == "all" for x in allowed):
            continue
        ok_rows = set().union(*allowed) if allowed else set()
        bad = changed - ok_rows
        if bad:
            unexplained[c] = sorted(bad)
    for v, rs in cover.items():
        if v not in common_cols:
            noop.append(f"{v} (not in data)")
            continue
        changed = set(diff.index[diff[v]])
        for r in rs:
            if (r == "all" and not changed) or (r != "all" and not (r & changed)):
                noop.append(v)
    n_unexp = sum(len(x) for x in unexplained.values())
    chk("A", "every_changed_cell_logged", not unexplained,
        "all changes declared" if not unexplained else
        f"{n_unexp} changed cells with no log entry: " + "; ".join(f"{k}: rows {v[:10]}" for k, v in list(unexplained.items())[:8]))
    chk("A", "every_logged_change_happened", not noop,
        "every declaration changed something" if not noop else f"declared but nothing changed: {sorted(set(noop))}")
    if m.get("raw_path") and os.path.exists(m["raw_path"]):
        chk("A", "raw_unchanged", sha256_file(m["raw_path"]) == m["raw_sha256"], "sha256 of the raw file vs intake")

    # ---------------- layer B
    chk("B", "n_arithmetic", len(P) - len(removed) + len(added) == len(C),
        f"{len(P)} - {len(removed)} = {len(C)}")
    chk("B", "row_key_unique", C[ROWKEY].is_unique, "_row unique" if C[ROWKEY].is_unique else "duplicate _row values")
    earlier_removed = set()
    for e in log:
        if e["action"] == "remove_rows" and e.get("version_to") != v_to and int(e["version_to"][1:]) < int(v_to[1:]):
            earlier_removed |= _rows(e) if _rows(e) != "all" else set()
    back = earlier_removed & set(C[ROWKEY])
    chk("B", "removed_rows_stay_removed", not back, f"reappeared: {sorted(back)[:20]}" if back else "none reappeared")
    upto = [e for e in log if int(e.get("version_to", "v0")[1:]) <= int(v_to[1:])]
    rev = {}
    for e in upto:
        if e["action"] == "reverse":
            for v in e.get("vars", []):
                rev[v] = rev.get(v, 0) + 1
    twice = [v for v, n in rev.items() if n > 1]
    chk("B", "no_double_reversal", not twice, f"reversed more than once: {twice}" if twice else "ok")
    undocumented = [e.get("step", "?") for e in E if not str(e.get("rationale", "")).strip() or not str(e.get("source", "")).strip()]
    chk("B", "entries_have_rationale_and_source", not undocumented and bool(E),
        "no log entry for this transition" if not E else (f"missing rationale/source in steps {undocumented}" if undocumented else "ok"))
    if m.get("dictionary") and os.path.exists(m["dictionary"]):
        V, _ = read_dictionary(m["dictionary"])
        worse = []
        for _, r in V.iterrows():
            v = r["var"]
            if v not in C.columns or v not in P.columns or r["type"] not in ("numeric", "integer", "likert", "binary"):
                continue
            codes = set(split_list(r["missing_codes"]))
            lo, hi = as_number(pd.Series([r["min"]])).iloc[0], as_number(pd.Series([r["max"]])).iloc[0]

            def bad(s):
                s = s.str.strip()
                x = as_number(s)
                pres = (s != "") & ~s.isin(codes)
                oor = x.notna() & (((x < lo) if pd.notna(lo) else False) | ((x > hi) if pd.notna(hi) else False))
                return int((pres & (x.isna() | oor)).sum())
            bp, bc = bad(P[v]), bad(C[v])
            if bc > bp:
                worse.append(f"{v}: {bp}->{bc}")
        chk("B", "no_new_dictionary_violations", not worse, "ok" if not worse else "cleaning introduced violations: " + "; ".join(worse[:10]))
    res = {"from": v_from, "to": v_to, "passed": all(c["ok"] for c in checks), "checks": checks,
           "removed": sorted(removed), "unexplained": {k: v[:50] for k, v in unexplained.items()}}
    from report import render_verify
    write_json(os.path.join(work, f"verify_{v_from}_{v_to}.json"), res)
    open(os.path.join(work, f"verify_{v_from}_{v_to}_{lang}.md"), "w", encoding="utf-8").write(render_verify(res, lang))
    return res


def nflow(work, lang="he"):
    m = json.load(open(os.path.join(work, "manifest.json"), encoding="utf-8"))
    log = _log(work)
    flow = []
    for v in m["versions"]:
        es = [e for e in log if e.get("version_to") == v["version"]]
        rm = [e for e in es if e["action"] == "remove_rows"]
        flow.append({"version": v["version"], "rows": v["rows"],
                     "step": ", ".join(sorted({str(e.get("step", "")) for e in es})),
                     "removed": sum(len(e["rows"]) for e in rm) if rm else "",
                     "reason": " | ".join(e.get("rationale", "") for e in rm)})
    for a, b in zip(flow, flow[1:]):
        if b["removed"] != "" and a["rows"] - b["removed"] != b["rows"]:
            b["reason"] += "  ⚠ arithmetic does not add up"
    from report import render_nflow
    md = render_nflow(flow, lang)
    open(os.path.join(work, f"nflow_{lang}.md"), "w", encoding="utf-8").write(md)
    write_json(os.path.join(work, "nflow.json"), {"versions": flow, "final_rows": flow[-1]["rows"] if flow else None,
                                                  "arithmetic_ok": not any("does not add up" in str(f["reason"]) for f in flow)})
    return flow, md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--from", dest="v_from")
    ap.add_argument("--to", dest="v_to")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--nflow", action="store_true")
    ap.add_argument("--lang", default="he")
    a = ap.parse_args()
    if a.nflow:
        print(nflow(a.work, a.lang)[1])
        return
    m = json.load(open(os.path.join(a.work, "manifest.json"), encoding="utf-8"))
    vs = [v["version"] for v in m["versions"]]
    pairs = list(zip(vs, vs[1:])) if a.all else [(a.v_from or vs[-2], a.v_to or vs[-1])]
    ok = True
    for x, y in pairs:
        r = verify_transition(a.work, x, y, a.lang)
        ok &= r["passed"]
        print(f"{x}->{y}: {'PASS' if r['passed'] else 'FAIL'}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"   ✘ {c['check']}: {c['detail']}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
