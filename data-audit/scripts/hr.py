#!/usr/bin/env python3
"""Organisational / HR / biographical data: logic between fields, hierarchy, and free-text privacy.

  hr.py DATA --out DIR [--map role=column ...] [--asof YYYY-MM-DD] [--lang he|en]

Roles are found by column name (Hebrew or English) or given with --map, e.g.
  --map id=מספר_עובד birth=תאריך_לידה hire=תחילת_עבודה exit=סיום_עבודה manager=מנהל_ישיר
        age=גיל tenure=ותק salary=שכר grade=דרגה dept=מחלקה
Checks (nothing is changed):
  dates     birth/hire/exit parse; exit before hire; hire before age 15; age outside 15-80;
            stated age or tenure disagreeing with the dates (as of --asof)
  hierarchy manager IDs that are not employees; employees who manage themselves; cycles in the chain
  pay       salary outside its grade's range (robust: median +- 4 MAD within grade); non-positive pay
  privacy   free-text columns holding e-mails, phone numbers, ID-like numbers (with the Israeli check
            digit), URLs, or personal names after a title (a third party named in an open answer)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import ROWKEY, as_number, load_table, to_str, write_json  # noqa: E402

ROLES = {
    "id": r"(^id$|employee.?(id|no|num)|emp.?no|מספר.?עובד|מס.?עובד|^מ\.?א\.?$|מספר.?אישי|ת\.?ז)",
    "birth": r"(birth|dob|תאריך.?לידה|ת\.?לידה)",
    "hire": r"(hire|start.?date|join|תחילת.?עבודה|תאריך.?קליטה|תאריך.?גיוס|גיוס)",
    "exit": r"(exit|termination|leave.?date|end.?date|סיום.?עבודה|תאריך.?עזיבה|שחרור)",
    "manager": r"^(?!.*(years?|since|tenure|time|count|num|satisf|rating|שנים|ותק)).*(manager|supervisor|reports.?to|מנהל.?ישיר|^מנהל$|ממונה)",
    "age": r"(^age$|^גיל$)",
    "tenure": r"(tenure|seniority|years.?at|ותק)",
    "salary": r"(salary|pay|wage|income|שכר|משכורת)",
    "grade": r"(grade|level|band|rank|דרגה|דירוג)",
    "dept": r"(department|dept|unit|division|מחלקה|יחידה|אגף)",
    "status": r"(^status$|employment.?status|active|סטטוס|מצב.?העסקה|פעיל)",
    "eff_start": r"(effective.?(start|from)|valid.?from|מתאריך|תוקף.?מ)",
    "eff_end": r"(effective.?(end|to)|valid.?to|עד.?תאריך|תוקף.?עד)",
}
ACTIVE = re.compile(r"^(active|פעיל|פעילה|employed|current|1|yes|כן)$", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+972|0)(?:[23489]|5\d|7\d)[-\s]?\d{3}[-\s]?\d{4}")
LONGNUM = re.compile(r"(?<!\d)\d{7,9}(?!\d)")
URL = re.compile(r"https?://\S+|www\.\S+")
GPS = re.compile(r"(?<![\d.])-?(?:[1-8]?\d)\.\d{3,}\s*,\s*-?(?:1[0-7]\d|\d{1,2})\.\d{3,}(?![\d.])")
TITLE_NAME = re.compile(r"(?<![\u05d0-\u05ea\w])(?:המפקד|המפקדת|מפקד|מ\"פ|מפ\"ץ|מפק\"ץ|סמל|רס\"ר|סרן|סגן|רס\"ן|סא\"ל|אל\"ם|ד\"ר|פרופ'|המנהל|המנהלת|"
                        r"מר|גב'|Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Prof\.?|Capt\.?|Sgt\.?)\s+([א-תA-Z][א-ת\w'\"]{1,15})")


STOP = set("""את על של עם לא כן הוא היא הם הן זה זו זאת שלי שלנו שלו שלה בצורה מאוד יותר פחות כל גם רק אבל כי אם
או מה מי איך למה כמו אחד אחת היה הייתה יש אין צריך צריכה טוב טובה מצוין מצוינת ממש תמיד לפעמים בכלל בעיקר הרבה מעט
צוות הצוות הפלוגה פלוגה מחלקה המחלקה שאני שהוא שהיא מעולה מעולים אדיר מדהים נהדר תותח מקצועי מקצועית אכפתי אכפתית
קשוח רגיש רגישה חזק חזקה נותן נותנת מצליח מצליחה מאפשר מאפשרת יודע יודעת לנו לי לו לה the a an and is was of to in who""".split())


def il_id_ok(x: str) -> bool:
    if not x.isdigit() or not 5 <= len(x) <= 9:
        return False
    x = x.zfill(9)
    return sum((d if d < 10 else d - 9) for d in (int(c) * (1 + i % 2) for i, c in enumerate(x))) % 10 == 0


def find_roles(cols, given):
    roles = dict(given)
    for role, pat in ROLES.items():
        if role in roles:
            continue
        hit = next((c for c in cols if re.search(pat, str(c), re.I)), None)
        if hit and hit not in roles.values():
            roles[role] = hit
    return roles


class F:
    def __init__(self, ids):
        self.items, self.ids = [], ids

    def add(self, check, severity, var, rows, evidence, **extra):
        rows = [int(r) for r in rows]
        f = {"id": f"H{len(self.items) + 1:03d}", "layer": 4, "check": check, "severity": severity, "var": var,
             "count": len(rows), "rows": rows[:300], "evidence": evidence, "status": "open", **extra}
        if self.ids is not None and rows:
            f["ids"] = [to_str(self.ids.get(r, "")) for r in rows[:50]]
        self.items.append(f)


def privacy_scan(df: pd.DataFrame, out: F, skip: set):
    for c in df.columns:
        if c in skip or c == ROWKEY:
            continue
        s = df[c].astype(str).str.strip()
        s = s[s != ""]
        if s.empty or s.str.len().median() < 4 or s.str.fullmatch(r"[\d.\-/: ]+").mean() > 0.8:
            continue                                    # numbers, codes, dates: not free text
        hits, kinds = set(), Counter()
        for r, v in s.items():
            k = []
            if EMAIL.search(v):
                k.append("email")
            if PHONE.search(v):
                k.append("phone")
            nums = LONGNUM.findall(v)
            if nums:
                k.append("id_checksum" if any(il_id_ok(n) for n in nums) else "long_number")
            if URL.search(v):
                k.append("url")
            if GPS.search(v):
                k.append("gps")
            if any(m.group(1) not in STOP for m in TITLE_NAME.finditer(v)):
                k.append("name_after_title")
            if k:
                hits.add(r)
                kinds.update(k)
        if hits:
            sev = "blocking" if kinds.keys() & {"email", "phone", "id_checksum", "gps"} else "decision"
            out.add("pii_in_text", sev, c, sorted(hits),
                    f"open answers that may identify people: {dict(kinds)} - scrub or withhold before sharing",
                    kinds=dict(kinds))


def small_cells(df: pd.DataFrame, out: "F", quasi: list[str], k: int):
    """k-anonymity: combinations of quasi-identifiers shared by fewer than k people."""
    cols = [c for c in quasi if c in df.columns]
    if not cols:
        return
    key = df[cols].astype(str).apply(lambda r: " | ".join(x.strip() for x in r), axis=1)
    size = key.map(key.value_counts())
    rare = size < k
    if rare.any():
        out.add("small_cells", "blocking", ", ".join(cols), df.index[rare],
                f"{int(rare.sum())} people sit in combinations of {cols} shared by fewer than {k} people "
                f"({int(key[rare].nunique())} such combinations): they could be recognised in a shared file or a "
                "table - coarsen (age bands, merge units), suppress, or do not share")


def run(path, given, asof, quasi=None, k=5):
    L = load_table(path)
    df = L.df.set_index(ROWKEY, drop=False)
    cols = [c for c in df.columns if c != ROWKEY]
    R = find_roles(cols, given)
    out = F(df[R["id"]] if "id" in R else None)
    now = pd.Timestamp(asof) if asof else pd.Timestamp.today().normalize()

    def date(role):
        if role not in R:
            return None
        s = df[R[role]].astype(str).str.strip()
        num = as_number(s)
        iso = s[s != ""].str.match(r"^\d{4}-\d{1,2}-\d{1,2}").mean() > 0.8 if (s != "").any() else False
        d = pd.to_datetime(s.where(s != ""), errors="coerce", format="mixed", dayfirst=not iso)
        serial = num.between(10000, 60000) & d.isna()
        if serial.any():
            d = d.fillna(pd.to_datetime("1899-12-30") + pd.to_timedelta(num.where(serial), unit="D"))
        bad = (s != "") & d.isna()
        if bad.any():
            out.add("bad_date", "blocking", R[role], df.index[bad], "does not parse as a date",
                    examples=s[bad].head(5).tolist())
        return d

    birth, hire, exit_ = date("birth"), date("hire"), date("exit")
    if hire is not None and exit_ is not None:
        x = exit_ < hire
        if x.any():
            out.add("exit_before_hire", "blocking", f"{R['hire']}/{R['exit']}", df.index[x.fillna(False)], "leaves before joining")
    if birth is not None:
        age_now = (now - birth).dt.days / 365.25
        x = (age_now < 15) | (age_now > 100)
        if x.any():
            out.add("implausible_birth", "blocking", R["birth"], df.index[x.fillna(False)], "age today below 15 or above 100")
        if hire is not None:
            ah = (hire - birth).dt.days / 365.25
            x = (ah < 15) | (ah > 80)
            if x.any():
                out.add("implausible_hire_age", "blocking", f"{R['birth']}/{R['hire']}", df.index[x.fillna(False)],
                        "joined before age 15 or after 80 (a date typo, or day and month swapped)")
        if "age" in R:
            stated = as_number(df[R["age"]])
            diff = (stated - age_now).abs()
            x = diff > 1.5
            if x.any():
                out.add("age_disagrees_with_birth", "decision", R["age"], df.index[x.fillna(False)],
                        f"stated age differs from the birth date by more than 1.5 years (as of {now.date()}): "
                        "the age was frozen at an earlier date, or one of them is wrong")
    if "age" in R:
        a = as_number(df[R["age"]])
        x = (a < 15) | (a > 80)
        if x.any():
            out.add("implausible_age", "blocking", R["age"], df.index[x.fillna(False)], "age below 15 or above 80")
    if "tenure" in R:
        t = as_number(df[R["tenure"]])
        if hire is not None:
            end = exit_.fillna(now) if exit_ is not None else pd.Series(now, index=df.index)
            calc = (end - hire).dt.days / 365.25
            x = (t - calc).abs() > 1.5
            if x.any():
                out.add("tenure_disagrees_with_dates", "decision", R["tenure"], df.index[x.fillna(False)],
                        f"stated tenure differs from the dates by more than 1.5 years (as of {now.date()})")
        if "age" in R:
            x = t > as_number(df[R["age"]]) - 14
            if x.any():
                out.add("tenure_exceeds_age", "blocking", f"{R['tenure']}/{R['age']}", df.index[x.fillna(False)],
                        "tenure longer than the working life the age allows")
        x = t < 0
        if x.any():
            out.add("negative_tenure", "blocking", R["tenure"], df.index[x.fillna(False)], "negative tenure")
    if "manager" in R and "id" in R:
        ids = df[R["id"]].astype(str).str.strip()
        mgr = df[R["manager"]].astype(str).str.strip()
        known = set(ids[ids != ""])
        orphan = (mgr != "") & ~mgr.isin(known)
        if orphan.any():
            out.add("manager_not_employee", "decision", R["manager"], df.index[orphan],
                    "manager ID not found among employees: an external manager, a former employee, or a typo",
                    examples=sorted(set(mgr[orphan]))[:8])
        self_m = (mgr != "") & (mgr == ids)
        if self_m.any():
            out.add("self_manager", "blocking", R["manager"], df.index[self_m], "employee listed as their own manager")
        up = dict(zip(ids, mgr))
        cyc = set()
        for start in known:
            seen, cur = [], start
            while cur in up and up[cur] and cur not in seen:
                seen.append(cur)
                cur = up[cur]
            if cur in seen and cur != up.get(cur) and len(seen) > 1:
                cyc |= set(seen[seen.index(cur):])
        if cyc:
            out.add("hierarchy_cycle", "blocking", R["manager"], df.index[ids.isin(cyc)],
                    "reporting lines form a loop (A reports to B ... reports to A)")
    if "salary" in R:
        pay = as_number(df[R["salary"]])
        x = pay <= 0
        if x.any():
            out.add("non_positive_pay", "blocking", R["salary"], df.index[x.fillna(False)], "zero or negative pay")
        if "grade" in R:
            g = df[R["grade"]].astype(str).str.strip()
            bad = []
            for _, idx in g.groupby(g).groups.items():
                p = pay.loc[idx].dropna()
                if len(p) < 8:
                    continue
                med, mad = p.median(), (p - p.median()).abs().median() * 1.4826
                if mad > 0:
                    bad += list(p.index[(p - med).abs() > 4 * mad])
            if bad:
                out.add("pay_outside_grade", "decision", f"{R['salary']}/{R['grade']}", bad,
                        "pay more than 4 robust SDs from the median of its grade: a unit error (monthly vs yearly, "
                        "thousands), a part-time contract, or a wrong grade")
    # effective-dated records: several rows per person, each valid from..to (only when such columns exist)
    if "id" in R and ("eff_start" in R or (hire is not None and df[R["id"]].duplicated().any())):
        st = date("eff_start") if "eff_start" in R else hire
        en = date("eff_end") if "eff_end" in R else exit_
        if en is not None:
            ids_ = df[R["id"]].astype(str).str.strip()
            over = set()
            for _, idx in ids_[ids_ != ""].groupby(ids_).groups.items():
                if len(idx) < 2:
                    continue
                seg = pd.DataFrame({"s": st.loc[idx], "e": en.loc[idx].fillna(pd.Timestamp.max)}).sort_values("s")
                ov = seg["s"].iloc[1:].values < seg["e"].iloc[:-1].values
                if ov.any():
                    over |= set(seg.index[1:][ov]) | set(seg.index[:-1][ov])
            if over:
                out.add("overlapping_records", "blocking", f"{R['id']}", sorted(over),
                        "one person has records whose validity periods overlap: two assignments at once, or a record "
                        "that was not closed when the next one opened")
    if "status" in R and exit_ is not None:
        stale = df[R["status"]].astype(str).str.strip().str.match(ACTIVE) & exit_.notna() & (exit_ < now)
        if stale.any():
            out.add("stale_active_status", "decision", f"{R['status']}/{R['exit']}", df.index[stale],
                    f"marked active although the exit date has passed (as of {now.date()})")
    privacy_scan(df, out, set(R.values()))
    if quasi:
        small_cells(df, out, quasi, k)
    else:
        out.add("small_cells_not_checked", "info", "", [],
                "no quasi-identifiers given: which columns could identify a person together (rank, unit, age, "
                "gender...) and the smallest group that may be shared are project decisions - ask, then rerun "
                "with --quasi and --k")
    return {"file": os.path.basename(path), "rows": int(len(df)), "roles": R, "asof": str(now.date()),
            "summary": dict(Counter(f["severity"] for f in out.items)), "findings": out.items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map", nargs="*", default=[])
    ap.add_argument("--asof")
    ap.add_argument("--quasi", nargs="*", default=None, help="quasi-identifier columns (project decision)")
    ap.add_argument("--k", type=int, default=None, help="smallest shareable group (project / ethics decision)")
    ap.add_argument("--project", help="run folder with project.json")
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    quasi, k, asof = a.quasi, a.k, a.asof
    if a.project:
        from project import get
        quasi = quasi or get(a.project, "quasi_identifiers")
        k = k or get(a.project, "k_anonymity")
        asof = asof or get(a.project, "asof_date")
    if isinstance(quasi, str):
        quasi = [q.strip() for q in re.split(r"[,;]", quasi) if q.strip()]
    if quasi and not k:
        print("k not set: the smallest shareable group is an ethics/project decision - using 5 and saying so")
        k = 5
    res = run(a.data, dict(x.split("=", 1) for x in a.map), asof, quasi, int(k) if k else 5)
    write_json(os.path.join(a.out, "hr.json"), res)
    he = a.lang == "he"
    L = ["# " + ("בדיקת נתונים ארגוניים: " if he else "Organisational data audit: ") + res["file"], "",
         (f"{res['rows']} שורות; תפקידי עמודות שזוהו: {res['roles']}; נכון ל-{res['asof']}." if he else
          f"{res['rows']} rows; column roles found: {res['roles']}; as of {res['asof']}."), "",
         "| # | בדיקה | חומרה | משתנה | שורות | ראיה |" if he else "| # | Check | Severity | Variable | Rows | Evidence |",
         "|---|---|---|---|---|---|"]
    for f in res["findings"]:
        L.append(f"| {f['id']} | {f['check']} | {f['severity']} | {f['var']} | {f['count']}: "
                 f"{', '.join(map(str, f['rows'][:10]))} | {f['evidence'].replace('|', '/')[:250]} |")
    open(os.path.join(a.out, f"hr_{a.lang}.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(res["summary"], Counter(f["check"] for f in res["findings"]))


if __name__ == "__main__":
    main()
