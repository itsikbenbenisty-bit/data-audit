"""Build the data dictionary — the contract every later check runs against.

Two entry points:

  python profile_dict.py draft DATA_FILE --out dict.xlsx [--label NAME] [--lang he|en]
      Infers a DRAFT from the data. Every inferred field is a HYPOTHESIS with its evidence;
      nothing inferred is 'confirmed' until a source (questionnaire, manual, syntax, paper)
      or the researcher confirms it.

  python profile_dict.py import THEIR_DICT.xlsx --out dict.xlsx [--sheet NAME]
      Reads an existing dictionary (any language/layout), maps its headers onto the
      skill's schema, and marks rows 'confirmed' (source = that document).

The researcher-facing rule: what the skill did not read in a source is a hypothesis.
A hypothesis is tested against the data first; the researcher is asked only when the
test cannot decide.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import (DICT_COLUMNS, ROWKEY, as_number, load_table, norm_label, parse_pairs,  # noqa: E402
                       to_str, write_dictionary)

MISSING_CODE_CANDIDATES = [9, 99, 999, 9999, -1, -9, -99, -999, 88, 888, 98, 998]
LEAD_NUM = re.compile(r"^\s*(\d{1,2})\s*[-.)–:]\s*\S")
SCALE_PREFIX = re.compile(r"^(.*?)[_\-\.]?(\d{1,3})$")
DATE_NAME = re.compile(r"(date|time|תאריך|זמן|חותמת|start|end|recorded|timestamp)", re.I)
ID_NAME = re.compile(r"(^id$|_id$|^id_|responseid|מספר.?אישי|ת\.?ז|מספר.?משיב|מספר.?עובד|participant|subject.?(id|no|num|code)|^subj([_.\- ]?\d|$))", re.I)


def infer_column(name: str, s: pd.Series, qtext: str = "") -> dict:
    raw = s.astype(str).str.strip()
    nonmiss = raw[raw != ""]
    n, k = len(raw), len(nonmiss)
    row = {c: "" for c in DICT_COLUMNS}
    row.update(var=name, label=name, item_text=qtext, status="hypothesis")
    ev = [f"n={n}", f"missing={n - k}"]
    if k == 0:
        row.update(type="empty", evidence="; ".join(ev + ["all values missing"]))
        return row
    uniq = nonmiss.nunique()
    ev.append(f"unique={uniq}")
    if uniq == 1:
        row.update(type="constant", allowed=nonmiss.iloc[0], evidence="; ".join(ev + ["one value only"]))
        return row
    num = as_number(nonmiss)
    share_num = float(num.notna().mean())
    if ID_NAME.search(f"{name} {qtext}") and uniq / k > 0.6:
        row.update(type="id", required="yes", privacy="indirect",
                   evidence="; ".join(ev + [f"name/text looks like an identifier; uniqueness={uniq / k:.2f}"]))
        return row
    if DATE_NAME.search(name):
        bare = nonmiss.str.fullmatch(r"-?\d+(\.\d+)?")            # a bare number is never a written date
        d = pd.to_datetime(nonmiss.where(~bare), errors="coerce", format="mixed")
        ser = num.between(1, 80000) & nonmiss.str.fullmatch(r"\d{1,5}(\.0+)?")
        if 0 < ser.mean() < 1 and d.notna().mean() >= 0.1 and (d.notna() | ser).mean() > 0.8:
            row.update(type="datetime", evidence="; ".join(ev + [
                f"dates in two formats: {int(ser.sum())} Excel serial numbers and {int(d.notna().sum())} date strings "
                "- convert the serials (origin 1899-12-30) before use"]))
            return row
        if d.notna().mean() > 0.8:
            row.update(type="datetime", min=str(d.min()), max=str(d.max()),
                       evidence="; ".join(ev + ["parses as date; observed span shown as min/max"]))
            return row
    lead = nonmiss.str.extract(LEAD_NUM)[0]
    # Qualtrics habit: middle points exported as numbers, the labelled end points as text
    # ("7 - מסכים מאוד", "1- לא מסכים בכלל"). Treat as one scale with a text->code map.
    texty = num.isna()
    if 0.5 <= share_num < 0.999 and lead[texty].notna().mean() >= 0.9:
        pairs = sorted({(int(a), b) for a, b in zip(lead[texty].dropna(), nonmiss[texty & lead.notna()])})
        combined = num.fillna(lead.astype(float))
        lo, hi = combined.min(), combined.max()
        is_int = bool(np.all(np.isclose(combined.dropna(), np.round(combined.dropna()))))
        typ = "likert" if is_int and lo >= 0 and hi <= 11 else ("integer" if is_int else "numeric")
        row.update(type=typ, min=to_str(lo), max=to_str(hi),
                   text_map="; ".join(f"{b}=>{a}" for a, b in pairs[:20]),
                   evidence="; ".join(ev + [f"{texty.mean():.1%} of answers are labelled points that start with a number "
                                            f"(e.g. {pairs[0][1]!r}); the rest are numbers: one scale, recode labels to codes",
                                            f"observed range {to_str(lo)}–{to_str(hi)}"]))
        return row
    if share_num < 0.5 and lead.notna().mean() > 0.8:
        pairs = sorted({(int(a), b) for a, b in zip(lead.dropna(), nonmiss[lead.notna()])})
        tmap = "; ".join(f"{b}=>{a}" for a, b in pairs[:20])
        row.update(type="likert", min=str(min(p[0] for p in pairs)), max=str(max(p[0] for p in pairs)),
                   text_map=tmap, evidence="; ".join(ev + ["text answers that start with a number: candidate text->code map"]))
        return row
    if share_num >= 0.5:
        nn = num.dropna()
        codes = []
        if len(nn) > 10:
            core = nn[~nn.isin(MISSING_CODE_CANDIDATES)]
            for c in MISSING_CODE_CANDIDATES:
                if (nn == c).any() and len(core) and (c > core.quantile(0.99) * 1.5 or c < 0 <= core.min()):
                    codes.append(c)
        core = nn[~nn.isin(codes)] if codes else nn
        is_int = bool(np.all(np.isclose(core, np.round(core))))
        lo, hi = float(core.min()), float(core.max())
        typ = "integer" if is_int else "numeric"
        if is_int and lo >= 0 and hi <= 11 and core.nunique() <= 12:
            typ = "likert"
        row.update(type=typ, min=to_str(lo), max=to_str(hi))
        if codes:
            row["missing_codes"] = "; ".join(map(to_str, codes))
            ev.append(f"isolated values {codes} look like missing codes")
        if share_num < 0.999:
            bad = nonmiss[num.isna()].value_counts().head(5).to_dict()
            ev.append(f"{(1 - share_num):.1%} non-numeric values, e.g. {bad}")
        ev.append(f"observed range {to_str(lo)}–{to_str(hi)} (from data, not from a source)")
        row["evidence"] = "; ".join(ev)
        return row
    if uniq <= max(20, int(0.05 * k)):
        vc = nonmiss.value_counts()
        groups = {}
        for v in vc.index:
            groups.setdefault(norm_label(v), []).append(v)
        variants = [g for g in groups.values() if len(g) > 1]
        row.update(type="categorical", allowed="; ".join(vc.index.astype(str)))
        if variants:
            ev.append(f"label variants that normalise to one value: {variants}")
        row["evidence"] = "; ".join(ev + ["allowed = observed values (needs a source)"])
        return row
    row.update(type="text", evidence="; ".join(ev + ["free text"]))
    return row



# ---- fully labelled rating scales ("מסכים מאוד", "קצת דומה לי", "strongly agree") ------------------
INTENSITY = [  # (pattern, rank) - checked in order, the first hit wins
    (r"(בכלל לא|כלל לא|not at all|strongly disagree|never)", 1),
    (r"(^לא |^לא$| לא |disagree|rarely|^no$|^not )", 2),
    (r"(קצת|מעט|מועט|slightly|a little|somewhat disagree|seldom)", 3),
    (r"(^די |במידה בינונית|neutral|neither|moderately|somewhat|sometimes)", 4),
    (r"(מאוד|במידה רבה מאוד|strongly agree|very|extremely|always|completely)", 6),
]
NEG_WORDING = re.compile(r"(?:^|\s)(לא|אין|אינם|אינו|אינה|אינני|איני|ללא|בלי|כמעט שלא|שלא|מעט|not|never|no|hardly|rarely|little|few|"
                         r"don't|doesn't|isn't|aren't|cannot|can't)(?:\s|$|[,.])", re.I)


def label_rank(label: str) -> int:
    t = " " + norm_label(label) + " "
    for pat, rk in INTENSITY:
        if re.search(pat, t.strip(), re.I) or re.search(pat, t, re.I):
            return rk
    return 5                     # the plain form ("מסכים", "דומה לי", "agree") sits just below "very"


def anchors(qtext: str) -> dict:
    """'1 = בכלל לא דומה לי, 6= מאוד דומה לי' -> {1: label, 6: label}"""
    return {int(n): lab.strip(" .") for n, lab in re.findall(r"(\d{1,2})\s*=\s*([^,()\n\d]+)", qtext or "")}


def ordered_scale(labels: list[str], qtext: str) -> tuple[dict, str] | None:
    """Order a family's labels into codes 1..k by intensity words; check against anchors in the question."""
    labs = sorted(set(labels), key=label_rank)
    if not 3 <= len(labs) <= 11:
        return None
    ranks = [label_rank(x) for x in labs]
    if len(set(ranks)) != len(ranks):
        return None              # two labels at one intensity: cannot order them safely
    an = anchors(qtext)
    tmap = {lab: i + 1 for i, lab in enumerate(labs)}
    ev = f"labels ordered by intensity words: {' < '.join(labs)}"
    if an:
        lo, hi = min(an), max(an)
        ok = norm_label(an[lo]) == norm_label(labs[0]) and norm_label(an[hi]) == norm_label(labs[-1])
        if not ok:
            return None
        if hi - lo + 1 == len(labs):
            tmap = {lab: lo + i for i, lab in enumerate(labs)}
        ev += f"; end points match the anchors in the question text ({lo} = {an[lo]}, {hi} = {an[hi]})"
    return tmap, ev


