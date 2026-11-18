#!/usr/bin/env python3
"""Decision status: the researcher's decisions joined to the findings they answer.

`scan.py` and `measure.py` write findings with `status: open`; the researcher answers them in a copy of
`decisions_template.csv` (columns finding, check, var, count, decision, option, rationale, source). This
script joins the two and writes the status of every finding, so that whatever comes next (cleaning
versions, or an analysis skill such as stat-analysis) can tell whether a blocking finding is still open.
Contract gap 2, 17.11.26.

  python decisions.py --findings RUN/scan/findings.json [--findings RUN/measure/measure.json ...]
                      --decisions RUN/decisions.csv --out RUN

  -> RUN/decisions_status.json and RUN/decisions_status_<lang>.md; exit code 1 when a blocking finding is open.

Finding ids are prefixed with their source ("scan:F003", "measure:F002"); in the decisions file a bare
"F003" means the first --findings file. A decision is
  decided   - `decision` is filled (the option chosen, or the researcher's own words), or
  deferred  - `decision` starts with "later" / "בהמשך" AND `rationale` names the rule that will decide it,
  open      - anything else.
A blocking finding may not stay open or be deferred: it must be decided before analysis.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import sha256_file, write_json  # noqa: E402

LATER = ("later", "בהמשך", "מאוחר יותר")


def source_label(path, i):
    base = os.path.basename(path)
    if base.startswith("measure"):
        return "measure"
    if base.startswith("findings"):
        return "scan" if i == 0 else f"scan{i}"
    return os.path.splitext(base)[0]


def load_findings(paths):
    out, sources = [], []
    for i, p in enumerate(paths):
        lab = source_label(p, i)
        if any(s["label"] == lab for s in sources):
            lab = f"{lab}{i}"
        res = json.load(open(p, encoding="utf-8"))
        sources.append({"label": lab, "path": os.path.abspath(p), "sha256": sha256_file(p)})
        for f in res.get("findings", []):
            out.append({"key": f"{lab}:{f['id']}", "source": lab, "id": f["id"], "severity": f["severity"],
                        "check": f["check"], "var": f.get("var", ""), "evidence": f.get("evidence", "")})
    return out, sources


def load_decisions(path, first_label):
    if not path or not os.path.exists(path):
        return {}, None
    d = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    need = {"finding", "decision"}
    if not need <= set(d.columns):
        raise SystemExit(f"{path}: needs the columns {sorted(need)} (start from decisions_template.csv)")
    rows = {}
    for r in d.to_dict("records"):
        k = r["finding"].strip()
        if not k:
            continue
        k = k if ":" in k else f"{first_label}:{k}"
        rows[k] = r
    return rows, {"path": os.path.abspath(path), "sha256": sha256_file(path)}


def status_of(f, r):
    if r is None:
        return "open"
    dec = str(r.get("decision", "")).strip()
    if not dec:
        return "open"
    if dec.lower().startswith(LATER):
        return "deferred" if str(r.get("rationale", "")).strip() else "open"
    return "decided"


def build(findings, decisions):
    out, unknown = [], sorted(set(decisions) - {f["key"] for f in findings})
    for f in findings:
        r = decisions.get(f["key"])
        st = status_of(f, r)
        rec = {**f, "status": st}
        if r is not None:
            rec.update({k: str(r.get(k, "")).strip() for k in ("decision", "option", "rationale", "source")})
        out.append(rec)
    open_blocking = [f["key"] for f in out if f["severity"] == "blocking" and f["status"] != "decided"]
    return out, open_blocking, unknown


def render(res, lang):
    he = lang == "he"
    n = {s: sum(1 for f in res["findings"] if f["status"] == s) for s in ("decided", "deferred", "open")}
    lines = [("# סטטוס החלטות" if he else "# Decision status"), ""]
    lines.append((f"הוחלטו {n['decided']}, נדחו לפי כלל {n['deferred']}, פתוחים {n['open']}." if he else
                  f"Decided {n['decided']}, deferred by rule {n['deferred']}, open {n['open']}."))
    if res["open_blocking"]:
        lines += ["", ("**ממצאים חוסמים ללא החלטה — אין להריץ ניתוח:** " if he else
                       "**Blocking findings without a decision - do not analyse:** ") + ", ".join(res["open_blocking"])]
    if res["unknown_in_decisions"]:
        lines += ["", ("שורות בקובץ ההחלטות שאינן תואמות ממצא: " if he else
                       "Rows in the decisions file that match no finding: ") + ", ".join(res["unknown_in_decisions"])]
    lines += ["", "| finding | severity | check | var | status | decision |", "| --- | --- | --- | --- | --- | --- |"]
    for f in res["findings"]:
        lines.append(f"| {f['key']} | {f['severity']} | {f['check']} | {f['var']} | {f['status']} | {f.get('decision', '')} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", action="append", required=True)
    ap.add_argument("--decisions")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    a = ap.parse_args()
    findings, sources = load_findings(a.findings)
    decisions, dsrc = load_decisions(a.decisions, sources[0]["label"])
    out, open_blocking, unknown = build(findings, decisions)
    res = {"sources": sources, "decisions_file": dsrc, "findings": out, "open_blocking": open_blocking,
           "unknown_in_decisions": unknown, "ok": not open_blocking}
    os.makedirs(a.out, exist_ok=True)
    write_json(os.path.join(a.out, "decisions_status.json"), res)
    open(os.path.join(a.out, f"decisions_status_{a.lang}.md"), "w", encoding="utf-8").write(render(res, a.lang))
    print(f"{len(out)} findings; open blocking: {len(open_blocking)}" + (f"; unknown rows: {unknown}" if unknown else ""))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
