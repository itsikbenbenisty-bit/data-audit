"""Shared helpers for the data-audit skill: loading any tabular file as raw strings,
hashing, row keys, dictionary I/O and bilingual messages.

Design rule: every scan works on the RAW string values first (what the file really
contains); numeric conversion is a separate, explicit step. A value is never silently
coerced, because silent coercion is how text-in-numeric and missing codes disappear.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

ROWKEY = "_row"          # stable key: data-row number in the source file (1-based)
MISSING_TOKENS = {"", "nan", "NaN", "None", "NULL", "null", "NA", "N/A", "#N/A"}

# ---------------------------------------------------------------- messages
MSG = {
    "blocking": {"he": "חוסם", "en": "Blocking"},
    "decision": {"he": "דורש החלטה", "en": "Needs decision"},
    "info": {"he": "לידיעה", "en": "Info"},
    "pass": {"he": "עבר", "en": "PASS"},
    "fail": {"he": "נכשל", "en": "FAIL"},
}


def t(key: str, lang: str) -> str:
    return MSG.get(key, {}).get(lang, key)


# ---------------------------------------------------------------- hashing
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_frame(df: pd.DataFrame) -> str:
    """Content hash of a frame (column order + values), independent of file format."""
    h = hashlib.sha256()
    h.update("\x1f".join(map(str, df.columns)).encode("utf-8"))
    for row in df.astype(str).itertuples(index=False):
        h.update("\x1e".join(row).encode("utf-8"))
    return h.hexdigest()


def row_hashes(df: pd.DataFrame, cols: list[str] | None = None) -> pd.Series:
    """md5 per row; rows with no content at all are left out (they would 'match' everywhere)."""
    cols = cols or [c for c in df.columns if c != ROWKEY]
    filled = df[cols].astype(str).apply(lambda r: "".join(r).strip() != "", axis=1)
    return df.loc[filled, cols].astype(str).agg("\x1e".join, axis=1).map(
        lambda s: hashlib.md5(s.encode("utf-8")).hexdigest())


# ---------------------------------------------------------------- value helpers
def to_str(v: Any) -> str:
    """Raw-faithful string: 3.0 -> '3', NaN -> '', datetimes ISO."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, pd.Timestamp):
        return v.isoformat(sep=" ")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def is_missing(s: str) -> bool:
    return s.strip() in MISSING_TOKENS


def as_number(series: pd.Series) -> pd.Series:
    """Numeric view of a raw string column (strip only; nothing else is 'fixed')."""
    return pd.to_numeric(series.astype(str).str.strip().replace("", None), errors="coerce")


# ---------------------------------------------------------------- loading
@dataclass
class Loaded:
    path: str
    df: pd.DataFrame                       # raw strings, plus ROWKEY
    kind: str                              # csv | xlsx | sav | json
    meta: dict = field(default_factory=dict)


