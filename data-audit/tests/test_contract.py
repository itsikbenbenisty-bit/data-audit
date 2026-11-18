#!/usr/bin/env python3
"""Gate 13 - the contract data-audit hands to downstream analysis (stat-analysis), 17.11.26.

Three promises, each tested against the failure it prevents:
  R  reverse keys: an item the dictionary marks reverse that the log already reversed is NOT reversed again
     (alpha of the reversed version equals alpha of the raw version); a log reversal the dictionary does not
     support, or a partial one, is a blocking finding.
  N  nflow.json exists, its final N equals the last version, and its arithmetic is checked.
  D  decisions.py: a blocking finding without a decision fails (exit 1); "later" without a rule stays open;
     a decided blocking finding passes; a row that answers no finding is reported.

usage: test_contract.py OUT_DIR      (fully synthetic data; nothing real is read)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SC = os.environ.get("DA_SC") or os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SC)
from da_common import write_dictionary  # noqa: E402
from versioning import init, load_version, save_version  # noqa: E402

out = sys.argv[1]
shutil.rmtree(out, ignore_errors=True)
os.makedirs(out)
rng = np.random.default_rng(13)
N = 200
lat = rng.standard_normal(N)
cols = {}
for j in range(1, 7):
    raw = 0.75 * lat + np.sqrt(1 - 0.75 ** 2) * rng.standard_normal(N)
    x = np.digitize(raw, np.linspace(-1.2, 1.2, 4)) + 1          # 1..5
    cols[f"S{j}"] = 6 - x if j in (2, 5) else x                      # S2, S5 reverse-keyed in the instrument
df = pd.DataFrame(cols)
df.insert(0, "id", [f"P{i:04d}" for i in range(N)])
raw_csv = os.path.join(out, "raw.csv")
df.to_csv(raw_csv, index=False, encoding="utf-8-sig")
rows = [{"var": "id", "type": "id", "status": "confirmed"}]
rows += [{"var": f"S{j}", "type": "likert", "min": 1, "max": 5, "scale": "S",
          "reverse": "yes" if j in (2, 5) else "no", "status": "confirmed"} for j in range(1, 7)]
dict_path = os.path.join(out, "dict.xlsx")
write_dictionary(dict_path, pd.DataFrame(rows))

fails, total = [], [0]


def check(name, ok, detail=""):
    total[0] += 1
    print(f"  {'ok ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        fails.append(name)


def measure(data, tag):
    o = os.path.join(out, f"measure_{tag}")
    subprocess.run([sys.executable, "-W", "ignore", os.path.join(SC, "measure.py"), data, "--dict", dict_path,
                    "--id", "id", "--out", o], check=True, capture_output=True)
    return json.load(open(os.path.join(o, "measure.json"), encoding="utf-8"))


# --- R: reverse keys ---------------------------------------------------------------------------------------
work = os.path.join(out, "work")
init(raw_csv, work=work, dict_path=dict_path)
m1 = measure(os.path.join(work, "v01.csv"), "v01")
a1 = m1["reliability"]["S"]["alpha_scored"]
d = load_version(work)
for c in ("S2", "S5"):
    d[c] = (6 - d[c].astype(int)).astype(str)
save_version(d, work, [{"step": "t", "action": "reverse", "vars": ["S2", "S5"], "rows": "all", "min": 1, "max": 5,
                        "rationale": "instrument reverse keys", "source": "test"}], note="reverse S2 S5")
m2 = measure(os.path.join(work, "v02.csv"), "v02")
a2 = m2["reliability"]["S"]["alpha_scored"]
check("R1 alpha unchanged after the reversal is applied in the file", abs(a1 - a2) < 1e-9, f"v01 {a1} / v02 {a2}")
st = m2.get("reverse_state", {})
check("R2 reverse_state records S2, S5 as already reversed",
      all("already reversed" in st.get(v, {}).get("effective", "") for v in ("S2", "S5")), json.dumps(st)[:120])
check("R3 no blocking reverse finding on a consistent file",
      not any(f["check"] in ("reverse_log_conflict", "reverse_already_applied") and f["severity"] == "blocking"
              for f in m2["findings"]))
check("R4 measure.json names the file it describes",
      m2.get("version") == "v02" and len(m2.get("data_sha256", "")) == 64)
# a reversal the dictionary does not support
d = load_version(work)
d["S3"] = (6 - d["S3"].astype(int)).astype(str)
save_version(d, work, [{"step": "t", "action": "reverse", "vars": ["S3"], "rows": "all", "min": 1, "max": 5,
                        "rationale": "mistake", "source": "test"}], note="reverse S3 by mistake")
m3 = measure(os.path.join(work, "v03.csv"), "v03")
check("R5 log reversal without dictionary support is blocking",
      any(f["check"] == "reverse_log_conflict" and f["var"] == "S3" and f["severity"] == "blocking" for f in m3["findings"]))
# a partial reversal
d = load_version(work)
part = d["_row"].astype(int) <= 50
d.loc[part, "S6"] = (6 - d.loc[part, "S6"].astype(int)).astype(str)
save_version(d, work, [{"step": "t", "action": "reverse", "vars": ["S6"], "rows": d.loc[part, "_row"].astype(int).tolist(),
                        "min": 1, "max": 5, "rationale": "wave 1 only", "source": "test"}], note="partial")
m4 = measure(os.path.join(work, "v04.csv"), "v04")
check("R6 partial reversal is blocking",
      any(f["check"] == "reverse_log_conflict" and f["var"] == "S6" for f in m4["findings"]))
check("R7 a raw file outside a work folder is scored from the dictionary alone", m1.get("reverse_state") == {})

# --- N: nflow.json -----------------------------------------------------------------------------------------
d = load_version(work)
save_version(d[d["_row"].astype(int) > 10], work, [{"step": "t", "action": "remove_rows", "rows": list(range(1, 11)),
                                                     "rationale": "test rows", "source": "test"}], note="remove 10")
subprocess.run([sys.executable, "-W", "ignore", os.path.join(SC, "verify.py"), work, "--nflow"], check=True,
               capture_output=True)
nf = json.load(open(os.path.join(work, "nflow.json"), encoding="utf-8"))
check("N1 nflow.json final N = last version", nf["final_rows"] == N - 10, str(nf["final_rows"]))
check("N2 nflow arithmetic checked and true", nf["arithmetic_ok"] is True)

# --- D: decisions ------------------------------------------------------------------------------------------
fj = os.path.join(out, "findings.json")
json.dump({"findings": [{"id": "F001", "severity": "blocking", "check": "test_rows", "var": ""},
                        {"id": "F002", "severity": "decision", "check": "item_missing", "var": "S1"},
                        {"id": "F003", "severity": "info", "check": "constant", "var": "S4"}]},
          open(fj, "w", encoding="utf-8"))


def decide(rows, tag):
    p = os.path.join(out, f"dec_{tag}.csv")
    pd.DataFrame(rows, columns=["finding", "check", "var", "count", "decision", "option", "rationale", "source"]) \
        .to_csv(p, index=False, encoding="utf-8-sig")
    o = os.path.join(out, f"dstat_{tag}")
    r = subprocess.run([sys.executable, os.path.join(SC, "decisions.py"), "--findings", fj, "--findings",
                        os.path.join(out, "measure_v03", "measure.json"), "--decisions", p, "--out", o],
                       capture_output=True, text=True)
    return r.returncode, json.load(open(os.path.join(o, "decisions_status.json"), encoding="utf-8"))


rc, s = decide([["F002", "", "", "", "later", "", "", ""]], "open")
check("D1 blocking without a decision fails", rc == 1 and "scan:F001" in s["open_blocking"])
check("D2 'later' without a rule stays open",
      next(f for f in s["findings"] if f["key"] == "scan:F002")["status"] == "open")
mb = [f["id"] for f in m3["findings"] if f["severity"] == "blocking"]
rc, s = decide([["F001", "", "", "", "remove rows 1-2", "remove", "test rows", "researcher"],
                ["F002", "", "", "", "later", "", "rule R03: listwise", ""]]
               + [[f"measure:{i}", "", "", "", "undo with a new version", "", "", "researcher"] for i in mb]
               + [["F999", "", "", "", "x", "", "", ""]], "ok")
check("D3 decided blocking findings pass", rc == 0 and s["ok"], str(s["open_blocking"]))
check("D4 'later' with a rule is deferred",
      next(f for f in s["findings"] if f["key"] == "scan:F002")["status"] == "deferred")
check("D5 a row answering no finding is reported", s["unknown_in_decisions"] == ["scan:F999"])
check("D6 measure findings are part of the status", any(f["source"] == "measure" for f in s["findings"]))

print(f"CONTRACT GATE: {'PASS' if not fails else 'FAIL ' + ', '.join(fails)}  ({total[0] - len(fails)}/{total[0]})")
sys.exit(1 if fails else 0)
