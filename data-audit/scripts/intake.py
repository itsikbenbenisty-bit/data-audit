"""Layer 0 — intake and provenance.

Usage:
  python intake.py <file-or-folder> [...] --out AUDIT_DIR [--lang he|en]

For every tabular file (csv/tsv/xlsx/sav/json): fingerprint (sha256), size, rows, columns,
header signature, encoding/header rows, candidate ID columns, candidate identifying
columns, date span, and claims made by the file NAME. Then compares files with each other:
exact copies, same content in another format, row subsets, shared/different column
signatures, and names that contradict content ("anonymous" files holding IDs, "75
respondents" files holding 62).

Writes AUDIT_DIR/intake.json and AUDIT_DIR/intake_<lang>.md. Never modifies a source file.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import ROWKEY, as_number, load_table, row_hashes, sha256_file, sha256_frame, write_json  # noqa: E402

TABULAR = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".sav", ".json")
ID_NAME = re.compile(r"(^id$|response.?id|participant|respondent|_id$|^id_|personal|מספר.?אישי|^מ\.?א\.?$|ת\.?ז|תעודת|employee.?(id|no)|"
                     r"מספר.?עובד|participant|subject|respondent|מספר.?משיב)", re.I)
PII_NAME = re.compile(r"(email|e-mail|mail|phone|טלפון|נייד|ip.?address|latitude|longitude|"
                      r"recipient|first.?name|last.?name|^name$|שם.?(פרטי|משפחה|מלא)|כתובת|address|"
                      r"מספר.?אישי|ת\.?ז|תעודת.?זהות)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:^|\D)0(?:5\d|[2-9])-?\d{7}(?:\D|$)")
PERSON_REF = re.compile(r"(הערכת|משוב (?:על|ל)|evaluation of|feedback (?:on|for|to))\s+[^\s\-–(:]{2,}\s+[^\s\-–(:]{2,}", re.I)
TITLED = re.compile(r"(?<![\w])(?:Dr\.?|Prof\.?|Mr\.?|Mrs\.?|Ms\.?|ד\"ר|פרופ'|רס\"ן|סא\"ל|אל\"ם|סרן|המפקד|מ\"פ|מפק\"ץ)\s+[A-Z\u05d0-\u05ea][\w\u05d0-\u05ea'\"]{1,15}")
NOT_ID = re.compile(r"(duration|seconds|progress|latitude|longitude|_c$|_cen$|centered|score|mean|value)", re.I)
ANON_NAME = re.compile(r"(anon|אנונימ|de-?ident|מוסתר)", re.I)
N_IN_NAME = re.compile(r"(\d{2,5})\s*(משיבים|respondents|participants|נבדקים|rows)|N\s*=\s*(\d{2,5})", re.I)
DATE_NAME = re.compile(r"(date|time|תאריך|זמן|start|end|recorded)", re.I)


def guess_id_columns(df: pd.DataFrame, meta: dict) -> list[dict]:
    out = []
    qtext = meta.get("question_text", {})
    labels = meta.get("column_labels", {}) or {}
    n = len(df)
    for c in df.columns:
        if c == ROWKEY:
            continue
        s = df[c].astype(str).str.strip()
        nonempty = s[s != ""]
        if len(nonempty) < max(5, 0.5 * n) and not (n < 5 and len(nonempty) == n and ID_NAME.search(f"{c}")):
            continue
        text = f"{c} {qtext.get(c, '')} {labels.get(c, '') or ''}"
        if NOT_ID.search(str(c)):
            continue
        digits = nonempty.str.replace(r"\.0$", "", regex=True).str.fullmatch(r"\d{4,10}").mean()
        uniq = nonempty.nunique() / len(nonempty)
        by_name = bool(ID_NAME.search(text))
        keyish = bool(re.search(r"(response.?id|participant|respondent|subject.?id)", str(c), re.I))
        if (by_name and uniq > 0.6 and (digits > 0.3 or keyish)) or (digits > 0.8 and uniq > 0.9):
            out.append({"column": c, "by_name": by_name, "share_digits": round(float(digits), 3),
                        "uniqueness": round(float(uniq), 3), "text": text[:120]})
    return out


def guess_pii(df: pd.DataFrame, meta: dict) -> list[dict]:
    out = []
    qtext = meta.get("question_text", {})
    labels = meta.get("column_labels", {}) or {}
    for c in df.columns:
        if c == ROWKEY:
            continue
        text = f"{c} {qtext.get(c, '')} {labels.get(c, '') or ''}"
        s = df[c].astype(str)
        filled = (s.str.strip() != "").mean()
        if filled < 0.05:              # an identifying FIELD that holds nothing is not a leak
            continue
        reasons = [f"{filled:.0%} filled"]
        if PII_NAME.search(text):
            reasons.append("name/label")
        if PERSON_REF.search(text) or TITLED.search(f"{qtext.get(c, '')} {labels.get(c, '') or ''}"):
            reasons.append("question text seems to name a person (third-party PII) - check")
        sample = s[s.str.strip() != ""].head(500)
        if len(sample):
            if sample.str.contains(EMAIL).mean() > 0.2:
                reasons.append("email values")
            if sample.str.contains(PHONE).mean() > 0.2:
                reasons.append("phone values")
        if len(reasons) > 1:
            out.append({"column": c, "reasons": reasons, "text": text[:120]})
    return out


def date_span(df: pd.DataFrame) -> dict:
    span = {}
    for c in df.columns:
        if c == ROWKEY or not DATE_NAME.search(str(c)):
            continue
        s = df[c].astype(str).str.strip()
        s = s[s != ""]
        if s.empty:
            continue
        num = as_number(s)
        if num.notna().mean() > 0.9 and num.between(20000, 60000).mean() > 0.9:
            span[c] = {"excel_serial": True, "min": float(num.min()), "max": float(num.max())}
            continue
        d = pd.to_datetime(s, errors="coerce", dayfirst=False)
        if d.notna().mean() > 0.8:
            span[c] = {"min": str(d.min()), "max": str(d.max())}
    return span


def describe_all(path: str) -> list[dict]:
    """Every sheet that holds a table is described: data hide in 'גיליון2' too."""
    first = describe(path)
    out = [first]
    sheets = first.get("meta", {}).get("sheets") or []
    for sh in sheets[1:]:
        r = describe(path, sheet=sh)
        if "rows" in r and r["rows"] >= 5 and r["cols"] >= 3:
            r["file"] = f"{first['file']}::{sh}"
            r["sheet_of"] = first["file"]
            out.append(r)
    return out


def describe(path: str, sheet=None) -> dict:
    rec = {"path": path, "file": os.path.basename(path), "sha256": sha256_file(path),
           "bytes": os.path.getsize(path),
           "mtime": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")}
    try:
        L = load_table(path, sheet)
    except Exception as e:  # unreadable is itself a finding
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec
    df, meta = L.df, L.meta
    cols = [c for c in df.columns if c != ROWKEY]
    empty_rows = int((df[cols].astype(str).apply(lambda r: "".join(r).strip() == "", axis=1)).sum())
    rec.update(
        kind=L.kind, rows=len(df), cols=len(cols), empty_rows=empty_rows,
        header_signature=hashlib.md5("\x1f".join(cols).encode("utf-8")).hexdigest(),
        header_set_signature=hashlib.md5("\x1f".join(sorted(cols)).encode("utf-8")).hexdigest(),
        content_sha256=sha256_frame(df[cols]),
        meta={k: v for k, v in meta.items() if k not in ("question_text", "column_labels", "value_labels")},
        duplicate_columns=meta.get("duplicate_columns", {}),
        id_candidates=guess_id_columns(df, meta), pii_candidates=guess_pii(df, meta),
        date_span=date_span(df), columns=cols)
    rec["_rowhashes"] = set(row_hashes(df, cols))
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=False)
            ws = wb[sheet] if sheet else wb.worksheets[0]
            rec["merged_cells"] = len(ws.merged_cells.ranges)
        except Exception:
            pass
    skip = {i["column"] for i in rec["id_candidates"]} | {c for c in cols if DATE_NAME.search(str(c))
                                                           or NOT_ID.search(str(c))
                                                           or re.search(r"(ip.?address|response.?id|location|recipient|status|finished)", str(c), re.I)}
    rec["_content_cols"] = [c for c in cols if c not in skip]
    rec["_content_df"] = df[rec["_content_cols"]].astype(str)
    seqs = []
    for c in cols:
        v = as_number(df[c].astype(str).str.strip())
        if (len(v) >= 10 and v.notna().all() and (v.diff().dropna() > 0).all() and v.iloc[0] in (0, 1)
                and v.max() <= 2 * len(v) and (v == v.round()).all()):
            seqs.append(c)
    rec["_sequences"] = seqs
    rec["_idvals"] = {}
    for c in [i["column"] for i in rec["id_candidates"]]:
        v = df[c].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        v = v[v.str.fullmatch(r"\d{5,10}") & ~v.str.fullmatch(r"(\d)\1+")]
        if len(v) >= 5:
            rec["_idvals"][c] = set(v.str.lstrip("0"))
    rec["id_columns_used_for_linkage"] = list(rec["_idvals"])
    return rec


def name_claims(rec: dict) -> list[dict]:
    out = []
    m = N_IN_NAME.search(rec["file"])
    if m and "rows" in rec:
        claimed = int(next(g for g in m.groups() if g and g.isdigit()))
        real = rec["rows"] - rec.get("empty_rows", 0)
        if claimed != real:
            out.append({"check": "name_count_mismatch", "claimed": claimed, "rows_with_data": real})
    idc = [i["column"] for i in rec.get("id_candidates", []) if i["by_name"] or i["share_digits"] > 0.8]
    if ANON_NAME.search(rec["file"]) and (rec.get("pii_candidates") or idc):
        out.append({"check": "anonymous_name_but_identifiers",
                    "columns": ([p["column"] for p in rec["pii_candidates"]] + idc)[:10]})
    if rec.get("duplicate_columns"):
        out.append({"check": "duplicate_column_names", "columns": list(rec["duplicate_columns"])[:30],
                    "n": len(rec["duplicate_columns"])})
    if rec.get("merged_cells"):
        out.append({"check": "merged_cells", "ranges": rec["merged_cells"],
                    "note": "merged Excel cells: the value sits in the first cell only; the rest read as empty"})
    sheets = rec.get("meta", {}).get("sheets") or []
    if len(sheets) > 1:
        out.append({"check": "extra_sheets", "read": rec["meta"].get("sheet", sheets[0]), "others": sheets[1:],
                    "note": "other sheets may hold pivots, a codebook or an older version: open them"})
    for c, sp in rec.get("date_span", {}).items():
        if sp.get("excel_serial"):
            out.append({"check": "dates_as_excel_serials", "column": c})
    return out


def files_of(c: dict) -> list[str]:
    return c.get("files") or [c.get("subset", ""), c.get("of", ""), c.get("file", "")]


def compare(recs: list[dict]) -> list[dict]:
    out = []
    ok = [r for r in recs if "rows" in r]
    id_names = {i["column"] for r in ok for i in r.get("id_candidates", [])}
    seq_names = {c for r in ok for c in r.get("_sequences", [])}
    by_sha = {}
    for r in recs:
        if not r.get("sheet_of"):
            by_sha.setdefault(r["sha256"], []).append(r["file"])
    for files in by_sha.values():
        if len(files) > 1:
            out.append({"check": "identical_files", "files": files})
    by_content = {}
    for r in ok:
        by_content.setdefault(r["content_sha256"], []).append(r)
    for group in by_content.values():
        shas = {r["sha256"] for r in group}
        if len(group) > 1 and len(shas) > 1:
            out.append({"check": "same_content_different_file", "files": [r["file"] for r in group],
                        "note": "cell-identical data; the files differ in format or extra sheets"})
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            if a["content_sha256"] == b["content_sha256"]:
                continue
            if a["header_set_signature"] == b["header_set_signature"]:
                inter = len(a["_rowhashes"] & b["_rowhashes"])
                small, big = (a, b) if len(a["_rowhashes"]) <= len(b["_rowhashes"]) else (b, a)
                if inter and inter == len(small["_rowhashes"]):
                    out.append({"check": "row_subset", "subset": small["file"], "of": big["file"],
                                "rows": inter})
                elif inter:
                    out.append({"check": "row_overlap", "files": [a["file"], b["file"]],
                                "shared_rows": inter})
                else:
                    cc = sorted((set(a["_content_cols"]) & set(b["_content_cols"])) - id_names - seq_names)
                    ha = set(row_hashes(a["_content_df"], cc)) if cc else set()
                    hb = set(row_hashes(b["_content_df"], cc)) if cc else set()
                    ci, smallc = len(ha & hb), min(len(ha), len(hb))
                    if ci and ci >= 0.9 * smallc:
                        sm, bg = (a, b) if len(ha) <= len(hb) else (b, a)
                        out.append({"check": "content_subset", "subset": sm["file"], "of": bg["file"], "rows": ci,
                                    "of_rows": smallc,
                                    "note": "the answers are the same rows; identifiers, dates or metadata were changed "
                                            "(e.g. IDs replaced, dates converted): a working copy, not new data"})
            else:
                sa, sb = set(a["columns"]), set(b["columns"])
                jac = len(sa & sb) / len(sa | sb)
                if 0.5 <= jac < 1:      # versions of one instrument: report what differs
                    out.append({"check": "instrument_versions", "files": [a["file"], b["file"]],
                                "shared_columns": len(sa & sb), "only_a": sorted(sa - sb)[:15],
                                "only_b": sorted(sb - sa)[:15], "jaccard": round(jac, 2)})
    for r in ok:
        for c in r.get("_sequences", []):
            if c in id_names:
                out.append({"check": "id_replaced_by_row_number", "file": r["file"], "column": c,
                            "note": f"'{c}' is an identifier in other files but here holds 1..n: linkage to other files is lost"})
    # identifier overlap across files (any ID-like column, names may differ: Q72 / מ.א / Q290)
    links, few, linked = [], [], set()
    for c in out:
        if c["check"] in ("same_content_different_file", "row_subset", "content_subset"):
            linked.update(f for f in files_of(c) if f)
    done = {frozenset(c.get("files", [c.get("subset"), c.get("of")])) for c in out
            if c["check"] in ("same_content_different_file", "row_subset")}
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            if frozenset([a["file"], b["file"]]) in done or a["content_sha256"] == b["content_sha256"]:
                continue
            best = None
            for ca, va in a.get("_idvals", {}).items():
                for cb, vb in b.get("_idvals", {}).items():
                    k = len(va & vb)
                    if k and (best is None or k > best[0]):
                        best = (k, ca, cb, len(va), len(vb))
            if not best:
                continue
            k, ca, cb, na, nb = best
            if k < 10 or k / min(na, nb) < 0.1:
                raw_a = (a.get("meta", {}).get("header_rows") or 1) >= 2
                raw_b = (b.get("meta", {}).get("header_rows") or 1) >= 2
                if k < 10 and min(na, nb) >= 20 and raw_a and raw_b:
                    few.append({"files": [a["file"], b["file"]], "columns": [ca, cb], "shared_ids": k,
                                "ids_a": na, "ids_b": nb})
                continue
            linked.update([a["file"], b["file"]])
            sa, sb = set(a["columns"]), set(b["columns"])
            jac = len(sa & sb) / len(sa | sb)
            small = a if na <= nb else b
            kind = "id_subset" if k / min(na, nb) >= 0.95 and jac >= 0.8 else "id_linkage"
            (out if kind == "id_subset" else links).append({"check": kind, "files": [a["file"], b["file"]], "columns": [ca, cb],
                        "shared_ids": k, "ids_a": na, "ids_b": nb, "columns_jaccard": round(jac, 2),
                        "note": (f"every ID of {small['file']} is in the other file and the columns match: "
                                 "probably a working copy or an earlier export, not new data") if kind == "id_subset"
                        else "shared identifiers: a linkage key (waves, auxiliary files) or overlapping samples"})
    for r in ok:
        for c in r.get("claims", []):
            if c["check"] != "anonymous_name_but_identifiers":
                continue
            hits = []
            for o in ok:
                if o is r or ANON_NAME.search(o["file"]) or o.get("sheet_of") == r["file"] or r.get("sheet_of") == o["file"]:
                    continue
                k = max((len(va & vb) for va in r.get("_idvals", {}).values() for vb in o.get("_idvals", {}).values()), default=0)
                if k >= 5:
                    raw = (o.get("meta", {}).get("header_rows") or 1) >= 2   # straight from the survey platform
                    hits.append({"file": o["file"], "shared_ids": k, "raw_export": raw})
            c["matches_named_files"] = sorted(hits, key=lambda x: (-x["raw_export"], -x["shared_ids"]))[:5]
            if any(h["raw_export"] for h in hits):
                c["verdict"] = "confirmed: the same identifiers appear in a raw export - the file is NOT anonymous"
            elif hits:
                c["verdict"] = ("codes shared only with other working files, not with any raw export: probably "
                                "pseudonyms - verify with the person who made them")
            else:
                c["verdict"] = "identifier-like codes that match no other file: perhaps pseudonyms - verify"
    if few:
        out.append({"check": "few_shared_ids", "pairs": few,
                    "note": "a handful of identical IDs between otherwise unrelated files: a typo, a junk ID, a person "
                            "in two cohorts, or a test entry - check each one"})
    unlinked = [r["file"] for r in ok if len(r.get("_idvals", {})) and max(len(v) for v in r["_idvals"].values()) >= 10
                and r["file"] not in linked and not r.get("sheet_of")]
    if unlinked:
        out.append({"check": "unlinked_files", "files": unlinked,
                    "note": "these files have identifiers but share them with no other file: a separate group, "
                            "a different ID scheme, or a missing partner file"})
    if links:
        out.append({"check": "id_linkage_table", "pairs": sorted(links, key=lambda x: -x["shared_ids"]),
                    "note": "pairs of files that share identifiers; use it to choose linkage keys and to spot copies"})
    sig = {}
    for r in ok:
        sig.setdefault(r["header_set_signature"], []).append(r["file"])
    out.append({"check": "column_signature_groups", "groups": [g for g in sig.values()]})
    return out


def render_md(recs, cross, lang):
    he = lang == "he"
    L = []
    L.append("# " + ("קליטה ומקור (שכבה 0)" if he else "Intake and provenance (layer 0)"))
    L.append("")
    hdr = ("| קובץ | סוג | שורות | עמודות | שורות ריקות | עמודות מזהה אפשריות | מידע מזהה | sha256 (12) |"
           if he else "| File | Kind | Rows | Cols | Empty rows | ID candidates | Identifying | sha256 (12) |")
    L += [hdr, "|---|---|---|---|---|---|---|---|"]
    for r in recs:
        if "error" in r:
            L.append(f"| {r['file']} | ⚠ {r['error']} | | | | | | {r['sha256'][:12]} |")
            continue
        ids = ", ".join(x["column"] for x in r["id_candidates"][:3])
        pii = ", ".join(x["column"] for x in r["pii_candidates"][:3])
        L.append(f"| {r['file']} | {r['kind']} | {r['rows']} | {r['cols']} | {r['empty_rows']} | {ids} | {pii} | {r['sha256'][:12]} |")
    L.append("")
    L.append("## " + ("ממצאים" if he else "Findings"))
    for r in recs:
        for c in r.get("claims", []):
            L.append(f"- **{r['file']}** — `{c['check']}`: {c}")
    for c in cross:
        if c["check"] == "id_linkage_table":
            L.append("- `id_linkage_table`: " + ("זוגות קבצים עם מזהים משותפים" if he else "file pairs sharing identifiers"))
            L.append("")
            L.append("| " + (" | ".join(["קובץ א", "קובץ ב", "עמודות", "מזהים משותפים", "ב-א", "ב-ב"]) if he
                             else " | ".join(["File A", "File B", "Columns", "Shared IDs", "IDs in A", "IDs in B"])) + " |")
            L.append("|---|---|---|---|---|---|")
            for p in c["pairs"]:
                L.append(f"| {p['files'][0]} | {p['files'][1]} | {' / '.join(p['columns'])} | {p['shared_ids']} | {p['ids_a']} | {p['ids_b']} |")
            L.append("")
        elif c["check"] == "column_signature_groups":
            L.append(f"- `column_signature_groups`: {len(c['groups'])} " + ("קבוצות כותרות שונות" if he else "distinct header sets"))
            for g in c["groups"]:
                L.append(f"    - {', '.join(g)}")
        else:
            L.append(f"- `{c['check']}`: {c}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    a = ap.parse_args()
    files = []
    for p in a.paths:
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                files += [os.path.join(root, f) for f in fs if f.lower().endswith(TABULAR)
                          and not f.startswith("~$")]
        else:
            files.append(p)
    os.makedirs(a.out, exist_ok=True)
    recs = []
    for f in sorted(files):
        for r in describe_all(f):
            r["claims"] = name_claims(r) if "rows" in r else []
            recs.append(r)
            print(f"  {r['file']}: " + (r.get("error") or f"{r['rows']}x{r['cols']}"), file=sys.stderr)
    cross = compare(recs)
    for r in recs:
        for k in ("_rowhashes", "_idvals", "_content_df", "_content_cols", "_sequences"):
            r.pop(k, None)
    write_json(os.path.join(a.out, "intake.json"), {"files": recs, "cross": cross})
    open(os.path.join(a.out, f"intake_{a.lang}.md"), "w", encoding="utf-8").write(render_md(recs, cross, a.lang))
    print(os.path.join(a.out, f"intake_{a.lang}.md"))


if __name__ == "__main__":
    main()
