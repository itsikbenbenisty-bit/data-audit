"""Cross-file variable matching and linkage (dictionary building across files / waves).

  python match.py FILE [FILE ...] --out DIR [--id FILE=COL ...] [--lang he|en]

1. Code collisions: the same column name carries DIFFERENT item text in two files
   (e.g. Q73 = officer identity in one instrument family, aesthetics in another).
2. Renames: different column names carry the SAME item text (same item, new code).
3. Linkage: overlap of identifiers between files (who can be matched across waves).
4. Hypotheses without text: for a column in file B with no name/text match, the column
   in file A whose values correlate most strongly among shared IDs is proposed as a
   hypothesis with its evidence (r, n). The researcher confirms or rejects it.

Item text comes from Qualtrics question rows, SPSS variable labels, or a dictionary.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from itertools import combinations

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import ROWKEY, as_number, load_table, write_json  # noqa: E402


def _text(L):
    t = dict(L.meta.get("question_text", {}) or {})
    for k, v in (L.meta.get("column_labels", {}) or {}).items():
        if v and not t.get(k):
            t[k] = v
    return {k: re.sub(r"\s+", " ", str(v).replace("_x000D_", " ")).strip() for k, v in t.items() if v}


def sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def split_item(t: str) -> tuple[str, str]:
    """Matrix questions are exported as 'stem - item'. The item carries the meaning."""
    if " - " in t:
        stem, item = t.rsplit(" - ", 1)
        return stem.strip(), item.strip()
    return "", t


def text_sim(a: str, b: str) -> float:
    sa, ia = split_item(a)
    sb, ib = split_item(b)
    if ia and ib and (sa or sb):
        return sim(ia, ib)          # the same stem with a different item is a different question
    return sim(a, b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--id", nargs="*", default=[], help="FILE=COL (file name as given)")
    ap.add_argument("--lang", default="he")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ids = dict(x.split("=", 1) for x in a.id)
    Ls = {os.path.basename(f): load_table(f) for f in a.files}
    T = {k: _text(L) for k, L in Ls.items()}
    res = {"collisions": [], "renames": [], "linkage": [], "hypotheses": []}
    for fa, fb in combinations(Ls, 2):
        ta, tb = T[fa], T[fb]
        ca = [c for c in Ls[fa].df.columns if c != ROWKEY]
        cb = [c for c in Ls[fb].df.columns if c != ROWKEY]
        for c in set(ca) & set(cb):
            if (c in ta) != (c in tb) and re.match(r"^Q\d", c):
                res.setdefault("unverifiable", []).append({"column": c, "files": [fa, fb],
                                                            "text_in": fa if c in ta else fb})
            if c in ta and c in tb:
                s = text_sim(ta[c], tb[c])
                if s < 0.6:
                    res["collisions"].append({"column": c, "files": [fa, fb], "similarity": round(s, 2),
                                              "text_a": ta[c][:140], "text_b": tb[c][:140]})
        for x, y, fx, fy, tx, ty in ((ca, cb, fa, fb, ta, tb), (cb, ca, fb, fa, tb, ta)):
            ys = set(y)
            for c in x:
                if c in ys or not re.match(r"^Q\d+$", c):
                    continue
                fam = sorted(z for z in y if z.startswith(c + "_"))
                if fam:
                    res.setdefault("family_collisions", []).append({
                        "column": c, "file": fx, "family_in": fy, "family": fam[:12],
                        "text": tx.get(c, "")[:120], "family_text": ty.get(fam[0], "")[:120]})
        by_text_a = {}
        for c in ca:
            if c in ta and len(ta[c]) > 15:
                by_text_a.setdefault(ta[c], []).append(c)
        for c in cb:
            if c in tb and len(tb[c]) > 15 and tb[c] in by_text_a and c not in by_text_a[tb[c]]:
                res["renames"].append({"text": tb[c][:140], "a": [fa, by_text_a[tb[c]]], "b": [fb, c]})
        if fa in ids and fb in ids:
            A, B = Ls[fa].df, Ls[fb].df
            sa = A[ids[fa]].astype(str).str.strip()
            sb = B[ids[fb]].astype(str).str.strip()
            sa, sb = sa[sa != ""], sb[sb != ""]
            shared = sorted(set(sa) & set(sb))
            res["linkage"].append({"files": [fa, fb], "ids_a": int(sa.nunique()), "ids_b": int(sb.nunique()),
                                   "shared": len(shared), "only_a": int(sa.nunique() - len(shared)),
                                   "only_b": int(sb.nunique() - len(shared))})
            if len(shared) >= 20:
                Ai = A.assign(_k=sa).dropna(subset=["_k"]).drop_duplicates("_k").set_index("_k").loc[shared]
                Bi = B.assign(_k=sb).dropna(subset=["_k"]).drop_duplicates("_k").set_index("_k").loc[shared]
                na = {c: as_number(Ai[c]) for c in ca if as_number(Ai[c]).notna().mean() > 0.8}
                nb = {c: as_number(Bi[c]) for c in cb if as_number(Bi[c]).notna().mean() > 0.8}
                for c in [c for c in nb if c not in na and c not in tb]:
                    best = max(((abs(nb[c].corr(na[x])), x) for x in na if na[x].std() > 0 and nb[c].std() > 0),
                               default=(0, None))
                    if best[1] is not None and best[0] >= 0.5:
                        res["hypotheses"].append({"b": [fb, c], "a": [fa, best[1]], "abs_r": round(float(best[0]), 2),
                                                  "n": len(shared), "status": "hypothesis"})
    codes = {}
    for c in res["collisions"]:
        codes.setdefault(c["column"], set()).update(c["files"])
    res["collision_codes"] = {k: sorted(v) for k, v in sorted(codes.items())}
    fams = {}
    for fc in res.get("family_collisions", []):
        fams.setdefault(fc["column"], set()).update([fc["file"], fc["family_in"]])
    res["family_collision_codes"] = {k: sorted(v) for k, v in sorted(fams.items())}
    unv = {}
    for u in res.get("unverifiable", []):
        unv.setdefault(u["column"], set()).update(u["files"])
    res["unverifiable_codes"] = {k: sorted(v) for k, v in sorted(unv.items())}
    res["unverifiable_note"] = ("same code in two files, question text in only one: whether it is the same question "
                                "cannot be checked from the data - verify against the questionnaire before merging")
    write_json(os.path.join(a.out, "match.json"), res)
    he = a.lang == "he"
    md = ["# " + ("התאמת משתנים בין קבצים" if he else "Cross-file variable matching"), ""]
    md.append(f"- {'התנגשויות קוד (אותו שם, נוסח שונה)' if he else 'Code collisions'}: {len(res['collisions'])}")
    md.append(f"- {'שינויי שם (אותו נוסח, שם שונה)' if he else 'Renames'}: {len(res['renames'])}")
    if res["family_collision_codes"]:
        md.append(f"- {'קוד בודד בקובץ אחד שהוא שם משפחת פריטים בקובץ אחר (Q140 מול Q140_1…): לא למזג לפי קידומת' if he else 'Single code in one file is an item-family prefix in another (Q140 vs Q140_1...)'}: "
                  + ", ".join(list(res["family_collision_codes"])[:40]))
    if res["unverifiable_codes"]:
        md.append(f"- {'קודים משותפים שאין להם נוסח באחד הקבצים (אי אפשר לוודא שזו אותה שאלה)' if he else 'Shared codes with no text in one file (cannot verify)'}: "
                  + ", ".join(list(res["unverifiable_codes"])[:40]))
    for l in res["linkage"]:
        md.append(f"- {'קישור' if he else 'Linkage'} {l['files'][0]} ↔ {l['files'][1]}: "
                  f"{l['shared']} {'מזהים משותפים' if he else 'shared IDs'} ({l['only_a']} / {l['only_b']} {'רק באחד' if he else 'unmatched'})")
    md.append("")
    if res["collisions"]:
        md += ["| עמודה | קבצים | דמיון | נוסח א | נוסח ב |" if he else "| Column | Files | Similarity | Text A | Text B |", "|---|---|---|---|---|"]
        for c in res["collisions"][:60]:
            md.append(f"| {c['column']} | {' / '.join(c['files'])} | {c['similarity']} | {c['text_a']} | {c['text_b']} |")
    open(os.path.join(a.out, f"match_{a.lang}.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"collisions {len(res['collisions'])}, renames {len(res['renames'])}, linkage {len(res['linkage'])}, hypotheses {len(res['hypotheses'])}")


if __name__ == "__main__":
    main()
