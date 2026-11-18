"""Exercise versioning + verification A-B on the session-2 file.

Positive path: the three cleaning steps of the course (remove test/blank rows, remove
duplicate copies, unify department labels) must each PASS.
Sabotage path: three ways an AI typically goes wrong must each FAIL layers A-B:
  1. an extra value changed that the log does not mention (silent fix);
  2. a log entry that claims a change which never happened;
  3. a row removed without being declared.
Layer C (replay): the clean history must replay exactly, and a fourth sabotage - a change that IS
declared but whose declared mapping differs from what was written - passes A-B and must FAIL C.
Layer D (golden numbers): the N after cleaning must equal the course answer key (154).
"""
import json
import subprocess
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
from versioning import init, load_version, save_version  # noqa: E402

RAW, DICT = sys.argv[1], sys.argv[2]
ok = True


def expect(label, fn, should_pass):
    global ok
    try:
        fn()
        passed = True
    except RuntimeError as e:
        passed = False
        msg = str(e)
    good = passed == should_pass
    ok &= good
    print(f"{'✔' if good else '✘'} {label}: {'PASS' if passed else 'FAIL'}" + ("" if passed else f" ({msg[:160]})"))


work = tempfile.mkdtemp(prefix="da_")
init(RAW, work, DICT, "session2")
E = dict(decided_by="researcher", tag="method", source="dictionary + course answer key")


def step1():
    df = load_version(work)
    df = df[~df["_row"].isin([1, 112])]
    save_version(df, work, [{**E, "step": "2.1", "action": "remove_rows", "rows": [1, 112], "finding": "F001,F002",
                             "rationale": "test row and a row with no answers"}])


def step2():
    df = load_version(work)
    df = df[~df["_row"].isin([27, 72, 77])]
    save_version(df, work, [{**E, "step": "2.2", "action": "remove_rows", "rows": [27, 72, 77], "finding": "F003",
                             "rationale": "exact duplicate submissions; first copy kept"}])


def step3():
    df = load_version(work)
    m = {"שרות לקוחות": "שירות לקוחות", "משאבי-אנוש": "משאבי אנוש", "תפעול ": "תפעול"}
    rows = df.loc[df["מחלקה"].isin(m), "_row"].tolist()
    df["מחלקה"] = df["מחלקה"].replace(m)
    save_version(df, work, [{**E, "step": "2.3", "action": "recode", "vars": ["מחלקה"], "rows": rows, "map": m,
                             "finding": "F006", "rationale": "label variants mapped to dictionary labels"}])


expect("step 2.1 remove test/blank rows", step1, True)
expect("step 2.2 remove duplicate copies", step2, True)
expect("step 2.3 unify department labels", step3, True)

base = load_version(work)
snap = os.path.join(work, "_snapshot")
shutil.copytree(work, snap, ignore=shutil.ignore_patterns("_snapshot"))


def restore():
    for f in os.listdir(work):
        if f != "_snapshot":
            p = os.path.join(work, f)
            os.remove(p) if os.path.isfile(p) else shutil.rmtree(p)
    for f in os.listdir(snap):
        shutil.copy2(os.path.join(snap, f), work)


def sab1():
    df = load_version(work)
    df.loc[df["_row"] == 5, "ותק_בשנים"] = "4"          # silent change
    df.loc[df["_row"] == 40, "מחוברות_6"] = str(6 - int(df.loc[df["_row"] == 40, "מחוברות_6"].iloc[0]))
    save_version(df, work, [{**E, "step": "x", "action": "recode", "vars": ["ותק_בשנים"], "rows": [5],
                             "rationale": "typo fix", "finding": "-"}])


def sab2():
    df = load_version(work)
    save_version(df, work, [{**E, "step": "x", "action": "reverse", "vars": ["מחוברות_6"], "rows": "all",
                             "rationale": "reverse item 6", "finding": "-"}])


def sab3():
    df = load_version(work)
    df = df[df["_row"] != 50]
    save_version(df, work, [{**E, "step": "x", "action": "recode", "vars": ["אגף"], "rows": [3],
                             "rationale": "nothing", "finding": "-"}])


for label, fn in [("sabotage 1: an unlogged value change", sab1),
                  ("sabotage 2: a logged reversal that never happened", sab2),
                  ("sabotage 3: a row removed silently", sab3)]:
    restore()
    expect(label, fn, False)
restore()

SC = os.path.join(HERE, "..", "scripts")


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


r = run(os.path.join(SC, "replay.py"), work, "--strict", "--trace", "5")
good = r.returncode == 0
ok &= good
print(f"{'✔' if good else '✘'} layer C: the clean history replays exactly from the log" + ("" if good else f" ({r.stdout[-300:]})"))


def sab4():
    df = load_version(work)
    rows = df.loc[df["מחלקה"] == "תפעול", "_row"].tolist()[:2]
    df.loc[df["_row"].isin(rows), "מחלקה"] = "כספים"          # written: כספים
    save_version(df, work, [{**E, "step": "x", "action": "recode", "vars": ["מחלקה"], "rows": rows,
                             "map": {"תפעול": "שיווק"}, "rationale": "reassign", "finding": "-"}])  # declared: שיווק


try:
    sab4()
    ab_pass = True
except RuntimeError:
    ab_pass = False
r = run(os.path.join(SC, "replay.py"), work)
good = ab_pass and r.returncode != 0
ok &= good
print(f"{'✔' if good else '✘'} sabotage 4: declared map differs from what was written - A-B {'pass' if ab_pass else 'FAIL'}, "
      f"C {'FAIL (caught)' if r.returncode else 'pass (missed)'}")
restore()

g = os.path.join(work, "_goldens.json")
json.dump([{"name": "N after cleaning (course answer key)", "kind": "rows", "expected": 154, "source": "session-2 key"},
           {"name": "N in the raw export", "kind": "rows", "version": "first", "expected": 159, "source": "Qualtrics"}],
          open(g, "w", encoding="utf-8"), ensure_ascii=False)
r = run(os.path.join(SC, "golden.py"), work, g)
good = r.returncode == 0
ok &= good
print(f"{'✔' if good else '✘'} layer D: golden numbers" + ("" if good else f" ({r.stdout[-300:]})"))

r = run(os.path.join(SC, "method.py"), work, "--lang", "he")
mtxt = open(r.stdout.strip(), encoding="utf-8").read() if r.returncode == 0 else ""
good = r.returncode == 0 and "159" in mtxt and "154" in mtxt and "Van den Broeck" in mtxt and "[לאישור" in mtxt
ok &= good
print(f"{'✔' if good else '✘'} method draft: N from the log (159 -> 154), Van den Broeck table, open brackets for what the log cannot know")

from verify import nflow  # noqa: E402
print(nflow(work)[1])
print("ALL TESTS", "OK" if ok else "FAILED")
shutil.rmtree(work)
