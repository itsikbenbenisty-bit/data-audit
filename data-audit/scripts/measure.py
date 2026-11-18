#!/usr/bin/env python3
"""Layers 5-7: response quality, measurement, and two-wave consistency.

  measure.py DATA --dict dict.xlsx --out DIR [--label NAME] [--id COL] [--duration COL]
             [--t2 DATA2 --t2-dict dict2.xlsx --t2-label NAME2 --t2-id COL2] [--lang he|en]

Nothing here changes data. Every finding is evidence for a researcher's decision:
  layer 6  reliability (alpha, item-rest r, alpha-if-deleted), reverse-keying as the data show it,
           items of one scale on different ranges
  layer 5  careless responding by several indices (longstring, Mahalanobis D2, even-odd consistency,
           speed); a respondent is flagged when two or more indices agree (Curran, 2016; Ward & Meade, 2023)
  layer 7  two waves: linkage, fields that should be stable but changed, extreme change in scale scores,
           test-retest r per scale
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(__file__))
from da_common import (ROWKEY, as_number, load_table, parse_pairs, read_dictionary,  # noqa: E402
                       resolve_source, split_list, to_str, write_json)

NUMERIC = ("likert", "integer", "numeric", "binary")
YES = ("yes", "כן", "1", "true", "y")

OPTIONS = {
    "reverse_suspected": ["check the wording: reverse-keyed? then mark reverse=yes in the dictionary",
                          "two subscales scored separately (as the source instrument says)?",
                          "check the coding of this item in the export", "drop the item from the scale (report it)"],
    "reverse_already_applied": ["do NOT reverse again: the data already look reversed - confirm in the export/syntax",
                                "the item is not reverse-keyed: correct the dictionary"],
    "reverse_not_supported": ["keep the reversal if the wording is reversed (the sample may differ)",
                              "report the anomaly; decide by a rule set in advance"],
    "mixed_item_ranges": ["rescale to one range before averaging (document the formula)", "check the export"],
    "low_reliability": ["report alpha with its CI", "inspect weak items (item-rest r)", "do not drop items post hoc without a rule"],
    "reverse_log_conflict": ["the item is reverse-keyed: mark reverse=yes in the dictionary (the file is already reversed)",
                             "the item is not reverse-keyed: undo the reversal with a new logged version"],
    "reverse_hypothesis": ["confirm from the source instrument and mark reverse=yes/no", "report both alphas"],
    "ranking_items": ["score by the instrument's ranking rules, not as a mean"],
    "weak_item": ["report", "drop only by a rule set in advance"],
    "careless_multi": ["flag and report the share", "exclude by a rule set in advance; report results with and without"],
    "careless_single": ["report only: one index is weak evidence"],
    "careless_strong_single": ["flag and report the share by type", "exclude by a rule set in advance; report results with and without"],
    "speeding": ["flag; exclude by a threshold set in advance (e.g. < 2 s per item)"],
    "stable_field_changed": ["check the linkage (wrong person?)", "keep the value of one wave by a rule", "set to missing"],
    "extreme_change": ["check the linkage of these IDs", "keep; report as genuine change"],
    "small_groups": ["report; merge or drop groups only by a rule set in advance"],
    "aggregation_weak": ["report ICC/rwg and analyse at the individual level", "aggregate anyway with the values reported"],
    "unmatched_ids": ["report attrition", "check ID formats (leading zeros, typos) before accepting it"],
}


def numeric_items(df: pd.DataFrame, V: pd.DataFrame, vmap: dict) -> pd.DataFrame:
    """Items as numbers: text_map applied, missing codes removed. Nothing else is changed."""
    out = {}
    for v, c in vmap.items():
        if V.loc[v, "type"] not in NUMERIC:
            continue
        s = df[c].astype(str).str.strip()
        tmap = {k.strip(): val for k, val in parse_pairs(V.loc[v, "text_map"], "=>").items()}
        s = s.map(lambda x: tmap.get(x, x))
        x = as_number(s)
        codes = [float(z) for z in split_list(V.loc[v, "missing_codes"]) if as_number(pd.Series([z])).notna().iloc[0]]
        if codes:
            x = x.mask(x.isin(codes))
        out[v] = x
    return pd.DataFrame(out, index=df.index)


def cronbach(X: pd.DataFrame) -> float:
    X = X.dropna()
    k = X.shape[1]
    if k < 2 or len(X) < 3:
        return float("nan")
    var_items = X.var(ddof=1).sum()
    var_tot = X.sum(axis=1).var(ddof=1)
    return float(k / (k - 1) * (1 - var_items / var_tot)) if var_tot > 0 else float("nan")


def alpha_ci(a: float, n: int, k: int, level=0.95) -> tuple[float, float]:
    """Feldt (1965) interval."""
    if not np.isfinite(a) or n < 3 or k < 2:
        return (float("nan"), float("nan"))
    d1, d2 = n - 1, (n - 1) * (k - 1)
    lo = 1 - (1 - a) * stats.f.ppf(1 - (1 - level) / 2, d1, d2)
    hi = 1 - (1 - a) * stats.f.ppf((1 - level) / 2, d1, d2)
    return (round(float(lo), 3), round(float(hi), 3))


class Findings:
    def __init__(self, ids):
        self.items, self.ids = [], ids

    def add(self, layer, check, severity, var, rows, evidence, **extra):
        rows = [int(r) for r in rows]
        f = {"id": f"M{len(self.items) + 1:03d}", "layer": layer, "check": check, "severity": severity,
             "var": var, "count": len(rows), "rows": rows[:300], "evidence": evidence,
             "options": OPTIONS.get(check, []), "status": "open"}
        if self.ids is not None and rows:
            f["ids"] = [to_str(self.ids.get(r, "")) for r in rows[:50]]
        f.update(extra)
        self.items.append(f)


def scales_of(V: pd.DataFrame, vmap: dict) -> dict[str, list[str]]:
    sc = {}
    for v in vmap:
        s = str(V.loc[v, "scale"]).strip()
        if s and s.lower() != "nan" and V.loc[v, "type"] in NUMERIC:
            sc.setdefault(s, []).append(v)
    return {k: v for k, v in sc.items() if len(v) >= 3}


def layer6(F: Findings, X: pd.DataFrame, V: pd.DataFrame, scales: dict) -> dict:
    table = {}
    for sc, items in scales.items():
        A = X[items]
        lo = {v: as_number(pd.Series([V.loc[v, "min"]])).iloc[0] for v in items}
        hi = {v: as_number(pd.Series([V.loc[v, "max"]])).iloc[0] for v in items}
        obs = {v: (A[v].min(), A[v].max()) for v in items}
        ranges = {(lo[v] if pd.notna(lo[v]) else obs[v][0], hi[v] if pd.notna(hi[v]) else obs[v][1]) for v in items}
        rr = {r for r in ranges if all(pd.notna(x) for x in r)}
        if rr and (max(r[1] for r in rr) - min(r[1] for r in rr) > 1 or max(r[0] for r in rr) - min(r[0] for r in rr) > 1):
            confirmed = all(str(V.loc[v, "status"]).strip() == "confirmed" for v in items)
            F.add(6, "mixed_item_ranges", "blocking" if confirmed else "decision", sc, [],
                  f"items of '{sc}' sit on different ranges {sorted(map(str, rr))}"
                  + ("" if confirmed else " (observed, not confirmed - an unused end point can cause this)")
                  + ": a mean would weigh them unequally")
        marked = [v for v in items if str(V.loc[v, "reverse"]).strip().lower() in YES]
        comp = A.dropna()
        n = len(comp)
        rec = {"items": len(items), "n_complete": n, "reverse_marked": marked}
        if n < 10:
            rec["note"] = "fewer than 10 complete rows: not estimated"
            table[sc] = rec
            continue
        distinct = (comp.nunique(axis=1) == comp.shape[1]).mean()
        if distinct >= 0.9 and comp.shape[1] >= 3:
            rec["note"] = (f"{distinct:.0%} of rows give every item a different value: ranking (ipsative) items - "
                           "alpha and reversal checks do not apply")
            table[sc] = rec
            F.add(6, "ranking_items", "info", sc, [], rec["note"], scale=sc)
            continue
        # as the data stand (raw), and as the dictionary says they should be scored
        def ir(M):
            return {v: float(M[v].corr(M.drop(columns=[v]).sum(axis=1))) for v in M.columns}
        raw_ir = ir(comp)
        S = comp.copy()
        for v in marked:
            a, b = (lo[v] if pd.notna(lo[v]) else obs[v][0]), (hi[v] if pd.notna(hi[v]) else obs[v][1])
            S[v] = a + b - S[v]
        sc_ir = ir(S)
        a_raw, a_scored = cronbach(comp), cronbach(S)
        rec.update(alpha_raw=round(a_raw, 3), alpha_scored=round(a_scored, 3),
                   alpha_ci=alpha_ci(a_scored, n, len(items)),
                   item_rest_raw={v: round(r, 3) for v, r in raw_ir.items()},
                   item_rest_scored={v: round(r, 3) for v, r in sc_ir.items()},
                   alpha_if_deleted={v: round(cronbach(S.drop(columns=[v])), 3) for v in items})
        table[sc] = rec
        hyp = [v for v in items if str(V.loc[v, "reverse"]).strip() == "?"]
        if hyp:                                     # arbitrate a wording-based hypothesis with the data
            H = S.copy()
            for v in hyp:
                H[v] = H[v].max() + H[v].min() - H[v]
            a_h = cronbach(H)
            verdict = ("supports reversal" if a_h - a_scored >= 0.05 else
                       "argues against reversal" if a_scored - a_h >= 0.05 else "is neutral")
            rec["reverse_hypothesis"] = {"items": hyp, "alpha_as_is": round(a_scored, 3), "alpha_reversed": round(a_h, 3),
                                         "verdict": verdict}
            basis = ("negative wording" if any("negatively worded" in str(V.loc[v, "evidence"]) for v in hyp)
                     else "the draft dictionary (a negative correlation with the scale)")
            F.add(6, "reverse_hypothesis", "decision", ", ".join(hyp), [],
                  f"{basis} suggests reverse keying of {hyp}; alpha {a_scored:.2f} as scored vs {a_h:.2f} with them "
                  f"reversed - the data {verdict}. Decide from the source instrument; the data only arbitrate",
                  scale=sc, alpha_as_is=round(a_scored, 3), alpha_reversed=round(a_h, 3))
        # judge the other items against the scale as it would be scored if a supported hypothesis holds:
        # otherwise the still-unreversed hypothesis items contaminate every rest score (bfi E4, 17.11.26)
        R_, a_ref = S, a_scored
        if hyp and rec["reverse_hypothesis"]["verdict"] == "supports reversal":
            R_, a_ref = H, a_h
        ref_ir = ir(R_)
        for v in items:
            if v in hyp:
                continue
            if v in marked:
                if raw_ir[v] > 0.1 and sc_ir[v] < 0:
                    F.add(6, "reverse_already_applied", "blocking", v, [],
                          f"'{v}' is marked reverse, but correlates positively with its scale as it stands "
                          f"(item-rest r={raw_ir[v]:.2f}) and negatively once reversed (r={sc_ir[v]:.2f}): "
                          "it may already be reversed in this file, or it is not reverse-keyed", scale=sc)
                elif sc_ir[v] < 0.1:
                    F.add(6, "reverse_not_supported", "decision", v, [],
                          f"'{v}' is marked reverse; even after reversal its item-rest r is {sc_ir[v]:.2f}", scale=sc)
            elif ref_ir[v] < -0.1 and cronbach(R_.assign(**{v: R_[v].max() + R_[v].min() - R_[v]})) - a_ref >= 0.05:
                F.add(6, "reverse_suspected", "decision", v, [],
                      f"'{v}' correlates negatively with the rest of '{sc}' (item-rest r={ref_ir[v]:.2f}) and is not "
                      "marked reverse - a reverse-keyed item, a coding error, or a scale made of two subscales (check the source)",
                      scale=sc, alpha_if_reversed=round(cronbach(R_.assign(**{v: R_[v].max() + R_[v].min() - R_[v]})), 3))
            elif sc_ir[v] < 0.2 and np.isfinite(a_scored) and a_scored >= 0.5:
                F.add(6, "weak_item", "info", v, [], f"item-rest r={sc_ir[v]:.2f} in '{sc}' (alpha without it: "
                      f"{rec['alpha_if_deleted'][v]:.2f})", scale=sc)
        if np.isfinite(a_scored) and a_scored < 0.6:
            F.add(6, "low_reliability", "decision", sc, [],
                  f"alpha={a_scored:.2f} (95% CI {rec['alpha_ci'][0]}-{rec['alpha_ci'][1]}), n={n}, {len(items)} items",
                  scale=sc)
    return table


def longstring(row: np.ndarray) -> int:
    best = cur = 0
    prev = None
    for x in row:
        if np.isnan(x):
            prev, cur = None, 0
            continue
        cur = cur + 1 if x == prev else 1
        prev = x
        best = max(best, cur)
    return best


def scored(X: pd.DataFrame, V: pd.DataFrame, scales: dict) -> pd.DataFrame:
    """Items as the dictionary says they are scored (reverse = min + max - x)."""
    S = X.copy()
    for vs in scales.values():
        for v in vs:
            if str(V.loc[v, "reverse"]).strip().lower() in YES:
                lo = as_number(pd.Series([V.loc[v, "min"]])).iloc[0]
                hi = as_number(pd.Series([V.loc[v, "max"]])).iloc[0]
                lo = lo if pd.notna(lo) else S[v].min()
                hi = hi if pd.notna(hi) else S[v].max()
                S[v] = lo + hi - S[v]
    return S


def layer5(F: Findings, X: pd.DataFrame, scales: dict, df: pd.DataFrame, duration: str | None,
           rel: dict | None = None) -> dict:
    items = [v for vs in scales.values() for v in vs]
    if not items:
        return {}
    M = X[items]
    answered = M.notna().sum(axis=1)
    use = answered >= max(5, 0.5 * len(items))
    flags = {}
    # longstring over the survey order
    ls = M[use].apply(lambda r: longstring(r.to_numpy(dtype=float)), axis=1)
    q1, q3 = ls.quantile(.25), ls.quantile(.75)
    cut_ls = max(q3 + 3 * (q3 - q1), 10)
    flags["longstring"] = set(ls.index[ls > cut_ls])
    # Mahalanobis D2 on the items of reliable scales: random answers break the items' correlations
    good = [sc for sc in scales if rel is None or (rel.get(sc, {}).get("alpha_scored") or 0) >= 0.6]
    good_items = [v for sc in good for v in scales[sc]]
    md_info = {}
    if good_items and len(good_items) * 5 < use.sum():
        Ssc = X.loc[use, good_items].dropna()
        level = "items"
    else:
        Ssc = pd.DataFrame({sc: X[vs].mean(axis=1) for sc, vs in scales.items()})[use].dropna()
        level = "scale means"
    if Ssc.shape[1] >= 2 and len(Ssc) > Ssc.shape[1] + 10:
        C = np.cov(Ssc.to_numpy(dtype=float), rowvar=False)
        Ci = np.linalg.pinv(C)
        D = Ssc.to_numpy(dtype=float) - Ssc.mean().to_numpy()
        d2 = np.einsum("ij,jk,ik->i", D, Ci, D)
        p = 1 - stats.chi2.cdf(d2, Ssc.shape[1])
        flags["mahalanobis"] = set(Ssc.index[p < 0.01])    # lenient: one index alone never flags
        md_info = {"df": Ssc.shape[1], "level": level, "scales": good if level == "items" else list(scales),
                   "threshold": "p < .01"}
    # even-odd consistency across scales with >= 4 items
    eo_sc = {sc: vs for sc, vs in scales.items() if len(vs) >= 4
             and (rel is None or (rel.get(sc, {}).get("alpha_scored") or 0) >= 0.6)}
    if len(eo_sc) >= 3:
        E = pd.DataFrame({sc: X[vs[0::2]].mean(axis=1) for sc, vs in eo_sc.items()})
        O = pd.DataFrame({sc: X[vs[1::2]].mean(axis=1) for sc, vs in eo_sc.items()})
        r = []
        for idx in E.index[use]:
            e, o = E.loc[idx], O.loc[idx]
            ok = e.notna() & o.notna()
            if ok.sum() >= 3 and e[ok].std() > 0 and o[ok].std() > 0:
                rr = np.corrcoef(e[ok], o[ok])[0, 1]
                r.append((idx, 2 * rr / (1 + rr) if rr > -1 else -1))
        eo = pd.Series(dict(r))
        flags["even_odd"] = set(eo.index[eo < 0.3])     # Huang et al. (2012) cut-off; one index alone never flags
    # speed
    speed_info = {}
    if duration and duration in df.columns:
        dur = as_number(df[duration].astype(str))
        per_item = dur / answered.replace(0, np.nan)
        fast = per_item[use] < 2.0
        flags["speed"] = set(per_item[use].index[fast.fillna(False)])
        speed_info = {"column": duration, "median_seconds_per_item": round(float(per_item[use].median()), 2)}
        if flags["speed"]:
            F.add(5, "speeding", "decision", duration, sorted(flags["speed"]),
                  f"less than 2 seconds per answered item (median here {speed_info['median_seconds_per_item']} s)")
    # one index can be strong evidence on its own (decision of 17.11.26): straightliners and random responders
    # are caught by DIFFERENT indices (Curran, 2016; Meade & Craig, 2012), so "two agree" misses both types.
    strong = {}
    maxlen = max(len(vs) for vs in scales.values())
    for r in ls.index[(ls > cut_ls) & (ls >= max(10, 2 * maxlen))]:
        strong[r] = "straightlining: longstring %d, at least twice the longest scale (%d items)" % (ls[r], maxlen)
    A_ = X.loc[use, items].dropna()
    n_, p_ = A_.shape
    if p_ >= 5 and n_ >= 2 * p_:                       # all items, exact distribution of the sample D2
        Ca = np.linalg.pinv(np.cov(A_.to_numpy(dtype=float), rowvar=False))
        Da = A_.to_numpy(dtype=float) - A_.mean().to_numpy()
        d2a = np.einsum("ij,jk,ik->i", Da, Ca, Da)
        pa = 1 - stats.beta.cdf(n_ * d2a / (n_ - 1) ** 2, p_ / 2, (n_ - p_ - 1) / 2)   # Wilks (1963)
        flags["mahalanobis_all_items"] = set(A_.index[pa < 0.001])
        for r in A_.index[pa < 0.001]:
            strong.setdefault(r, "random responding: D2 on all items p < .001 (exact Beta distribution)")
        md_info["all_items"] = {"df": p_, "n": n_, "threshold": "p < .001, exact (Wilks, 1963)",
                                "flagged": len(flags["mahalanobis_all_items"])}
    hits = Counter(r for s in flags.values() for r in s)
    multi = sorted(r for r, k in hits.items() if k >= 2)
    single = sorted(r for r, k in hits.items() if k == 1 and r not in strong)
    strong_only = sorted(r for r in strong if r not in multi)
    which = {int(r): sorted(k for k, s in flags.items() if r in s) for r in multi + single + strong_only}
    if multi:
        F.add(5, "careless_multi", "decision", "", multi,
              "two or more careless-responding indices agree", indices={r: which[r] for r in multi})
    if strong_only:
        F.add(5, "careless_strong_single", "decision", "", strong_only,
              "one index, at an extreme level that is strong evidence on its own - the type is named per respondent",
              types={int(r): strong[r] for r in strong_only})
    if single:
        F.add(5, "careless_single", "info", "", single, "one careless-responding index only",
              indices={r: which[r] for r in single})
    return {"indices": {k: len(v) for k, v in flags.items()}, "longstring_cut": float(cut_ls),
            "mahalanobis": md_info, "speed": speed_info, "rows_assessed": int(use.sum())}


def layer7(F: Findings, A: dict, B: dict) -> dict:
    """A, B: {'df','X','V','vmap','id','scales','label'} for wave 1 and wave 2."""
    ka = A["df"][A["id"]].astype(str).str.strip()
    kb = B["df"][B["id"]].astype(str).str.strip()
    ka, kb = ka[ka != ""], kb[kb != ""]
    ka = ka[~ka.duplicated(keep=False)]
    kb = kb[~kb.duplicated(keep=False)]               # duplicated IDs cannot be linked safely
    shared = sorted(set(ka) & set(kb))
    ia = pd.Series(ka.index, index=ka.values)
    ib = pd.Series(kb.index, index=kb.values)
    info = {"ids_t1": int(len(ka)), "ids_t2": int(len(kb)), "matched": len(shared)}
    only_a, only_b = sorted(set(ka) - set(kb)), sorted(set(kb) - set(ka))
    if only_a or only_b:
        F.add(7, "unmatched_ids", "info", f"{A['id']} / {B['id']}", list(ia[only_a].values)[:300],
              f"{len(shared)} matched; {len(only_a)} only in {A['label']}, {len(only_b)} only in {B['label']} "
              "(unique IDs only; duplicated IDs left out of linkage)")
    if len(shared) < 10:
        return info
    ra, rb = ia[shared].values, ib[shared].values
    # stable-looking fields: same variable in both dictionaries, few categories
    common = [v for v in A["vmap"] if v in B["vmap"] and A["V"].loc[v, "type"] in ("categorical", "binary")]
    stab = {}
    for v in common:
        x = A["df"].loc[ra, A["vmap"][v]].astype(str).str.strip().to_numpy()
        y = B["df"].loc[rb, B["vmap"][v]].astype(str).str.strip().to_numpy()
        ok = (x != "") & (y != "")
        if ok.sum() < 10:
            continue
        agree = float((x[ok] == y[ok]).mean())
        stab[v] = round(agree, 3)
        if 0.9 <= agree < 1:
            bad = [int(r) for r, keep in zip(rb, ok & (x != y)) if keep]
            F.add(7, "stable_field_changed", "decision", v, bad,
                  f"'{v}' agrees across waves for {agree:.0%} of matched people - the exceptions are suspicious "
                  "(a typo, or a wrong link)", rows_are=B["label"])
    info["agreement_by_field"] = stab
    # scale scores: test-retest, and change across ALL reliable scales at once (a different person under
    # one ID changes everywhere; genuine change is usually specific)
    trt, Z = {}, {}
    SA, SB = scored(A["X"], A["V"], A["scales"]), scored(B["X"], B["V"], B["scales"])
    for sc in A["scales"]:
        if sc not in B["scales"]:
            continue
        sa = SA[A["scales"][sc]].mean(axis=1).loc[ra].to_numpy()
        sb = SB[B["scales"][sc]].mean(axis=1).loc[rb].to_numpy()
        ok = ~np.isnan(sa) & ~np.isnan(sb)
        if ok.sum() < 10:
            continue
        trt[sc] = round(float(np.corrcoef(sa[ok], sb[ok])[0, 1]), 3)
        if trt[sc] >= 0.3:
            d = sb - sa
            Z[sc] = (d - np.nanmean(d[ok])) / np.nanstd(d[ok])
    info["test_retest"] = trt
    if len(Z) >= 2:
        Zm = np.vstack(list(Z.values())).T
        k = (~np.isnan(Zm)).sum(axis=1)
        chi = np.nansum(Zm ** 2, axis=1)
        p = np.where(k >= 2, 1 - stats.chi2.cdf(chi, np.maximum(k, 1)), 1.0)
        extreme = sorted(int(r) for r, pp in zip(rb, p) if pp < 0.001)
        info["change_test"] = {"scales": list(Z), "rule": "sum of squared change z-scores ~ chi2(k), p < .001"}
        if extreme:
            F.add(7, "extreme_change", "decision", ", ".join(Z), extreme,
                  "change between waves is extreme on the reliable scales taken together (p < .001): genuine "
                  "change, or two different people under one ID", rows_are=B["label"])
    return info


def layer7_groups(F: Findings, A: dict, group: str, rwg_null: str = "uniform") -> dict:
    """ICC(1), ICC(2) and rwg(j) per scale - only when the project aggregates individuals to groups."""
    g = A["df"][group].astype(str).str.strip()
    out = {"group_column": group, "groups": int(g[g != ""].nunique()), "sizes": {}}
    sizes = g[g != ""].value_counts()
    out["sizes"] = {"min": int(sizes.min()), "median": float(sizes.median()), "max": int(sizes.max())}
    small = sizes[sizes < 3]
    if len(small):
        F.add(7, "small_groups", "decision", group, list(A["df"].index[g.isin(small.index)]),
              f"{len(small)} groups with fewer than 3 members: group means and rwg are unstable there")
    res = {}
    for sc, items in A["scales"].items():
        X = A["X"][items]
        score = X.mean(axis=1)
        d = pd.DataFrame({"g": g, "y": score}).dropna()
        d = d[d.g != ""]
        grp = d.groupby("g")["y"]
        k = grp.size()
        if len(k) < 3 or (k >= 2).sum() < 3:
            continue
        n, J = len(d), len(k)
        ms_b = (k * (grp.mean() - d.y.mean()) ** 2).sum() / (J - 1)
        ms_w = ((d.y - grp.transform("mean")) ** 2).sum() / (n - J)
        k0 = (n - (k ** 2).sum() / n) / (J - 1)
        icc1 = (ms_b - ms_w) / (ms_b + (k0 - 1) * ms_w) if ms_b + (k0 - 1) * ms_w > 0 else float("nan")
        icc2 = (ms_b - ms_w) / ms_b if ms_b > 0 else float("nan")
        lo = as_number(pd.Series([A["V"].loc[items[0], "min"]])).iloc[0]
        hi = as_number(pd.Series([A["V"].loc[items[0], "max"]])).iloc[0]
        a_opts = (hi - lo + 1) if pd.notna(lo) and pd.notna(hi) else X.stack().nunique()
        s2_eu = (a_opts ** 2 - 1) / 12                     # uniform null (James, Demaree & Wolf, 1984)
        rwg = []
        for gg, rows in X.groupby(g).groups.items():
            if gg == "" or len(rows) < 2:
                continue
            sub = X.loc[rows].dropna()
            if len(sub) < 2:
                continue
            mv = sub.var(ddof=1).mean()
            J_ = len(items)
            r = (J_ * (1 - mv / s2_eu)) / (J_ * (1 - mv / s2_eu) + mv / s2_eu)
            rwg.append(min(max(r, 0), 1))
        res[sc] = {"ICC1": round(float(icc1), 3), "ICC2": round(float(icc2), 3),
                   "rwg_mean": round(float(np.mean(rwg)), 3) if rwg else None, "groups": J, "null": rwg_null}
        if np.isfinite(icc1) and (icc1 < 0.05 or (rwg and np.mean(rwg) < 0.7)):
            F.add(7, "aggregation_weak", "decision", sc, [],
                  f"ICC(1)={icc1:.2f}, ICC(2)={icc2:.2f}, mean rwg(j)={np.mean(rwg) if rwg else float('nan'):.2f}: "
                  "group means of this scale are weakly justified (conventional guides: ICC(1) > .05, rwg > .70) - "
                  "the project decides whether to aggregate, and reports these values either way", scale=sc)
    out["scales"] = res
    return out


def prepare(path, dict_path, label, id_col):
    L = load_table(path)
    df = L.df.set_index(ROWKEY, drop=False)
    V, _ = read_dictionary(dict_path)
    label = label or os.path.basename(path)
    vmap = resolve_source(V, label, [c for c in df.columns if c != ROWKEY])
    V = V.set_index("var")
    if not id_col:
        ids = [v for v in vmap if V.loc[v, "type"] == "id"]
        id_col = vmap[ids[0]] if ids else None
    X = numeric_items(df, V, vmap)
    V, rstate = reverse_state(path, V, vmap)
    return {"df": df, "X": X, "V": V, "vmap": vmap, "id": id_col, "scales": scales_of(V, vmap), "label": label,
            "reverse_state": rstate}


def reverse_state(path, V, vmap):
    """Reconcile the dictionary's reverse keys with reversals the log already applied to this file.

    The dictionary describes the instrument; the log describes the file. When the file is a version in a
    work folder, an item marked reverse that the log already reversed must not be reversed again (scoring it
    twice restores the original direction and silently breaks the scale). Returns an effective copy of the
    dictionary for scoring and a record per affected item; contract gap 3, 17.11.26.
    """
    from versioning import applied_reversals, work_of
    work, ver = work_of(path)
    if not work:
        return V, {}
    done = applied_reversals(work, ver)
    V = V.copy()
    rec = {}
    for v, c in vmap.items():
        if c not in done:
            continue
        d = done[c]
        marked = str(V.loc[v, "reverse"]).strip().lower()
        odd = d["full"] % 2 == 1
        state = {"column": c, "dictionary": marked or "no", "applied_full": d["full"], "applied_partial": d["partial"]}
        if d["partial"]:
            state["effective"] = "conflict"
            state["why"] = "reversed for some rows only: scoring cannot know which rows are in which direction"
        elif marked in YES and odd:
            V.loc[v, "reverse"] = "no"
            state["effective"] = "already reversed in the file - not reversed again"
        elif marked == "?" and odd:
            V.loc[v, "reverse"] = "no"
            state["effective"] = "reversed in the file while the dictionary still says '?': treated as reversed"
        elif odd:
            state["effective"] = "conflict"
            state["why"] = "the log reversed it, the dictionary says it is not reverse-keyed"
        else:
            state["effective"] = "reversed an even number of times: back to the original direction"
        rec[v] = state
    return V, rec


def report_reverse_state(F, rstate):
    for v, st in rstate.items():
        if st["effective"] == "conflict":
            F.add(6, "reverse_log_conflict", "blocking", v, [],
                  f"'{v}' ({st['column']}): {st['why']} - correct the dictionary or the log before any scale is scored")
        elif st["applied_full"]:
            F.add(6, "reverse_applied_in_log", "info", v, [],
                  f"'{v}': {st['effective']} (log: {st['applied_full']} full reversal(s); dictionary: {st['dictionary']})")


def main():
    from versioning import work_of
    from da_common import sha256_file
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--dict", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label")
    ap.add_argument("--id")
    ap.add_argument("--duration")
    ap.add_argument("--t2")
    ap.add_argument("--t2-dict")
    ap.add_argument("--t2-label")
    ap.add_argument("--t2-id")
    ap.add_argument("--group", help="group column; ICC/rwg run only when the project aggregates to groups")
    ap.add_argument("--project", help="run folder with project.json (supplies group / aggregation settings)")
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    A = prepare(a.data, a.dict, a.label, a.id)
    group, null = a.group, "uniform"
    if a.project:
        from project import get
        if get(a.project, "aggregate_to_group") in ("yes", True) and not group:
            nested = get(a.project, "nested")
            group = nested if nested not in (None, "no", False) else None
        null = get(a.project, "rwg_null", "uniform") or "uniform"
    F = Findings(A["df"][A["id"]] if A["id"] else None)
    report_reverse_state(F, A["reverse_state"])
    rel = layer6(F, A["X"], A["V"], A["scales"])
    dur = a.duration or next((c for c in A["df"].columns if "duration" in c.lower()), None)
    care = layer5(F, scored(A["X"], A["V"], A["scales"]), A["scales"], A["df"], dur, rel)
    lon = {}
    if a.t2:
        B = prepare(a.t2, a.t2_dict or a.dict, a.t2_label, a.t2_id or a.id)
        report_reverse_state(F, B["reverse_state"])
        lon = layer7(F, A, B)
    grp = layer7_groups(F, A, group, null) if group and group in A["df"].columns else {}
    res = {"file": A["label"], "rows": int(len(A["df"])), "id_column": A["id"], "groups": grp,
           "summary": dict(Counter(f["severity"] for f in F.items)), "reliability": rel,
           "careless": care, "longitudinal": lon, "findings": F.items,
           # contract for downstream analysis (stat-analysis): which file this describes, and reverse keys as applied
           "data_sha256": sha256_file(a.data), "version": work_of(a.data)[1],
           "reverse_state": A["reverse_state"]}
    write_json(os.path.join(a.out, "measure.json"), res)
    from report import render_measure
    open(os.path.join(a.out, f"measure_{a.lang}.md"), "w", encoding="utf-8").write(render_measure(res, a.lang))
    print(os.path.join(a.out, f"measure_{a.lang}.md"))


if __name__ == "__main__":
    main()