def labelled_families(df: pd.DataFrame, rows: list[dict], qtext: dict) -> None:
    fam = {}
    for r in rows:
        m = SCALE_PREFIX.match(r["var"])
        if r["type"] != "categorical":
            continue
        key = m.group(1).rstrip("_-. ") if m and m.group(1) else r["var"]
        fam.setdefault(key, []).append(r)
    for g, items in fam.items():
        if len(items) < 3:                        # a single rating item: order it only with >= 4 intensity labels
            v_ = df[items[0]["var"]].astype(str).str.strip()
            if len(items) > 1 or v_[v_ != ""].nunique() < 4:
                continue
        labs = set()
        for r in items:
            v = df[r["var"]].astype(str).str.strip()
            labs |= set(v[v != ""])
        res = ordered_scale(sorted(labs), qtext.get(items[0]["var"], ""))
        if not res:
            continue
        tmap, ev = res
        for r in items:
            r.update(type="likert", min=str(min(tmap.values())), max=str(max(tmap.values())), allowed="",
                     text_map="; ".join(f"{k}=>{v}" for k, v in sorted(tmap.items(), key=lambda kv: kv[1])))
            r["evidence"] += "; " + ev + " (hypothesis: confirm the order)"


def wording_hypotheses(rows: list[dict]) -> None:
    for r in rows:
        if not r.get("scale") or "ranking" in r.get("notes", ""):
            continue
        item = str(r.get("item_text", "")).rsplit(" - ", 1)[-1]
        m = NEG_WORDING.search(item)
        if m and r.get("reverse") != "?":
            r["reverse"] = "?"
            r["evidence"] += f"; negatively worded ('{m.group(1)}'): reverse-keyed? decide from the source instrument"


