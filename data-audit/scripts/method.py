#!/usr/bin/env python3
"""Draft the data-preparation part of a Method section from the log - every number comes from the log.

  method.py WORK [--measure measure.json] [--project RUN] [--lang he|en] [--out FILE]

Writes: a paragraph (sample flow, screening, decisions, missing data, reliability) and a table in the
spirit of Van den Broeck et al. (2005): screening -> diagnosis -> treatment for every logged decision.
What the log cannot know (who decided, whether rules were set in advance, the citation style, how much
the audience needs) comes from project.json; when it is unset, the draft says so in a bracket
[לאישור: ...] instead of inventing it. The draft is a draft: the researcher edits and owns it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(__file__))

ACT_HE = {"remove_rows": "הוסרו", "recode": "קודדו מחדש", "set_missing": "הוגדרו כחסרים", "set_value": "תוקנו",
          "reverse": "הופכו", "add_column": "נוספו", "drop_column": "הושמטו", "rename_column": "שונה שמם"}
ACT_EN = {"remove_rows": "removed", "recode": "recoded", "set_missing": "set to missing", "set_value": "corrected",
          "reverse": "reverse-scored", "add_column": "added", "drop_column": "dropped", "rename_column": "renamed"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--measure")
    ap.add_argument("--project")
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    ap.add_argument("--out")
    a = ap.parse_args()
    he = a.lang == "he"
    m = json.load(open(os.path.join(a.work, "manifest.json"), encoding="utf-8"))
    log = [json.loads(x) for x in open(os.path.join(a.work, "log.jsonl"), encoding="utf-8") if x.strip()]
    meas = json.load(open(a.measure, encoding="utf-8")) if a.measure else {}
    get = (lambda k: None)
    if a.project:
        from project import get as pget
        get = (lambda k: pget(a.project, k))
    ask = (lambda what: f"[לאישור: {what}]") if he else (lambda what: f"[to confirm: {what}]")
    vs = m["versions"]
    n0, n1 = vs[0]["rows"], vs[-1]["rows"]
    removals = OrderedDict()
    for e in log:
        if e["action"] == "remove_rows":
            n = len(e["rows"]) if e["rows"] != "all" else 0
            removals[e.get("rationale", "")] = removals.get(e.get("rationale", ""), 0) + n
    other = [e for e in log if e["action"] != "remove_rows"]
    prereg = get("preregistered_rules")
    L = []
    if he:
        L.append("### הכנת הנתונים")
        rem = "; ".join(f"{n} {r}" for r, n in removals.items()) or "לא הוסרו רשומות"
        L.append(f"קובץ הנתונים הגולמי כלל {n0} רשומות. לאחר בדיקה שיטתית של המקור, המבנה, המזהים והערכים "
                 f"מול מילון הנתונים הוסרו {n0 - n1} רשומות ({rem}), ובמדגם הסופי נותרו {n1} רשומות. ")
        if other:
            L.append("בנוסף " + "; ".join(
                f"{ACT_HE.get(e['action'], e['action'])} ערכים במשתנים {', '.join(e.get('vars', []))} "
                f"({len(e['rows']) if e.get('rows') != 'all' else 'כל'} רשומות; {e.get('rationale', '')})" for e in other) + ". ")
        L.append("כללי ההחרגה והטיפול בנתונים חסרים " + (f"נקבעו מראש ({prereg})" if prereg else ask("האם נקבעו מראש, והיכן תועדו")) + ". ")
        L.append("כל שינוי נרשם ביומן מגורסא, ונבדק אוטומטית מול הנתונים: התאמה תא-תא בין השינוי לרישום ביומן, "
                 "שימור מספר הרשומות, ובנייה מחדש של כל גרסה מתוך היומן בלבד.")
    else:
        L.append("### Data preparation")
        rem = "; ".join(f"{n} {r}" for r, n in removals.items()) or "no records were removed"
        L.append(f"The raw data file contained {n0} records. After a systematic audit of provenance, structure, "
                 f"identifiers and values against the data dictionary, {n0 - n1} records were removed ({rem}), "
                 f"leaving {n1} records for analysis. ")
        if other:
            L.append("In addition, " + "; ".join(
                f"values of {', '.join(e.get('vars', []))} were {ACT_EN.get(e['action'], e['action'])} "
                f"({len(e['rows']) if e.get('rows') != 'all' else 'all'} records; {e.get('rationale', '')})" for e in other) + ". ")
        L.append("Exclusion and missing-data rules were " + (f"set in advance ({prereg})" if prereg else ask("set in advance? where documented")) + ". ")
        L.append("Every change was logged and checked automatically against the data: a cell-by-cell match between "
                 "change and log, conservation of N, and a rebuild of every version from the log alone.")
    rel = meas.get("reliability", {})
    if rel:
        parts = [f"{sc}: α = {r['alpha_scored']:.2f}".replace("0.", ".") for sc, r in rel.items()
                 if isinstance(r.get("alpha_scored"), (int, float))]
        if parts:
            L.append(("מהימנות העקביות הפנימית: " if he else "Internal consistency: ") + "; ".join(parts) + ".")
    grp = meas.get("groups", {}).get("scales", {})
    if grp:
        parts = [f"{sc}: ICC(1) = {g['ICC1']:.2f}, ICC(2) = {g['ICC2']:.2f}, rwg(j) = {g['rwg_mean']:.2f}" for sc, g in grp.items()
                 if g.get("rwg_mean") is not None]
        if parts:
            L.append(("הצדקת הצבירה לרמת הקבוצה: " if he else "Justification for aggregation: ") + "; ".join(parts) + ".")
    aud = get("method_audience")
    if not aud:
        L.append("\n" + ask("לאיזה קהל (עבודת גמר / מאמר / דוח קורס) - זה קובע כמה מפרטי הניקוי נכנסים לגוף הטקסט ומה עובר לנספח"
                            if he else "audience (thesis / article / course report) decides how much goes into the text vs an appendix"))
    L.append("")
    L.append("### " + ("טבלת ניקוי הנתונים (Van den Broeck et al., 2005)" if he else "Data cleaning table (Van den Broeck et al., 2005)"))
    L.append("| שלב | ממצא (סינון) | אבחנה | טיפול | רשומות | מקור ההחלטה |" if he else
             "| Step | Finding (screening) | Diagnosis | Treatment | Records | Decided by |")
    L.append("|---|---|---|---|---|---|")
    for e in log:
        n = len(e["rows"]) if e.get("rows") not in ("all", None) else ("כל" if he else "all")
        act = (ACT_HE if he else ACT_EN).get(e["action"], e["action"])
        L.append(f"| {e.get('step', e.get('version_to'))} | {e.get('finding', '')} | {e.get('rationale', '')} | "
                 f"{act} {', '.join(e.get('vars', []))} | {n} | {e.get('source', '') or ask('מי החליט' if he else 'who decided')} |")
    L.append("")
    L.append(("הטיוטה נגזרה מהיומן; כל מספר בה מופיע ביומן. יש לערוך את הניסוח ולאשר את הסוגריים המרובעים."
              if he else "Drafted from the log; every number appears in it. Edit the wording and confirm the brackets."))
    txt = "\n".join(L) + "\n"
    out = a.out or os.path.join(a.work, f"method_{a.lang}.md")
    open(out, "w", encoding="utf-8").write(txt)
    print(out)


if __name__ == "__main__":
    main()
