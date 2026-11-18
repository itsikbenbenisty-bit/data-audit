#!/usr/bin/env python3
"""Layer 7 for time-stamped data: wearables, sensors, logs, daily summaries.

  sensor.py PATHS... --out DIR [--time COL] [--entity COL] [--glob PATTERN] [--lang he|en]

PATHS are files or folders (csv / xlsx / json lists, e.g. a Garmin export). All matching files are
stacked with a `_source` column, so overlapping exports are compared rather than silently merged.
Nothing is changed. Checks:
  time        unparseable or mixed timestamps; local-vs-UTC offsets (odd offsets, jumps = travel/DST)
  duplicates  one entity, one timestamp, several records - identical or conflicting
  overlap     several sources cover the same time with different values (re-exports, revised data)
  gaps        intervals far above the usual sampling interval; changes of sampling rate
  values      physically impossible values by measure; sentinel codes (-1, -2, 255, 65535)
  stuck       a continuous measure repeating one exact value over many consecutive records
  coverage    records that cover part of the period only (wear time), empty records
  logic       min <= resting <= max heart rate; distance per step; activity time within the period
  identity    identifier fields carried in every record (profile keys, device ids, uuids)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from da_common import write_json  # noqa: E402

# (name pattern, low, high, unit) - plausible physical ranges; checked only when the name matches
BOUNDS = [
    (r"resting.?heart|restinghr|rhr", 25, 120, "bpm"),
    (r"heart.?rate|^hr$|_hr$|bpm|pulse(?!ox)", 25, 230, "bpm"),
    (r"spo2|pulse.?ox|oxygen", 70, 100, "%"),
    (r"(total)?steps?$|step.?count", 0, 100000, "steps/day"),
    (r"distance", 0, 300000, "m/day"),
    (r"kilocal|calor", 0, 12000, "kcal/day"),
    (r"stress", 0, 100, "score"),
    (r"body.?battery", 0, 100, "score"),
    (r"respiration|breath", 4, 60, "breaths/min"),
    (r"temperature|temp_c|skin.?temp", 25, 43, "°C"),
    (r"(?<!milli)seconds$", 0, 86400 * 1.05, "s/day"),
    (r"weight.?kg|body.?mass", 25, 300, "kg"),
]
SENTINELS = {-1, -2, -99, -999, 255, 65535, 9999}
TIME_HINT = re.compile(r"(calendardate|timestamp|datetime|^date$|^time$|starttime|gmt|local|recorded)", re.I)
ID_HINT = re.compile(r"(userprofile|profilepk|user.?id|device.?id|serial|uuid|email|imei|mac.?address)", re.I)


class F:
    def __init__(self):
        self.items = []

    def add(self, check, severity, var, count, evidence, **extra):
        self.items.append({"id": f"S{len(self.items) + 1:03d}", "layer": 7, "check": check, "severity": severity,
                           "var": var, "count": int(count), "evidence": evidence, "status": "open", **extra})


def read_any(path: str) -> pd.DataFrame:
    if path.lower().endswith(".json"):
        obj = json.load(open(path, encoding="utf-8"))
        if isinstance(obj, dict):
            lists = [v for v in obj.values() if isinstance(v, list)]
            obj = max(lists, key=len) if lists else [obj]
        return pd.json_normalize(obj) if obj else pd.DataFrame()
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    return pd.read_csv(path, low_memory=False)


def collect(paths, pattern):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, fs in os.walk(p):
                files += [os.path.join(root, f) for f in fs if fnmatch.fnmatch(f, pattern)]
        else:
            files.append(p)
    frames = []
    for f in sorted(files):
        d = read_any(f)
        if len(d):
            d["_source"] = os.path.relpath(f, os.path.commonpath(files) if len(files) > 1 else os.path.dirname(f))
            frames.append(d)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame(), files


def pick_time(df, wanted):
    if wanted:
        return wanted
    cands = [c for c in df.columns if TIME_HINT.search(c)]
    best, score = None, -1
    for c in cands:
        t = pd.to_datetime(df[c], errors="coerce", format="mixed", utc=True)
        s = t.notna().mean() - (0.5 if re.search(r"(^end|_end|end(time|date|gmt|local))", c, re.I) else 0) \
            + (0.3 if "calendardate" in c.lower() else 0)
        if s > score:
            best, score = c, s
    return best


def to_time(s: pd.Series) -> pd.Series:
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().mean() > 0.9:                          # epoch: seconds or milliseconds
        unit = "ms" if num.median() > 1e11 else "s"
        return pd.to_datetime(num, unit=unit, errors="coerce", utc=True)
    return pd.to_datetime(s, errors="coerce", format="mixed", utc=True)


def run(paths, pattern, time_col, entity):
    df, files = collect(paths, pattern)
    out = F()
    info = {"files": len(files), "records": int(len(df))}
    if df.empty:
        return info, out.items
    tc = pick_time(df, time_col)
    info["time_column"] = tc
    t = to_time(df[tc])
    bad = df[tc].notna() & t.isna()
    if bad.any():
        out.add("bad_time", "blocking", tc, bad.sum(), "timestamps that do not parse",
                examples=df.loc[bad, tc].astype(str).head(5).tolist())
    df = df.assign(_t=t)
    ent = entity if entity in df.columns else None
    if ent:
        info["entities"] = int(df[ent].nunique())
    keys = [ent, "_t"] if ent else ["_t"]
    num_cols = [c for c in df.columns if c not in ("_t", "_source", tc) and pd.api.types.is_numeric_dtype(df[c])
                and not pd.api.types.is_bool_dtype(df[c]) and not ID_HINT.search(c)
                and not re.search(r"(timestamp|time$|date$|^includes|^is[A-Z_]|version)", c.split(".")[-1])
                and not (pd.to_numeric(df[c], errors="coerce").abs() > 1e9).mean() > 0.5]
    # identity fields
    ids = [c for c in df.columns if ID_HINT.search(c) and df[c].notna().any()]
    if ids:
        out.add("identifier_fields", "decision", ", ".join(ids[:8]), len(ids),
                "fields that identify the person or device travel with every record: drop or pseudonymise before sharing")
    # duplicates and overlap between sources
    d = df[df["_t"].notna()]
    dup = d[d.duplicated(keys, keep=False)]
    if len(dup):
        g = dup.groupby(keys, dropna=False)
        conflict_fields, n_conf, n_same, per_src = Counter(), 0, 0, Counter()
        for _, grp in g:
            diff = [c for c in num_cols if grp[c].nunique(dropna=True) > 1]
            if diff:
                n_conf += 1
                conflict_fields.update(diff)
            else:
                n_same += 1
            if grp["_source"].nunique() > 1:
                per_src[" | ".join(sorted(grp["_source"].unique())[:2])] += 1
        if n_same:
            out.add("duplicate_records_identical", "decision", tc, n_same,
                    "the same time stamp appears more than once with identical values (overlapping files or exports): keep one")
        if n_conf:
            out.add("duplicate_records_conflicting", "blocking", tc, n_conf,
                    "the same time stamp appears more than once with DIFFERENT values: a revised re-export, two devices, "
                    "or a merge error - choose a rule (latest export? the source the device syncs to?) before analysis",
                    fields=dict(conflict_fields.most_common(12)), source_pairs=dict(per_src.most_common(5)))
    # gaps and sampling
    u = d.drop_duplicates(keys).sort_values(keys)
    gaps_total, longest, interval = 0, None, None
    for _, grp in (u.groupby(ent) if ent else [(None, u)]):
        dt = grp["_t"].diff().dropna()
        if len(dt) < 5:
            continue
        med = dt.median()
        interval = med
        big = dt[dt > 3 * med]
        gaps_total += len(big)
        if len(big):
            i = big.idxmax()
            cand = (grp.loc[i, "_t"] - big.max(), grp.loc[i, "_t"], big.max())
            longest = cand if longest is None or cand[2] > longest[2] else longest
        by_month = dt.groupby(grp["_t"].iloc[1:].dt.tz_localize(None).dt.to_period("M").values).median()
        if by_month.nunique() > 1 and (by_month.max() / by_month.min()) >= 2:
            out.add("sampling_change", "decision", tc, by_month.nunique(),
                    f"the usual interval changes over time ({by_month.min()} .. {by_month.max()}): device or setting change")
    info["usual_interval"] = str(interval) if interval is not None else None
    info["span"] = [str(u["_t"].min()), str(u["_t"].max())]
    if gaps_total:
        out.add("gaps", "decision", tc, gaps_total,
                f"intervals longer than 3x the usual one ({interval}); longest {longest[2]} "
                f"from {longest[0]} to {longest[1]}" if longest else "gaps")
    # time zones: local vs GMT pairs
    for lc in [c for c in df.columns if re.search(r"local", c, re.I)]:
        gc = next((c for c in df.columns if c.lower() == re.sub("local", "gmt", lc, flags=re.I).lower()), None)
        if not gc:
            continue
        a = pd.to_datetime(df[lc], errors="coerce", format="mixed")
        b = pd.to_datetime(df[gc], errors="coerce", format="mixed")
        off = ((a.dt.tz_localize(None) if getattr(a.dt, "tz", None) else a) -
               (b.dt.tz_localize(None) if getattr(b.dt, "tz", None) else b)).dt.total_seconds() / 3600
        off = off.dropna()
        if off.empty:
            continue
        counts = off.round(2).value_counts()
        odd = off[(off * 4) % 1 != 0]
        info.setdefault("utc_offsets_hours", {})[lc] = {str(k): int(v) for k, v in counts.head(6).items()}
        if len(odd):
            out.add("odd_utc_offset", "blocking", lc, len(odd), "local minus UTC is not a whole quarter hour: corrupted times")
        if len(counts) > 2:
            out.add("timezone_changes", "info", lc, len(counts),
                    f"{len(counts)} different UTC offsets ({', '.join(map(str, counts.index[:6]))} h): daylight saving, travel, "
                    "or a device set to the wrong zone - day boundaries move with them")
        break
    # values: bounds and sentinels
    for c in num_cols:
        x = pd.to_numeric(u[c], errors="coerce").dropna()
        if x.empty:
            continue
        name = c.split(".")[-1]
        neg = {v for v in SENTINELS if v < 0} if (x >= 0).mean() > 0.9 else set()
        spikes = set()
        for v in SENTINELS - neg:                 # a positive code counts only if it spikes above its neighbours
            k = int((x == v).sum())
            near = int(((x - v).abs().between(1, 5)).sum()) / 10
            if k >= 3 and k > 5 * max(near, 0.2):
                spikes.add(v)
        sent = x[x.isin(neg | spikes)]
        if len(sent) and len(sent) < 0.5 * len(x):
            vc = sent.value_counts()
            out.add("sentinel_values", "decision", c, len(sent),
                    f"codes {sorted(map(int, vc.index))} look like 'no measurement', not values: set to missing",
                    counts={str(int(k)): int(v) for k, v in vc.items()})
            x = x[~x.isin(SENTINELS)]
        for pat, lo, hi, unit in BOUNDS:
            if re.search(pat, name, re.I):
                oob = x[(x < lo) | (x > hi)]
                if len(oob):
                    out.add("impossible_values", "blocking", c, len(oob),
                            f"outside {lo}-{hi} {unit} (physically implausible for '{name}')",
                            examples=sorted(map(float, oob.head(8))))
                break
    # stuck values
    for c in num_cols:
        x = pd.to_numeric(u[c], errors="coerce")
        if x.notna().sum() < 20 or x.nunique() < 10:
            continue
        if x.value_counts(normalize=True).iloc[0] > 0.8:      # constant by design (a goal, a period length, a profile value)
            continue
        run_id = (x != x.shift()).cumsum()
        runs = x.groupby(run_id).agg(["first", "size"])
        p = float((x == x.shift()).iloc[1:].mean())          # how often this measure repeats from one record to the next
        n = int(x.notna().sum())
        # a run of length L is surprising when n * p^(L-1) < 0.01 (a geometric run-length bound)
        lmin = 5 if p <= 0 else max(5, int(np.ceil(1 + np.log(0.01 / n) / np.log(p)))) if p < 1 else 10 ** 9
        long = runs[(runs["size"] >= lmin) & runs["first"].notna() & (runs["first"] != 0)]
        if len(long):
            out.add("stuck_values", "decision", c, int(long["size"].sum()),
                    f"{len(long)} run(s) of >= {lmin} identical values (this measure repeats {p:.0%} of the time, so "
                    f"runs this long are not expected; longest {int(long['size'].max())}): a stuck sensor, copied values, "
                    "or a default", repeat_rate=round(p, 3))
    # coverage: empty records and partial periods
    core = [c for c in num_cols if re.search(r"step|heart|distance|(?<!sedentary)(?<!sedentary_)active", c, re.I)
            and not re.search(r"sedentary", c, re.I)]
    if not core:
        core = [c for c in num_cols if re.search(r"calor", c, re.I)]
    if core:
        empty = u[core].apply(pd.to_numeric, errors="coerce").fillna(0).eq(0).all(axis=1)
        if empty.any():
            full = ""
            if interval is not None:
                per = interval.total_seconds() / 60
                sed = [c for c in num_cols if re.search(r"sedentary", c, re.I)]
                if sed:
                    k = int((pd.to_numeric(u.loc[empty, sed[0]], errors="coerce") >= per).sum())
                    full = f"; in {k} of them '{sed[0]}' fills the whole period - the device lay unused"
            out.add("empty_records", "decision", tc, int(empty.sum()),
                    f"records with zero/no {', '.join(c.split('.')[-1] for c in core[:4])}: device not worn or not synced - "
                    f"missing, not zero{full}", rows=[int(r) + 1 for r in u.index[empty]][:2000])
    dur = next((c for c in num_cols if re.search(r"durationinmilliseconds|wear", c, re.I)), None)
    if dur and interval is not None:
        frac = pd.to_numeric(u[dur], errors="coerce") / (interval.total_seconds() * (1000 if "milli" in dur.lower() else 1))
        part = frac[(frac > 0) & (frac < 0.9)]
        if len(part):
            out.add("partial_coverage", "info", dur, len(part),
                    f"records covering less than 90% of their period (median {part.median():.0%}): daily totals understate")
    # logic
    def col(p):
        return next((c for c in num_cols if re.search(p, c.split(".")[-1], re.I)), None)
    mn, rs, mx = col(r"^minheartrate$"), col(r"^restingheartrate$"), col(r"^maxheartrate$")
    if mn and mx:
        a, b = pd.to_numeric(u[mn], errors="coerce"), pd.to_numeric(u[mx], errors="coerce")
        badl = (a > b) | ((pd.to_numeric(u[rs], errors="coerce") > b) if rs else False)
        if badl.any():
            out.add("logic_heart_rate", "blocking", f"{mn}/{rs}/{mx}", badl.sum(), "minimum or resting heart rate above the maximum")
    st, ds = col(r"^totalsteps$|^steps$"), col(r"^totaldistancemeters$|^distance")
    if st and ds:
        s_, m_ = pd.to_numeric(u[st], errors="coerce"), pd.to_numeric(u[ds], errors="coerce")
        stride = (m_ / s_).where(s_ > 1000)
        badst = stride[(stride < 0.3) | (stride > 2.0)]
        if len(badst):
            out.add("logic_stride", "decision", f"{st}/{ds}", len(badst),
                    "distance per step outside 0.3-2.0 m on days with >1000 steps: a unit change (km vs m), or distance from "
                    "activities without steps (cycling, swimming) - check those days")
    return info, out.items


def stream_lag(paths1, paths2, pattern, time_col, out: F, info: dict, max_lag=60):
    """Two devices measuring the same thing: the lag that best aligns them (clock offset)."""
    a, _ = collect(paths1, pattern)
    b, _ = collect(paths2, pattern)
    ta, tb = pick_time(a, time_col), pick_time(b, time_col)
    common = [c for c in a.columns if c in b.columns and c not in ("_source", ta, tb) and pd.api.types.is_numeric_dtype(a[c])]
    if not common:
        return
    a = a.assign(_t=to_time(a[ta])).dropna(subset=["_t"]).sort_values("_t").set_index("_t")
    b = b.assign(_t=to_time(b[tb])).dropna(subset=["_t"]).sort_values("_t").set_index("_t")
    lags = {}
    for c in common:
        x, y = pd.to_numeric(a[c], errors="coerce"), pd.to_numeric(b[c], errors="coerce")
        j = pd.concat([x.rename("x"), y.rename("y")], axis=1, join="inner").dropna()
        if len(j) < 20:
            continue
        lo, hi = j.x.quantile([.01, .99])                  # robust to single artefacts
        j = j[(j.x.between(lo, hi)) & (j.y.between(*j.y.quantile([.01, .99])))]
        rk = {k: j.x.corr(j.y.shift(-k)) for k in range(-max_lag, max_lag + 1) if abs(k) < len(j) - 10}
        top = max(v for v in rk.values() if pd.notna(v))
        best = min((k for k, v in rk.items() if pd.notna(v) and v >= top - 0.01), key=abs)   # periodic signals: nearest lag
        r0, rb = j.x.corr(j.y), j.x.corr(j.y.shift(-best))
        lags[c] = {"lag_samples": int(best), "r_at_0": round(float(r0), 3), "r_at_lag": round(float(rb), 3)}
        if best != 0 and rb - r0 > 0.1:
            out.add("clock_offset", "blocking", c, abs(best),
                    f"the second stream matches best when shifted by {best} samples (r {r0:.2f} -> {rb:.2f}): the "
                    "device clocks disagree - align before combining", **lags[c])
    info["stream_lags"] = lags


def ema_compliance(df: pd.DataFrame, done: str, sched: str, out: F, info: dict, cut=0.8):
    d, s_ = pd.to_numeric(df[done], errors="coerce"), pd.to_numeric(df[sched], errors="coerce")
    rate = (d / s_.replace(0, np.nan)).dropna()
    info["ema_compliance_median"] = round(float(rate.median()), 3) if len(rate) else None
    low = rate[rate < cut]
    if len(low):
        out.add("low_ema_compliance", "decision", f"{done}/{sched}", len(low),
                f"answered less than {cut:.0%} of the scheduled prompts (median here {rate.median():.0%}): the project's "
                "minimum compliance rule decides who is analysed")


def render(info, items, lang):
    he = lang == "he"
    L = ["# " + ("בדיקת נתוני חיישנים וסדרות זמן" if he else "Sensor / time-series audit"), ""]
    L.append((f"{info.get('files')} קבצים, {info.get('records')} רשומות; עמודת הזמן `{info.get('time_column')}`; "
              f"טווח {info.get('span')}; מרווח רגיל {info.get('usual_interval')}." if he else
              f"{info.get('files')} files, {info.get('records')} records; time column `{info.get('time_column')}`; "
              f"span {info.get('span')}; usual interval {info.get('usual_interval')}."))
    if info.get("utc_offsets_hours"):
        L.append(("היסטים מ-UTC (שעות): " if he else "UTC offsets (hours): ") + str(info["utc_offsets_hours"]))
    L += ["", "| # | בדיקה | חומרה | משתנה | כמות | ראיה |" if he else "| # | Check | Severity | Variable | Count | Evidence |",
          "|---|---|---|---|---|---|"]
    order = {"blocking": 0, "decision": 1, "info": 2}
    for f in sorted(items, key=lambda f: order[f["severity"]]):
        ev = f["evidence"] + "".join(f" — {f[k]}" for k in ("fields", "counts", "examples") if k in f)
        L.append(f"| {f['id']} | {f['check']} | {f['severity']} | {f['var']} | {f['count']} | {ev.replace('|', '/')[:300]} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--time")
    ap.add_argument("--entity")
    ap.add_argument("--glob", default="*")
    ap.add_argument("--stream2", nargs="*", help="a second device's files measuring the same things (clock check)")
    ap.add_argument("--ema", nargs=2, metavar=("COMPLETED", "SCHEDULED"), help="EMA compliance columns")
    ap.add_argument("--ema-min", type=float, default=0.8, help="minimum compliance (a project decision)")
    ap.add_argument("--lang", default="he", choices=["he", "en"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    info, items = run(a.paths, a.glob, a.time, a.entity)
    extra = F()
    if a.stream2:
        stream_lag(a.paths, a.stream2, a.glob, a.time, extra, info)
    if a.ema:
        df_, _ = collect(a.paths, a.glob)
        ema_compliance(df_, a.ema[0], a.ema[1], extra, info, a.ema_min)
    for f in extra.items:
        f["id"] = f"S{len(items) + 1:03d}"
        items.append(f)
    write_json(os.path.join(a.out, "sensor.json"), {"info": info, "findings": items})
    open(os.path.join(a.out, f"sensor_{a.lang}.md"), "w", encoding="utf-8").write(render(info, items, a.lang))
    print(json.dumps({"info": {k: v for k, v in info.items() if k != "utc_offsets_hours"},
                      "findings": Counter(f["check"] for f in items)}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