def scale_hypotheses(df: pd.DataFrame, rows: list[dict]) -> None:
    """Group item-like columns into candidate scales; flag candidate reverse items."""
    groups = {}
    for r in rows:
        m = SCALE_PREFIX.match(r["var"])
        if m and r["type"] in ("likert", "integer") and m.group(1):
            groups.setdefault(m.group(1).rstrip("_-. "), []).append(r)
    for g, items in groups.items():
        if len(items) < 3:
            continue
        cols = [i["var"] for i in items]
        X = pd.DataFrame({c: as_number(df[c].astype(str).str.strip().map(
            lambda x, m=parse_pairs(i["text_map"], "=>"): m.get(x, x))) for c, i in zip(cols, items)})
        for i in items:
            i["scale"] = g
            i["evidence"] += f"; scale '{g}' by shared prefix ({len(items)} items)"
        if len(X.dropna()) < 10:
            continue
        comp = X.dropna()
        if (comp.nunique(axis=1) == comp.shape[1]).mean() >= 0.9:
            for i in items:
                i["notes"] = "ranking (ipsative) items: every respondent gives each item a different value"
            continue
        # the first principal component, oriented so most items load positively: an item loading the other way
        # is a reverse candidate. (A corrected item-total r misleads when 2 of 5 items are reversed: the rest score
        # is itself contaminated, and every item can come out negative.)
        C = comp.corr().fillna(0).values
        w, vec = np.linalg.eigh(C)
        load = vec[:, -1] * np.sqrt(max(w[-1], 0))
        if (load > 0).sum() < (load < 0).sum() or ((load > 0).sum() == (load < 0).sum() and load.sum() < 0):
            load = -load
        for i, l in zip(items, load):
            if l < -0.2:
                i["reverse"] = "?"
                i["evidence"] += (f"; loads {l:.2f} on the scale's first component while most items load positively: "
                                  "reverse-keyed? decide from item wording")


