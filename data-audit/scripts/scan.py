"""Layers 1-4 — scan a data file against its dictionary and emit a findings table.

Usage:
  python scan.py DATA --dict dict.xlsx --out AUDIT_DIR [--label NAME] [--id COL]
                 [--window 2026-10-25,2026-11-05] [--lang he|en]

Layer 1  structure      empty rows/cols, duplicate or unnamed columns, header residue,
                         test rows, dictionary coverage, constant columns
Layer 2  identity       missing / junk / duplicate IDs (identical vs conflicting),
                         duplicate rows without an ID, near-duplicate respondents
Layer 3  values         type, range, allowed set, label variants, text->code answers,
                         missing codes, dates outside the collection window,
                         partial responses and blocks abandoned midway
Layer 4  cross-field    dictionary conditions (skip logic, required) and rules sheet

Every finding: id, layer, check, severity (blocking / decision / info), variable,
rows (the stable _row keys), evidence, and the fix options from references/fixes.md.
Nothing is changed. Findings are proposals; the researcher decides.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import (ROWKEY, as_number, load_table, norm_label, parse_pairs,  # noqa: E402
                       read_dictionary, resolve_source, split_list, to_str, write_json)

NUMERIC_TYPES = {"numeric", "integer", "likert", "binary"}
TEST_TEXT = re.compile(r"^\s*(בדיקה|בדיקת|test|testing|טסט|ניסיון|dummy|xxx+|asdf|qwer)\b", re.I)
JUNK_ID = re.compile(r"^(0+|1+|9+|12345\d*|123)$")

OPTIONS = {
    "empty_rows": ["remove rows (log them in the N-flow)"],
    "duplicate_column_names": ["rename by position after reading the item text", "never select these columns by name"],
    "unnamed_columns": ["identify from the questionnaire", "drop if an export artifact"],
    "header_residue": ["remove the residue rows"],
    "test_rows": ["remove the test rows", "keep if they are real respondents (verify with the collector)"],
    "not_in_dictionary": ["add to the dictionary (source?)", "drop as export artifact"],
    "missing_from_data": ["check another file/version", "correct the dictionary"],
    "missing_id": ["drop rows (cannot be linked)", "keep for cross-sectional analyses only"],
    "id_length_mismatch": ["check against the source of IDs (which identifier was asked for?)", "set to missing and re-link manually", "keep (another valid ID form) and record the mapping"],
    "junk_id": ["set ID to missing and keep the row", "recover the ID from another file", "drop the row"],
    "duplicate_id_identical": ["keep one copy"],
    "duplicate_id_conflicting": ["keep the most complete row", "keep the earliest submission", "keep the latest submission", "flag, keep all, exclude from linked analyses"],
    "duplicate_rows": ["keep one copy"],
    "duplicate_empty_records": ["set to missing", "keep; report"],
    "special_value": ["set to missing (logged)"],
    "date_mixed_excel_serials": ["convert the serials to dates (origin 1899-12-30), logged"],
    "balance_hypothesis": ["confirm the identity with the data's owner and add it as a rule", "not an identity: ignore"],
    "same_name_twice": ["one person, several rows: aggregate to the person", "different people: keep", "ask the source"],
    "negative_in_positive": ["check against the source", "set to missing only by a rule set in advance"],
    "near_duplicates": ["inspect: same person under two IDs?", "keep both"],
    "non_numeric": ["recode with a documented text->code map", "set to missing"],
    "text_answers": ["recode with the dictionary text_map"],
    "out_of_range": ["set to missing", "correct from the source document", "treat as a missing code and document it"],
    "missing_codes_present": ["set the codes to missing before any computation"],
    "non_integer": ["check the source; round only with a documented reason", "set to missing"],
    "label_variant": ["map to the canonical label in the dictionary"],
    "not_allowed": ["map to an allowed value (source?)", "set to missing", "add to the dictionary (source?)"],
    "date_order_ambiguous": ["confirm day-first or month-first with the researcher / export settings"],
    "date_order_mixed": ["split by source and parse each with its own order", "set the conflicting ones to missing"],
    "scale_size_mismatch": ["find the missing item in the export or translation", "fix the scale assignment",
                            "score with a documented shortened key (as for a translated scale that dropped an item)"],
    "extreme_value": ["check against the source", "keep (a true extreme)", "set to missing only by a rule set in advance"],
    "pair_disagreement": ["check whether the columns were swapped in these rows", "keep both; report"],
    "bad_date": ["parse with an explicit format", "set to missing"],
    "outside_window": ["exclude (not part of the collection)", "keep and document (window was wrong)"],
    "partial_response": ["flag only", "exclude below a completeness threshold set in advance"],
    "abandoned_block": ["scale scores for that block become missing (rule set in advance)", "exclude the row"],
    "skip_logic_violation": ["set to missing (should not have been asked)", "check the routing in the survey"],
    "required_missing": ["leave missing", "recover from source"],
    "rule_violation": ["inspect rows; decide per rule"],
    "constant_column": ["drop", "keep (by design)"],
    "straightlining_single": ["report the share; no exclusion on one scale"],
    "routed_blocks": ["confirm the routing against the survey flow"],
    "straightlining": ["flag only; report the share", "exclude by a rule set in advance (e.g. several scales)", "check against reversed items: identical answers contradict them"],
    "item_missing": ["apply the scale's missing-item rule (set in advance)", "report per variable"],
    "empty_column": ["drop", "check the export"],
}


def _il_id_ok(x: str) -> bool:
    """Israeli national ID check digit (9 digits, left-padded)."""
    if not x.isdigit() or len(x) > 9 or len(x) < 5:
        return False
    x = x.zfill(9)
    tot = sum((d if d < 10 else d - 9) for d in (int(c) * (1 + i % 2) for i, c in enumerate(x)))
    return tot % 10 == 0


MERGE_CHECKS = {"abandoned_block", "text_answers", "non_numeric", "missing_codes_present", "empty_column", "constant_column",
                "out_of_range", "non_integer"}


def merge_repeats(items: list[dict]) -> list[dict]:
    """One finding per pattern, not per column: the same recode on 40 items is one decision."""
    out, groups = [], {}
    for f in items:
        if f["check"] not in MERGE_CHECKS:
            out.append(f)
            continue
        if f["check"] in ("abandoned_block",):   # one decision, broken down by scale
            sig = (f["check"], f["severity"])
            if sig in groups:
                g = groups[sig]
                g["by_scale"][f["var"]] = f["count"]
                g["vars"].append(f["var"]); g["cells"] += f["count"]
                g["_rows"].update(f["rows"]); g["_ids"].update(f.get("ids", []))
                if "values" in f:
                    g.setdefault("values_by_scale", {})[f["var"]] = f["values"]
            else:
                g = dict(f, vars=[f["var"]], cells=f["count"], _rows=set(f["rows"]), _ids=set(f.get("ids", [])),
                         by_scale={f["var"]: f["count"]})
                if "values" in f:
                    g["values_by_scale"] = {f["var"]: g.pop("values")}
                groups[sig] = g
                out.append(g)
            continue
        if f["check"] == "non_numeric":         # free text in a family of items (Q80_1..Q80_4): one finding
            m = re.match(r"^(.*?)[_\-.]?\d{1,3}$", f["var"])
            sig = (f["check"], f["severity"], m.group(1) if m and m.group(1) else f["var"])
        else:
            sig = (f["check"], f["severity"], tuple(sorted(map(str, (f.get("examples") or {}))))
                   if isinstance(f.get("examples"), dict) else f["evidence"])
        if sig in groups:
            g = groups[sig]
            g["vars"].append(f["var"])
            g["cells"] += f["count"]
            g["_rows"].update(f["rows"])
            g["_ids"].update(f.get("ids", []))
        else:
            g = dict(f, vars=[f["var"]], cells=f["count"], _rows=set(f["rows"]), _ids=set(f.get("ids", [])))
            groups[sig] = g
            out.append(g)
    for i, f in enumerate(out):
        if "_rows" in f:
            rows = sorted(f.pop("_rows")); ids = sorted(f.pop("_ids"))
            if "by_scale" in f and len(f["vars"]) > 1:
                f["var"] = ", ".join(f["vars"][:12]) + (f" (+{len(f['vars']) - 12})" if len(f["vars"]) > 12 else "")
                f["evidence"] = (("all items of a block empty while others were answered" if f["check"] == "abandoned_block"
                                  else "the same answer on every item of a scale")
                                 + f" - in {len(f['vars'])} scales; rows per scale: {f['by_scale']}")
            elif len(f["vars"]) > 1:
                f["var"] = ", ".join(f["vars"][:12]) + (f" (+{len(f['vars']) - 12})" if len(f["vars"]) > 12 else "")
                f["evidence"] = f"{f['evidence']} - same pattern in {len(f['vars'])} variables, {f['cells']} cells"
            f["count"], f["rows"] = len(rows), rows[:300]
            if ids:
                f["ids"] = ids[:50]
        f["id"] = f"F{i + 1:03d}"
    return out


class Findings:
    def __init__(self, ids: pd.Series | None):
        self.items, self.ids = [], ids

    def add(self, layer, check, severity, var, rows, evidence, **extra):
        rows = [int(r) for r in rows]
        f = {"id": f"F{len(self.items) + 1:03d}", "layer": layer, "check": check, "severity": severity,
             "var": var, "count": len(rows), "rows": rows[:300], "evidence": evidence,
             "options": OPTIONS.get(check, []), "status": "open"}
        if self.ids is not None and rows:
            f["ids"] = [to_str(self.ids.get(r, "")) for r in rows[:50]]
        f.update(extra)
        self.items.append(f)


def scan(path, dict_path, label, id_col, window):
    L = load_table(path)
    df = L.df.set_index(ROWKEY, drop=False)
    cols = [c for c in L.df.columns if c != ROWKEY]
    file_label = label or os.path.basename(path)
    if dict_path:
        V, R = read_dictionary(dict_path)
    else:
        from profile_dict import draft
        V, R = draft(path, file_label), pd.DataFrame()
    vmap = resolve_source(V, file_label, cols)                  # var -> column
    V = V.set_index("var")
    if not id_col:
        ids = [v for v in vmap if V.loc[v, "type"] == "id"]
        id_col = vmap[ids[0]] if ids else None
    F = Findings(df[id_col] if id_col else None)
    raw = df[cols].astype(str)
    blank = raw.apply(lambda c: c.str.strip() == "")
    meta_like = {vmap[v] for v in vmap if V.loc[v, "type"] in ("id", "datetime")}
    content_cols = [c for c in cols if c not in meta_like] or cols

    # ---------------------------------------------------------------- layer 1
    rows_empty = blank[content_cols].all(axis=1)
    if rows_empty.any():
        only_meta = rows_empty & ~blank.all(axis=1)
        F.add(1, "empty_rows", "blocking", "", df.index[rows_empty],
              "no answers at all" + (f" ({int(only_meta.sum())} of them carry only an ID/timestamp)" if only_meta.any() else ""))
    dup_names = list(L.meta.get("duplicate_columns", {}))
    if dup_names:
        F.add(1, "duplicate_column_names", "blocking", ", ".join(dup_names), [],
              "the same column name appears more than once (kept as NAME#2 ...): selecting by name is ambiguous; identify each copy by its item text",
              copies=L.meta.get("duplicate_columns"))
    unnamed = [c for c in cols if c.startswith("__unnamed_")]
    if unnamed:
        F.add(1, "unnamed_columns", "decision", ", ".join(unnamed), [], "header cell empty")
    qtext = L.meta.get("question_text", {})
    residue = raw.apply(lambda r: np.mean([r[c].strip() != "" and r[c].strip() in (c, qtext.get(c, ""))
                                           for c in cols]) > 0.5, axis=1)
    if residue.any():
        F.add(1, "header_residue", "blocking", "", df.index[residue], "row repeats the header or question text")
    item_vars = [v for v in vmap if V.loc[v, "type"] in NUMERIC_TYPES and V.loc[v, "scale"]]
    text_cols = [vmap[v] for v in vmap if V.loc[v, "type"] in ("categorical", "text")] or cols
    test = raw[text_cols].apply(lambda r: any(TEST_TEXT.search(x) for x in r), axis=1) & ~rows_empty
    if test.any():
        F.add(1, "test_rows", "blocking", "", df.index[test], "a text field reads like a test entry",
              examples=raw.loc[test, text_cols].head(5).to_dict("records"))
    for rr in R.itertuples() if len(R) else []:
        if getattr(rr, "kind", "") == "test_ids":
            tid = set(split_list(rr.expression))
            hit = df.index[df[id_col].astype(str).isin(tid)] if id_col else []
            if len(hit):
                F.add(1, "test_rows", "blocking", id_col, hit, f"listed as test IDs in rule {rr.rule_id}")
    for rr in R.itertuples() if len(R) else []:          # scale_size: "WarE=10" - item count from the source instrument
        if getattr(rr, "kind", "") == "scale_size":
            for part in split_list(rr.expression):
                if "=" in part:
                    sc_, n_ = part.split("=", 1)
                    have = [v for v in V.index if str(V.loc[v, "scale"]).strip() == sc_.strip()]
                    if n_.strip().isdigit() and len(have) != int(n_):
                        F.add(1, "scale_size_mismatch", "blocking", sc_.strip(), [],
                              f"the dictionary lists {len(have)} items for '{sc_.strip()}', the source instrument has {n_.strip()} "
                              "(rule " + str(rr.rule_id) + "): an item lost in translation or export, or a wrong scale assignment")
    not_in = [c for c in cols if c not in vmap.values()]
    if not_in:
        F.add(1, "not_in_dictionary", "decision", ", ".join(not_in[:40]), [],
              f"{len(not_in)} columns have no dictionary entry")
    missing_vars = [v for v in V.index if v not in vmap]
    if missing_vars:
        req = [v for v in missing_vars if V.loc[v, "required"] == "yes"]
        F.add(1, "missing_from_data", "blocking" if req else "info", ", ".join(missing_vars[:40]), [],
              f"{len(missing_vars)} dictionary variables not found in this file")
    col2var = {c: v for v, c in vmap.items()}
    for c in cols:
        nb = raw[c][~blank[c]]
        vtype = V.loc[col2var[c], "type"] if c in col2var else ""
        if nb.empty and vtype != "empty":
            F.add(1, "empty_column", "info", c, [], "no values")
        elif len(nb) > 1 and nb.nunique() == 1 and vtype != "constant":
            F.add(1, "constant_column", "info", c, [], f"single value '{nb.iloc[0]}'")

    # ---------------------------------------------------------------- layer 2
    dup_cols = [c for c in cols if c not in {vmap[v] for v in vmap if V.loc[v, "type"] in ("id", "datetime", "text")}] or cols
    live = ~rows_empty & ~test
    if id_col:
        s = raw[id_col].str.strip()
        miss = (s == "") & live
        if miss.any():
            F.add(2, "missing_id", "blocking", id_col, df.index[miss], "no identifier")
        filled = s[s != ""]
        lens = filled.str.len()
        ml = int(lens.mode().iloc[0]) if len(lens) else 0
        fixed_len = len(lens) > 0 and (lens == ml).mean() >= 0.9 and ml >= 4   # personal numbers, not running counters
        digits_major = filled.str.fullmatch(r"\d+").mean() > 0.8 if len(filled) else False
        repeated = s.str.fullmatch(r"(\d)\1{3,}|0{3,}|12345\d*").fillna(False)
        nondigit = digits_major & ~s.str.fullmatch(r"\d+").fillna(False)
        junk = (s != "") & live & (repeated | nondigit)
        if junk.any():
            F.add(2, "junk_id", "blocking", id_col, df.index[junk],
                  "placeholder or malformed identifiers (repeated digits, letters/symbols/text inside a numeric ID)",
                  examples=[x[:40] for x in s[junk].head(10).tolist()])
        odd_len = (s != "") & live & ~junk & fixed_len & (s.str.len() != ml)
        if odd_len.any():
            ol = s[odd_len]
            natid = ol[ol.map(_il_id_ok)]
            F.add(2, "id_length_mismatch", "decision", id_col, df.index[odd_len],
                  f"most IDs have {ml} digits; these differ - possibly a different identifier (e.g. national ID instead of"
                  f" personal number), a typo, or a dropped leading zero. {len(natid)} of them pass the Israeli ID checksum"
                  + (" (likely a national ID number: also a privacy issue)" if len(natid) else ""),
                  lengths=ol.str.len().value_counts().to_dict(), checksum_valid=int(len(natid)))
        dup = s[(s != "") & live & ~junk]
        groups = dup[dup.duplicated(keep=False)].groupby(dup).groups
        ident, confl = [], []
        for _, idx in groups.items():
            block = raw.loc[list(idx), dup_cols]
            (ident if len(block.drop_duplicates()) == 1 else confl).append(list(idx))
        if ident:
            F.add(2, "duplicate_id_identical", "blocking", id_col, [r for g in ident for r in g[1:]],
                  f"{len(ident)} IDs appear more than once with identical answers (extra copies listed)",
                  groups=ident[:50])
        if confl:
            comp = (~blank[dup_cols]).sum(axis=1)
            detail = [[{"row": int(r), "answered": int(comp[r])} for r in g] for g in confl[:50]]
            F.add(2, "duplicate_id_conflicting", "blocking", id_col, [r for g in confl for r in g],
                  f"{len(confl)} IDs appear more than once with DIFFERENT answers", groups=detail)
    already = {r for g in (ident if id_col else []) for r in g[1:]}
    body = raw.loc[live, dup_cols]
    dmask = body.duplicated(keep="first") & (~blank.loc[live, dup_cols]).sum(axis=1).gt(len(dup_cols) * 0.5) \
        & ~body.index.isin(list(already))
    if dmask.any():
        nums = body.apply(as_number)
        zero = (nums.fillna(0).eq(0).sum(axis=1) >= 0.6 * len(dup_cols))
        z = dmask & zero
        if z.any():
            F.add(2, "duplicate_empty_records", "info", "", body.index[z],
                  "rows identical because they are empty or all-zero (e.g. a device not worn): not copies - "
                  "treat as missing records")
        if (dmask & ~zero).any():
            F.add(2, "duplicate_rows", "blocking", "", body.index[dmask & ~zero],
                  "identical answers on every content column (ID/time ignored); copies listed")
    items = [vmap[v] for v in item_vars]
    if len(items) >= 10 and live.sum() <= 3000:
        X = df.loc[live, items].apply(as_number).to_numpy(dtype=float)
        idx = df.index[live]
        pairs = []
        for i in range(len(X)):
            same = (X[i + 1:] == X[i]) & ~np.isnan(X[i + 1:]) & ~np.isnan(X[i])
            both = (~np.isnan(X[i + 1:]) & ~np.isnan(X[i])).sum(axis=1)
            share = np.divide(same.sum(axis=1), both, out=np.zeros(len(both)), where=both > 0)
            for j in np.where((share >= 0.95) & (both >= 10) & (share < 1.0001))[0]:
                a, b = idx[i], idx[i + 1 + j]
                if not dmask.get(b, False) and not dmask.get(a, False):
                    pairs.append((int(a), int(b), round(float(share[j]), 3)))
        if pairs:
            F.add(2, "near_duplicates", "decision", "", sorted({p[1] for p in pairs}),
                  f"{len(pairs)} pairs share >=95% of item answers", pairs=pairs[:50])

    # ---------------------------------------------------------------- layer 3
    for v, c in vmap.items():
        meta = V.loc[v]
        typ = meta["type"]
        s = raw[c].str.strip()
        present = (s != "") & live
        codes = set(split_list(meta["missing_codes"]))
        if typ in NUMERIC_TYPES:
            tmap = parse_pairs(meta["text_map"], "=>")
            num = as_number(s)
            is_code = s.isin(codes)
            textual = present & num.isna() & ~is_code
            mapped = textual & s.isin(tmap.keys())
            if mapped.any():
                F.add(3, "text_answers", "decision", v, df.index[mapped],
                      f"answers stored as text with a known text->code map ({s[mapped].nunique()} distinct)")
            bad = textual & ~mapped
            if bad.any():
                F.add(3, "non_numeric", "blocking", v, df.index[bad], "non-numeric value in a numeric variable",
                      examples=s[bad].value_counts().head(8).to_dict())
            hit = present & is_code
            if hit.any():
                F.add(3, "missing_codes_present", "decision", v, df.index[hit],
                      f"missing codes {sorted(set(s[hit]))} are numbers in the file and would enter means")
            lo, hi = as_number(pd.Series([meta["min"]])).iloc[0], as_number(pd.Series([meta["max"]])).iloc[0]
            valid = present & num.notna() & ~is_code
            oor = valid & (((num < lo) if pd.notna(lo) else False) | ((num > hi) if pd.notna(hi) else False))
            if oor.any():
                F.add(3, "out_of_range", "blocking", v, df.index[oor],
                      f"outside {to_str(lo)}–{to_str(hi)} ({meta['status']} range)",
                      examples=s[oor].value_counts().head(8).to_dict())
            if typ in ("integer", "likert"):
                nonint = valid & ~oor & (num != np.round(num))
                if nonint.any():
                    F.add(3, "non_integer", "blocking", v, df.index[nonint], "fractional value in an integer variable",
                          examples=s[nonint].head(8).tolist())
        elif typ in ("categorical", "binary"):
            allowed = split_list(meta["allowed"])
            allowed_keys = [a.split("=", 1)[0].strip() if "=" in a else a for a in allowed]
            allowed_lbls = [a.split("=", 1)[1].strip() if "=" in a else a for a in allowed]
            if not allowed:
                continue
            if str(meta["status"]).strip() != "confirmed":    # allowed = observed values: variants hide inside it
                groups_ = {}
                for a_ in allowed_lbls:
                    groups_.setdefault(norm_label(a_), []).append(a_)
                for vs_ in [g for g in groups_.values() if len(g) > 1]:
                    canon = max(vs_, key=lambda x: (raw[c] == x).sum())
                    hit = present & raw[c].isin([x for x in vs_ if x != canon])
                    if hit.any():
                        F.add(3, "label_variant", "decision", v, df.index[hit],
                              "spelling/spacing variants of one label inside the (unconfirmed) allowed list",
                              mapping={f"{x!r} -> {canon!r}": int((raw[c] == x).sum()) for x in vs_ if x != canon})
            s = raw[c]                         # unstripped: a trailing space is a different group
            ok = s.isin(allowed_keys) | s.isin(allowed_lbls) | s.isin(codes)
            bad = present & ~ok
            if bad.any():
                norm_allowed = {norm_label(a): a for a in allowed_lbls}
                variants, others = {}, []
                for r_, val in s[bad].items():
                    nv = norm_label(val)
                    tgt = norm_allowed.get(nv)
                    if tgt is None:
                        close = difflib.get_close_matches(nv, list(norm_allowed), n=1, cutoff=0.8)
                        tgt = norm_allowed[close[0]] if close else None
                    if tgt:
                        variants.setdefault(f"{val!r} -> {tgt!r}", []).append(r_)
                    else:
                        others.append(r_)
                if variants:
                    F.add(3, "label_variant", "decision", v, [r for rs in variants.values() for r in rs],
                          "labels that differ from an allowed value only in spacing/spelling",
                          mapping={k: len(rs) for k, rs in variants.items()})
                if others:
                    F.add(3, "not_allowed", "blocking", v, others, "value not in the allowed list",
                          examples=s.loc[others].value_counts().head(8).to_dict())
        elif typ == "datetime":
            parts = s[present].str.extract(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")
            if parts.notna().all(axis=1).mean() > 0.5:
                a_, b_ = parts[0].astype(float), parts[1].astype(float)
                if (a_ > 12).any() and not (b_ > 12).any():
                    order = "day-first"
                elif (b_ > 12).any() and not (a_ > 12).any():
                    order = "month-first"
                elif not (a_ > 12).any() and not (b_ > 12).any():
                    order = None
                    F.add(3, "date_order_ambiguous", "decision", v, df.index[present][((a_ <= 12) & (b_ <= 12)).values],
                          "every date here has day and month both <= 12: 03/04 could be 3 April or 4 March - "
                          "ask how they were typed (project setting date_order); the parser must be told, never guess")
                else:
                    order = "mixed"
                    F.add(3, "date_order_mixed", "blocking", v, df.index[present],
                          "some dates have a first part > 12 and others a second part > 12: two orders in one column")
                d = pd.to_datetime(s.where(present), errors="coerce", dayfirst=(order != "month-first"), format="mixed")
            else:
                d = pd.to_datetime(s.where(present), errors="coerce", format="mixed")
            ser = present & s.str.fullmatch(r"\d{1,5}(\.0+)?") & as_number(s).between(1, 80000)
            if ser.any() and (present & ~ser).any():
                F.add(3, "date_mixed_excel_serials", "blocking", v, df.index[ser],
                      f"{int(ser.sum())} dates stored as Excel serial numbers beside {int((present & ~ser).sum())} written "
                      "dates: two formats in one column - convert the serials (origin 1899-12-30), then check them",
                      examples=s[ser].head(5).tolist())
            badd = present & d.isna() & ~ser
            if badd.any():
                F.add(3, "bad_date", "blocking", v, df.index[badd], "does not parse as a date",
                      examples=s[badd].head(5).tolist())
            if window:
                w0, w1 = pd.to_datetime(window[0]), pd.to_datetime(window[1]) + pd.Timedelta(days=1)
                out = present & d.notna() & ((d < w0) | (d >= w1))
                if out.any():
                    F.add(3, "outside_window", "decision", v, df.index[out],
                          f"before {window[0]} or after {window[1]}", examples=s[out].head(5).tolist())
    # completeness by scale, aware of routing: blocks that respondents get EITHER/OR are not "abandoned"
    by_scale = {}
    for v in item_vars:
        by_scale.setdefault(V.loc[v, "scale"], []).append(vmap[v])
    excl, routed_out = {}, pd.DataFrame(False, index=df.index[live], columns=list(by_scale))
    if by_scale:
        all_items = [c for cs in by_scale.values() for c in cs]
        nl = int(live.sum())
        ans = {sc: (~blank.loc[live, cs]).any(axis=1) for sc, cs in by_scale.items()}
        floor = max(5, 0.1 * nl)
        for a in by_scale:
            for b in by_scale:
                if a == b or ans[a].sum() < floor or ans[b].sum() < floor:
                    continue
                if (ans[a] & ans[b]).sum() <= max(1, 0.02 * nl):
                    excl.setdefault(a, set()).add(b)
        for a, bs in excl.items():                       # a is empty because the row was routed to b
            routed_out[a] = ~ans[a] & pd.concat([ans[b] for b in bs], axis=1).any(axis=1)
        routes, none_routed = [], pd.Series(False, index=df.index[live])
        if excl:
            RS = sorted(excl)
            parent = {a: a for a in RS}

            def root(x):
                while parent[x] != x:
                    x = parent[x]
                return x
            for i, a in enumerate(RS):
                for b in RS[i + 1:]:
                    if b not in excl[a] and (ans[a] & ans[b]).sum() >= 0.5 * min(ans[a].sum(), ans[b].sum()):
                        parent[root(a)] = root(b)
            comp = {}
            for a in RS:
                comp.setdefault(root(a), []).append(a)
            routes = sorted(sorted(g) for g in comp.values())
            none_routed = ~pd.concat([ans[a] for a in RS], axis=1).any(axis=1) \
                & (~blank.loc[live, all_items]).any(axis=1)
            F.add(4, "routed_blocks", "info", ", ".join(RS[:12]), [],
                  f"{len(routes)} alternative routes: blocks answered on an either/or basis (almost no respondent "
                  "answered both) are treated as routing, not abandonment - confirm against the survey flow",
                  routes=routes[:10], respondents_per_route=[int(pd.concat([ans[a] for a in g], axis=1).any(axis=1).sum())
                                                             for g in routes[:10]])
            if none_routed.any():
                F.add(3, "abandoned_block", "decision", "routed section", none_routed[none_routed].index,
                      "stopped before the routed section: none of its routes answered")
        expected = pd.DataFrame({c: True for c in all_items}, index=df.index[live])
        for sc, cs in by_scale.items():
            for c in cs:
                expected[c] = ~routed_out[sc]
        answered_n = (~blank.loc[live, all_items] & expected).sum(axis=1)
        share = answered_n / expected.sum(axis=1).clip(lower=1)
        part = share[(share < 0.5) & (share > 0)]
        if len(part):
            F.add(3, "partial_response", "decision", "", part.index,
                  "answered fewer than half of the scale items this respondent was routed to")
        for sc, cs in by_scale.items():
            if len(cs) < 3:
                continue
            ab = (blank.loc[live, cs].all(axis=1)) & ((~blank.loc[live, all_items]).sum(axis=1) > 0) & ~routed_out[sc]
            if sc in excl:
                ab &= ~none_routed
            ab = ab[ab]
            if len(ab):
                F.add(3, "abandoned_block", "decision", sc, ab.index,
                      f"all {len(cs)} items of '{sc}' empty while other items were answered")

    # layer 5: identical answers across a whole scale. One scale alone is weak evidence (a low-variance
    # construct, a frequency scale where "never" is common); several scales, or a scale with reversed
    # items, are strong evidence (Ward & Meade, 2023).
    rev_scales = {V.loc[v, "scale"] for v in item_vars if str(V.loc[v, "reverse"]).strip().lower() in ("yes", "כן", "1", "true")}
    sl, rates = {}, {}
    for sc, cs in by_scale.items():
        if len(cs) < 5:
            continue
        X = df.loc[live, cs].apply(as_number)
        answered = X.notna().sum(axis=1)
        elig = answered >= 5
        same = (X.nunique(axis=1) == 1) & elig
        rates[sc] = round(float(same.sum() / max(1, elig.sum())), 3)
        if same.any():
            sl[sc] = same[same].index
    if sl:
        informative = [sc for sc in sl if rates[sc] <= 0.2]
        count = Counter(r for sc in informative for r in sl[sc])
        strong = {r for r, k in count.items() if k >= 2} | {r for sc in informative if sc in rev_scales for r in sl[sc]}
        if strong:
            F.add(5, "straightlining", "decision", ", ".join(sorted(informative)[:12]), sorted(strong),
                  "the same answer on every item of two or more scales, or on a scale with reversed items",
                  scales_per_row={int(r): count[r] for r in sorted(strong)}, rate_by_scale=rates)
        weak = sorted({r for sc in sl for r in sl[sc]} - strong)
        if weak:
            common = sorted(sc for sc in sl if rates[sc] > 0.2)
            F.add(5, "straightlining_single", "info", ", ".join(sorted(sl)[:12]), weak,
                  "identical answers on one scale only - weak evidence of careless responding"
                  + (f"; in {common} more than 20% of respondents answer identically, a property of the scale" if common else ""),
                  rate_by_scale=rates)

    # univariate extremes and disagreeing twin measures (e.g. measured vs reported weight)
    cont = [v for v in vmap if V.loc[v, "type"] in ("numeric", "integer")]
    Xc = {v: as_number(raw.loc[live, vmap[v]]) for v in cont}
    for v in cont:
        sv = raw.loc[live, vmap[v]].astype(str).str.strip().str.lower()
        sp = sv.index[sv.isin(["inf", "-inf", "+inf", "infinity", "-infinity", "nan", "#num!", "#div/0!", "#value!", "na"])
                      | np.isinf(Xc[v].fillna(0))]
        if len(sp):
            F.add(3, "special_value", "blocking", v, sp,
                  f"{len(sp)} special values (Inf, NaN, spreadsheet errors) stored as data: a computation or export failure - "
                  "missing, not a number", examples=sorted(set(raw.loc[sp, vmap[v]].astype(str)))[:6])
        Xc[v] = Xc[v].replace([np.inf, -np.inf], np.nan)
        x = Xc[v].dropna()
        neg = x[x < 0]
        if 0 < len(neg) <= max(1, 0.02 * len(x)) and len(x) >= 10 and (x >= 0).sum() >= len(x) - max(1, 0.02 * len(x)):
            F.add(3, "negative_in_positive", "decision", v, neg.index,
                  f"{len(neg)} negative values in a measure that is otherwise never negative: a sign error or a code",
                  examples=sorted(map(to_str, neg))[:6])
        if len(x) < 30 or x.nunique() < 10:
            continue
        q1, q2, q3 = x.quantile([.25, .5, .75])
        skewed = q3 > q1 and ((q3 - q2) - (q2 - q1)) / (q3 - q1) > 0.2 and x.min() >= 0
        xt = np.log1p(x) if skewed else x              # a long right tail (tenure, income, counts) is not an error
        med, mad = x.median(), (xt - xt.median()).abs().median() * 1.4826
        if mad == 0:
            continue
        z = (xt - xt.median()) / mad
        ext = z[z.abs() > 6]
        if 0 < len(ext) <= max(3, 0.01 * len(x)):
            F.add(3, "extreme_value", "decision", v, ext.index,
                  f"{len(ext)} values more than 6 robust SDs from the median ({to_str(float(med))}): a typo, a unit or column "
                  "swap, or a true extreme - check against the source", examples=sorted(map(to_str, x[ext.index]))[:8])
    seen_pairs = set()
    for i, a in enumerate(cont):
        for b in cont[i + 1:]:
            j = pd.concat([Xc[a], Xc[b]], axis=1, keys=["a", "b"]).dropna()
            if len(j) < 30 or j.a.nunique() < 10 or j.b.nunique() < 10:
                continue
            r = j.a.corr(j.b, method="spearman")
            if not np.isfinite(r) or r < 0.85:
                continue
            d = j.a.rank(pct=True) - j.b.rank(pct=True)          # rank disagreement: robust to units and skew
            res = j.a - j.b if (abs(j.a.median() - j.b.median()) < 0.25 * max(abs(j.a.median()), 1e-9)) else None
            cap = max(5, 0.02 * len(j))
            if res is not None:                                    # same scale: the raw difference
                mad = (res - res.median()).abs().median() * 1.4826
                if mad > 0:
                    bad = res.index[(res - res.median()).abs() > 6 * mad]
                else:                                              # identical in most rows: every departure counts
                    bad = res.index[(res - res.median()).abs() > 1e-9]
                    cap = 0.10 * len(j)
            else:
                bad = d.index[d.abs() > 0.5]
            if 0 < len(bad) <= cap:
                F.add(4, "pair_disagreement", "decision", f"{a} / {b}", bad,
                      f"'{a}' and '{b}' agree closely (Spearman {r:.2f}) except in {len(bad)} rows: two measures of one "
                      "thing that disagree - a swapped pair of columns, a unit error, or a typo",
                      examples={int(k): f"{to_str(j.at[k, 'a'])} / {to_str(j.at[k, 'b'])}" for k in list(bad)[:6]})
    # balance identities nobody declared: a + b = c in most complete rows (economic and HR records)
    if 3 <= len(cont) <= 40:
        Z = pd.DataFrame(Xc)
        for c in cont:
            for i, a in enumerate(cont):
                for b in cont[i + 1:]:
                    if c in (a, b):
                        continue
                    j = Z[[a, b, c]].dropna()
                    if len(j) < 10 or j[c].abs().median() == 0:
                        continue
                    tol = 1e-6 + 0.005 * j[c].abs()
                    hold = ((j[a] + j[b] - j[c]).abs() <= tol)
                    share = lambda u: (j[u].abs() / j[c].abs().replace(0, np.nan)).median()  # noqa: E731
                    if hold.mean() >= 0.7 and share(a) >= 0.01 and share(b) >= 0.01:   # both terms really add something
                        bad = j.index[~hold]
                        F.add(4, "balance_hypothesis", "decision", f"{a} + {b} = {c}", bad,
                              f"'{a}' + '{b}' equals '{c}' in {hold.mean():.0%} of {len(j)} complete rows: an accounting "
                              f"identity? if so, the {len(bad)} rows that break it are errors - confirm the rule with the "
                              "data's owner, then add it to the rules sheet", examples={int(k): f"{to_str(j.at[k, a])} + "
                              f"{to_str(j.at[k, b])} vs {to_str(j.at[k, c])}" for k in list(bad)[:6]})
    # the same person twice: no ID, or an ID that differs, but the same name
    name_cols = [vmap[v] for v in vmap if re.search(r"(first.?name|last.?name|surname|full.?name|^name$|שם.?פרטי|"
                                                    r"שם.?משפחה|^שם$|שם.?מלא)", v, re.I)]
    if name_cols:
        nm = raw.loc[live, name_cols].astype(str).apply(lambda c: c.str.strip().str.lower()).agg(" ".join, axis=1)
        nm = nm[nm.str.strip() != ""]
        dupn = nm[nm.duplicated(keep=False)]
        if len(dupn):
            F.add(2, "same_name_twice", "decision", " + ".join(name_cols), dupn.index,
                  f"{dupn.nunique()} names appear in more than one row: one person counted twice (e.g. one row per "
                  "subject or role), or two people with one name - decide the unit of analysis before counting",
                  examples=sorted(dupn.unique())[:6])
    # item-level missingness (information for the missing-data rule, not a defect by itself)
    flagged = {r for f in F.items if f["check"] in ("partial_response", "abandoned_block", "empty_rows") for r in f["rows"]}
    per_var, rows_any = {}, set()
    for v, c in vmap.items():
        if V.loc[v, "type"] in ("id", "datetime", "text", "empty", "constant"):
            continue
        m_ = blank[c] & live & ~df.index.isin(list(flagged))
        if m_.any():
            per_var[v] = int(m_.sum())
            rows_any |= set(df.index[m_])
    if per_var:
        F.add(3, "item_missing", "info", ", ".join(list(per_var)[:20]), sorted(rows_any),
              f"isolated missing values in {len(per_var)} variables", per_var=per_var)

    # ---------------------------------------------------------------- layer 4
    env = pd.DataFrame(index=df.index)
    for v, c in vmap.items():
        env[v] = as_number(raw[c]) if V.loc[v, "type"] in NUMERIC_TYPES else raw[c].str.strip()
    for v, c in vmap.items():
        cond = V.loc[v, "condition"]
        if not cond:
            continue
        try:
            asked = env.eval(cond, engine="python").fillna(False).astype(bool)
        except Exception as e:
            F.add(4, "rule_violation", "info", v, [], f"condition could not be evaluated: {cond} ({e})")
            continue
        present = raw[c].str.strip() != ""
        viol = live & ~asked & present
        if viol.any():
            F.add(4, "skip_logic_violation", "decision", v, df.index[viol], f"answered although condition is false: {cond}")
        if V.loc[v, "required"] == "yes":
            req = live & asked & ~present
            if req.any():
                F.add(4, "required_missing", "decision", v, df.index[req], f"missing although condition is true: {cond}")
    for rr in R.itertuples() if len(R) else []:
        if getattr(rr, "kind", "") not in ("row_rule", "exclude"):
            continue
        try:
            ok = env.eval(rr.expression, engine="python").fillna(rr.kind == "row_rule").astype(bool)
        except Exception as e:
            F.add(4, "rule_violation", "info", rr.rule_id, [], f"rule could not be evaluated: {rr.expression} ({e})")
            continue
        hit = live & (~ok if rr.kind == "row_rule" else ok)
        if hit.any():
            F.add(4, "rule_violation", rr.severity or "decision", rr.rule_id, df.index[hit],
                  rr.description or rr.expression)
    F.items = merge_repeats(F.items)
    summary = Counter(f["severity"] for f in F.items)
    return {"file": file_label, "path": path, "rows": int(len(df)), "rows_live": int(live.sum()),
            "id_column": id_col, "dictionary": dict_path, "summary": dict(summary), "findings": F.items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--dict")
    ap.add_argument("--out", required=True)
    ap.add_argument("--label")
    ap.add_argument("--id")
    ap.add_argument("--window")
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    res = scan(a.data, a.dict, a.label, a.id, a.window.split(",") if a.window else None)
    base = os.path.join(a.out, "findings")
    write_json(base + ".json", res)
    from report import render_findings
    open(f"{base}_{a.lang}.md", "w", encoding="utf-8").write(render_findings(res, a.lang))
    dec = pd.DataFrame([{"finding": f["id"], "check": f["check"], "var": f["var"], "count": f["count"],
                         "decision": "", "option": "", "rationale": "", "source": ""} for f in res["findings"]])
    dec.to_csv(os.path.join(a.out, "decisions_template.csv"), index=False, encoding="utf-8-sig")
    print(f"{len(res['findings'])} findings {res['summary']} -> {base}_{a.lang}.md")


if __name__ == "__main__":
    main()