def _detect_encoding(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            pass
    try:  # Hebrew Windows exports
        raw.decode("cp1255")
        return "cp1255"
    except UnicodeDecodeError:
        return "latin-1"


def _qualtrics_header_rows(head: pd.DataFrame) -> int:
    """How many header rows sit above the data.

    Qualtrics csv: row0 = codes, row1 = question text, row2 = {"ImportId": ...}  -> 3
    Qualtrics xlsx and similar: row0 = codes, row1 = question text            -> 2
    A second header row is recognised when, in columns whose later rows are mostly
    numeric, row1 holds non-numeric text (question wording), or when row1 repeats the
    standard Qualtrics labels ("Start Date", "Response ID").
    """
    if len(head) >= 3 and "ImportId" in " ".join(map(to_str, head.iloc[2].tolist())):
        return 3
    if len(head) < 4:
        return 1
    r1 = [to_str(x).strip() for x in head.iloc[1].tolist()]
    if {"Start Date", "Response ID", "Recorded Date"} & set(r1):
        return 2
    body = head.iloc[2:]
    numeric_cols = [j for j in range(head.shape[1])
                    if pd.to_numeric(body.iloc[:, j].map(to_str).replace("", None), errors="coerce").notna().mean() > 0.8]
    if len(numeric_cols) >= 5:
        texty = [j for j in numeric_cols if r1[j] and pd.isna(pd.to_numeric(r1[j], errors="coerce"))]
        if len(texty) / len(numeric_cols) > 0.6:
            return 2
    return 1


def _title_rows(head: pd.DataFrame) -> int:
    """Rows above the real header: a report title, a "refreshed on" line, a blank line.

    The header is the first row that is at least 60% as full as the fullest of the next rows and is
    mostly text that is not a number; everything above it is a title block (janitor's dirty_data.xlsx).
    """
    fill = [sum(1 for x in head.iloc[i].tolist() if to_str(x).strip()) for i in range(len(head))]
    if len(fill) < 3 or max(fill) == 0:
        return 0
    for i in range(min(5, len(head) - 1)):
        full = max(fill[i:i + 6])
        cells = [to_str(x).strip() for x in head.iloc[i].tolist() if to_str(x).strip()]
        texty = sum(1 for c in cells if pd.isna(pd.to_numeric(c, errors="coerce")))
        if fill[i] >= 0.6 * full and cells and texty / len(cells) >= 0.8:
            return i
    return 0


def load_table(path: str, sheet: str | int | None = None) -> Loaded:
    ext = os.path.splitext(path)[1].lower()
    meta: dict = {"file": os.path.basename(path)}
    if ext in (".csv", ".tsv", ".txt"):
        raw = open(path, "rb").read()
        enc = _detect_encoding(raw)
        text = raw.decode(enc)
        try:
            dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
            sep = dialect.delimiter
        except csv.Error:
            sep = "\t" if ext == ".tsv" else ","
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False,
                         header=None)
        skip = _title_rows(df.head(12))
        if skip:
            meta["title_rows"] = [" ".join(to_str(x) for x in df.iloc[i].tolist() if to_str(x).strip()) for i in range(skip)]
            df = df.iloc[skip:].reset_index(drop=True)
        hdr = _qualtrics_header_rows(df.head(12))
        meta.update(encoding=enc, sep=sep, bom=raw.startswith(b"\xef\xbb\xbf"),
                    header_rows=hdr, kind="csv")
        kind = "csv"
    elif ext in (".xlsx", ".xlsm", ".xls"):
        xl = pd.ExcelFile(path)
        meta["sheets"] = xl.sheet_names
        sh = sheet if sheet is not None else xl.sheet_names[0]
        df = pd.read_excel(xl, sh, header=None, dtype=object)
        df = df.map(to_str) if hasattr(df, "map") else df.applymap(to_str)
        skip = _title_rows(df.head(12))
        if skip:
            meta["title_rows"] = [" ".join(to_str(x) for x in df.iloc[i].tolist() if to_str(x).strip()) for i in range(skip)]
            df = df.iloc[skip:].reset_index(drop=True)
        hdr = _qualtrics_header_rows(df.head(12))
        meta.update(sheet=sh, header_rows=hdr, kind="xlsx")
        kind = "xlsx"
    elif ext == ".sav":
        import pyreadstat
        sdf, m = pyreadstat.read_sav(path, apply_value_formats=False, user_missing=True)  # keep the codes as typed
        meta.update(kind="sav", column_labels=dict(zip(m.column_names, m.column_labels)),
                    value_labels={k: {to_str(a): b for a, b in v.items()}
                                  for k, v in m.variable_value_labels.items()},
                    missing_ranges={k: [to_str(d["lo"]) if d["lo"] == d["hi"] else f"{to_str(d['lo'])}..{to_str(d['hi'])}"
                                        for d in v] for k, v in (m.missing_ranges or {}).items()})
        df = sdf.map(to_str) if hasattr(sdf, "map") else sdf.applymap(to_str)
        df.columns = [str(c) for c in df.columns]
        df.insert(0, ROWKEY, range(1, len(df) + 1))
        return Loaded(path, df, "sav", meta)
    elif ext == ".json":
        obj = json.load(open(path, encoding="utf-8"))
        recs = obj
        if isinstance(obj, dict):  # take the largest list of records
            lists = [(k, v) for k, v in obj.items() if isinstance(v, list)]
            if lists:
                k, recs = max(lists, key=lambda kv: len(kv[1]))
                meta["json_key"] = k
            else:
                recs = [obj]
        sdf = pd.json_normalize(recs)
        df = sdf.map(to_str) if hasattr(sdf, "map") else sdf.applymap(to_str)
        df.columns = [str(c) for c in df.columns]
        df.insert(0, ROWKEY, range(1, len(df) + 1))
        meta.update(kind="json")
        return Loaded(path, df, "json", meta)
    else:
        raise ValueError(f"unsupported file type: {path}")

    # header handling for csv/xlsx
    hdr = meta["header_rows"]
    names, seen, dups = [], {}, {}
    for i, x in enumerate(df.iloc[0].tolist()):
        n = to_str(x).strip() or f"__unnamed_{i}"
        if n in seen:                       # same name twice: keep both, never merge them
            seen[n] += 1
            dups.setdefault(n, [n]).append(f"{n}#{seen[n]}")
            n = f"{n}#{seen[n]}"
        else:
            seen[n] = 1
        names.append(n)
    if dups:
        meta["duplicate_columns"] = dups
    if hdr >= 2:
        meta["question_text"] = dict(zip(names, map(to_str, df.iloc[1].tolist())))
    body = df.iloc[hdr:].reset_index(drop=True)
    body.columns = names
    if ROWKEY in body.columns:
        # a version written by versioning.py already carries the stable key of the SOURCE rows: keep it.
        # Re-numbering would break the link to the raw file (and inserting a second _row raised; 17.11.26)
        body[ROWKEY] = as_number(body[ROWKEY]).astype(int)
        body = body[[ROWKEY] + [c for c in body.columns if c != ROWKEY]]
        meta["row_key_from_file"] = True
    else:
        body.insert(0, ROWKEY, range(1, len(body) + 1))
    return Loaded(path, body, kind, meta)