# answers that are not a position on the scale: don't know, refused, not applicable, hard to say
NONSUB = re.compile(r"(don.?t know|do not know|refus|not applicable|\bn/?a\b|no answer|hard to say|can.?t say|"
                    r"prefer not|לא יודע|לא יודעת|אין לי דעה|מסרב|לא רלוונטי|לא רלבנטי|אין תשובה|קשה לומר|"
                    r"trudno powiedzie|odmowa|nie wiem|nie dotyczy|^nd\b|weiß nicht|keine angabe|ne sais pas|ns/nr)", re.I)


def missing_from_labels(r: dict, labels: dict, declared: list, s: pd.Series) -> None:
    """Missing codes from the file's own metadata (a source) and from its value labels (a hypothesis).

    Declared user-missing values (SPSS) are confirmed by the producer. Labelled codes that are not a
    position on the scale - a 'don't know' or 'refusal' label, or a labelled code standing apart above
    a run of consecutive scale codes (1..5 then 7, 8 or 97..99) - are proposed as missing: left in, they
    enter every mean as the scale's most extreme answer.
    """
    codes, notes = [], []
    for d in declared:
        codes.append(str(d))
    if declared:
        notes.append(f"user-missing declared in the file: {', '.join(map(str, declared))}")
    num = {}
    for k, v in labels.items():
        try:
            num[float(k)] = str(v)
        except ValueError:
            pass
    hyp = [k for k, v in num.items() if NONSUB.search(v)]
    subst = sorted(k for k in num if k not in hyp and k >= 0)
    if len(subst) >= 3:                          # a run of consecutive codes is the scale
        run = [subst[0]]
        for k in subst[1:]:
            if k - run[-1] == 1:
                run.append(k)
            elif len(run) >= 3:
                break
            else:
                run = [k]
        if len(run) >= 3:
            hyp += [k for k in num if k > run[-1] + 1 and k not in hyp]
    hyp = [k for k in hyp if to_str(k) not in codes]
    if hyp:
        notes.append("labelled as a non-answer or standing apart from the scale - missing? "
                     + ", ".join(f"{to_str(k)}={num[k][:30]}" for k in sorted(hyp)))
        codes += [to_str(k) for k in sorted(hyp)]
    if not codes:
        return
    r["missing_codes"] = "; ".join(dict.fromkeys(codes))
    r["evidence"] += "; " + "; ".join(notes)
    x = as_number(s.astype(str).str.strip())
    bad = {float(c) for c in codes if re.fullmatch(r"-?\d+(\.\d+)?", c)}
    x = x[~x.isin(bad)].dropna()
    if len(x) and r.get("type") in ("likert", "integer", "numeric"):
        r["min"], r["max"] = to_str(x.min()), to_str(x.max())


def draft(path: str, label: str | None) -> pd.DataFrame:
    L = load_table(path)
    q = L.meta.get("question_text", {})
    labels = L.meta.get("column_labels", {}) or {}
    vlabels = L.meta.get("value_labels", {}) or {}
    mranges = L.meta.get("missing_ranges", {}) or {}
    rows = []
    file_label = label or os.path.basename(path)
    for c in L.df.columns:
        if c == ROWKEY:
            continue
        r = infer_column(c, L.df[c], q.get(c, "") or (labels.get(c) or ""))
        r["source_names"] = f"{file_label}::{c}"
        if c in vlabels:  # SPSS value labels are a source, not a guess
            r["allowed"] = "; ".join(f"{k}={v}" for k, v in vlabels[c].items())
            r["evidence"] += "; value labels read from the .sav file"
        missing_from_labels(r, vlabels.get(c, {}), mranges.get(c, []), L.df[c])
        rows.append(r)
    labelled_families(L.df, rows, q)
    scale_hypotheses(L.df, rows)
    wording_hypotheses(rows)
    return pd.DataFrame(rows, columns=DICT_COLUMNS)


# ------------------------------------------------------------------ import
HEADER_MAP = [
    ("var", r"^(עמודה|משתנה|שם.?משתנה|variable|var|column|name)$"),
    ("item_text", r"(נוסח הפריט$|תוכן העמודה|נוסח|item text|wording|question)"),
    ("source_item", r"(מספר הפריט במקור|source item|item number|original item)"),
    ("source_ref", r"^(מקור|source|reference|citation)$"),
    ("type", r"^(סוג|סוג ערכים|type|data type)$"),
    ("min", r"(מינימ|^min)"), ("max", r"(מקסימ|^max)"),
    ("allowed", r"(ערכים אפשריים|allowed|categories|values|codes)"),
    ("reverse", r"(הפוך|reverse)"),
    ("scale", r"^(שאלון|סולם|scale|instrument)$"),
    ("subscale", r"(היבט|תת.?סולם|subscale|facet|dimension)"),
    ("notes", r"(הערות|notes|comment)"),
    ("unit", r"(יחידה|unit)"),
    ("label", r"(תווית|label)"),
    ("role", r"(תפקיד|role)"),
    ("anchors", r"(עוגני|anchors)"),
]
TYPE_WORDS = [("id", r"(מזהה|identifier|\bid\b)"), ("datetime", r"(תאריך|date|time)"),
              ("likert", r"(ליקרט|likert|סולם|rating)"), ("integer", r"(מספר שלם|integer|int)"),
              ("numeric", r"(מספר|numeric|float|decimal|continuous)"),
              ("categorical", r"(קטגור|categor|nominal|list)"), ("binary", r"(בינארי|binary|yes/no|dichot)"),
              ("text", r"(טקסט|text|string|open)")]


def import_dictionary(path: str, sheet: str | None) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    sh = sheet or xl.sheet_names[0]
    raw = pd.read_excel(xl, sh, dtype=object, header=None)
    raw = raw.map(to_str) if hasattr(raw, "map") else raw.applymap(to_str)
    hdr = raw.iloc[0].tolist()
    mapping = {}
    for j, h in enumerate(hdr):
        for key, pat in HEADER_MAP:
            if key not in mapping and re.search(pat, h.strip(), re.I):
                mapping[key] = j
                break
    if "var" not in mapping:
        raise SystemExit(f"could not find the variable-name column among headers: {hdr}")
    rows = []
    for _, r in raw.iloc[1:].iterrows():
        name = r.iloc[mapping["var"]].strip()
        if not name:
            continue
        row = {c: "" for c in DICT_COLUMNS}
        row["var"] = name
        for key, j in mapping.items():
            if key in DICT_COLUMNS and key != "var":
                row[key] = r.iloc[j].strip()
        typ_text = f"{row.get('type', '')} {r.iloc[mapping['role']] if 'role' in mapping else ''}"
        row["type"] = next((t for t, p in TYPE_WORDS if re.search(p, typ_text, re.I)), row["type"] or "")
        if "role" in mapping and re.search(r"(מזהה|identifier)", r.iloc[mapping["role"]]):
            row["type"] = "id"
        if row["type"] == "numeric" and row["min"] and row["max"]:
            try:
                lo, hi = float(row["min"]), float(row["max"])
                if lo.is_integer() and hi.is_integer() and hi - lo <= 10:
                    row["type"] = "likert"
            except ValueError:
                pass
        rev = row.get("reverse", "")
        row["reverse"] = "yes" if re.search(r"(כן|yes|true|הפוך|^1$)", rev, re.I) else ("no" if rev else "")
        if "anchors" in mapping and r.iloc[mapping["anchors"]].strip():
            row["notes"] = (row["notes"] + " | anchors: " + r.iloc[mapping["anchors"]].strip()).strip(" |")
        row["allowed"] = "; ".join(x.strip() for x in re.split(r"[;,،]", row["allowed"]) if x.strip()) \
            if row["type"] in ("categorical", "binary") else row["allowed"]
        row.update(status="confirmed", evidence=f"imported from {os.path.basename(path)} [{sh}]")
        rows.append(row)
    return pd.DataFrame(rows, columns=DICT_COLUMNS)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft"); d.add_argument("data"); d.add_argument("--out", required=True)
    d.add_argument("--label"); d.add_argument("--lang", default="he")
    i = sub.add_parser("import"); i.add_argument("dictionary"); i.add_argument("--out", required=True)
    i.add_argument("--sheet"); i.add_argument("--lang", default="he")
    a = ap.parse_args()
    v = draft(a.data, a.label) if a.cmd == "draft" else import_dictionary(a.dictionary, a.sheet)
    write_dictionary(a.out, v, lang=a.lang)
    print(f"{len(v)} variables -> {a.out}  "
          f"(hypotheses: {(v['status'] == 'hypothesis').sum()}, confirmed: {(v['status'] == 'confirmed').sum()})")


if __name__ == "__main__":
    main()