# ---------------------------------------------------------------- dictionary
DICT_COLUMNS = [
    "var", "source_names", "label", "item_text", "source_item", "source_ref", "type",
    "unit", "min", "max", "allowed", "text_map", "missing_codes", "scale", "subscale",
    "reverse", "required", "available_in", "condition", "privacy", "status", "evidence",
    "notes",
]
DICT_COLUMNS_HE = [
    "משתנה", "שמות במקורות", "תווית", "נוסח הפריט", "מספר במקור", "מקור", "סוג",
    "יחידה", "מינימום", "מקסימום", "ערכים מותרים", "מיפוי טקסט לקוד", "קודי חסר", "סולם",
    "תת-סולם", "הפוך", "חובה", "זמין ב", "תנאי", "פרטיות", "מעמד", "ראיה", "הערות",
]
RULE_COLUMNS = ["rule_id", "kind", "expression", "severity", "description", "status"]


def split_list(s: Any) -> list[str]:
    s = to_str(s)
    return [x.strip() for x in s.split(";") if x.strip()] if s else []


def parse_pairs(s: Any, sep: str = "=") -> dict[str, str]:
    out = {}
    for part in split_list(s):
        if sep in part:
            a, b = part.split(sep, 1)
            out[a.strip()] = b.strip()
    return out


def read_dictionary(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (variables, rules). Accepts the skill's own xlsx (English keys in row 1)."""
    xl = pd.ExcelFile(path)
    v = pd.read_excel(xl, xl.sheet_names[0], dtype=object)
    if list(v.columns[:1]) != ["var"] and "var" in v.iloc[0].tolist():
        v.columns = v.iloc[0]
        v = v.iloc[1:]
    v = v.map(to_str) if hasattr(v, "map") else v.applymap(to_str)
    v = v[v["var"] != ""].reset_index(drop=True)
    if "rules" in xl.sheet_names:
        r = pd.read_excel(xl, "rules", dtype=object)
        r = r.map(to_str) if hasattr(r, "map") else r.applymap(to_str)
    else:
        r = pd.DataFrame(columns=RULE_COLUMNS)
    return v, r


def write_dictionary(path: str, variables: pd.DataFrame, rules: pd.DataFrame | None = None,
                     lang: str = "he") -> None:
    variables = variables.reindex(columns=DICT_COLUMNS).fillna("")
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        variables.to_excel(w, sheet_name="variables", index=False)
        (rules if rules is not None else pd.DataFrame(columns=RULE_COLUMNS)).to_excel(
            w, sheet_name="rules", index=False)
        pd.DataFrame({"key": DICT_COLUMNS, "עברית": DICT_COLUMNS_HE}).to_excel(
            w, sheet_name="legend", index=False)
    try:  # cosmetic: RTL sheets + frozen header
        from openpyxl import load_workbook
        wb = load_workbook(path)
        for ws in wb.worksheets:
            ws.freeze_panes = "B2"
            if lang == "he":
                ws.sheet_view.rightToLeft = True
        wb.save(path)
    except Exception:
        pass


def resolve_source(variables: pd.DataFrame, file_label: str, columns: list[str]) -> dict[str, str]:
    """Map canonical var -> column name in this file (via source_names 'file::col' or var)."""
    out = {}
    for _, r in variables.iterrows():
        names = split_list(r.get("source_names", ""))
        hit = None
        for n in names:
            f, _, c = n.rpartition("::")
            if (not f or f == file_label) and c in columns:
                hit = c
                break
        if hit is None and r["var"] in columns:
            hit = r["var"]
        if hit is not None:
            out[r["var"]] = hit
    return out


def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def norm_label(s: str) -> str:
    """Normalisation used only to DETECT label variants (never to silently fix them)."""
    s = re.sub(r"[־\-_/.,;:'\"״׳]+", " ", str(s))   # maqaf, hyphen, underscore, punctuation, geresh
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s
