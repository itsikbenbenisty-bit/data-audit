#!/usr/bin/env python3
"""Score measure.py on the synthetic two-wave fixture.  usage: score_measure.py measure.json manifest.json"""
import json
import sys

R = json.load(open(sys.argv[1], encoding="utf-8"))
M = json.load(open(sys.argv[2], encoding="utf-8"))
F = R["findings"]


def by(check):
    return [f for f in F if f["check"] == check]


def rows(check):
    return {r for f in by(check) for r in f["rows"]}


res = []
res.append(("P1 unmarked reverse item B3", "B3" in {f["var"] for f in by("reverse_suspected")}))
res.append(("P2 marked reverse but already reversed C2", "C2" in {f["var"] for f in by("reverse_already_applied")}))
res.append(("P3 mixed ranges in D", "D" in {f["var"] for f in by("mixed_item_ranges")}))
res.append(("P4 low reliability E", "E" in {f["var"] for f in by("low_reliability")}))
care = rows("careless_multi")
p5 = len(care & set(M["P5_random_fast_rows"])) / len(M["P5_random_fast_rows"])
p6 = len(care & set(M["P6_straightline_fast_rows"])) / len(M["P6_straightline_fast_rows"])
res.append((f"P5 random fast responders flagged {p5:.0%}", p5 >= 0.8))
res.append((f"P6 straight-liners flagged {p6:.0%}", p6 >= 0.8))
p7 = len(rows("stable_field_changed") & set(M["P7_gender_flip_t2_rows"])) / len(M["P7_gender_flip_t2_rows"])
p8 = len(rows("extreme_change") & set(M["P8_swapped_t2_rows"])) / len(M["P8_swapped_t2_rows"])
res.append((f"P7 gender changed between waves {p7:.0%}", p7 == 1))
res.append((f"P8 different person under one ID {p8:.0%}", p8 >= 0.8))
planted = set(M["P5_random_fast_rows"]) | set(M["P6_straightline_fast_rows"])
fa_care = len(care - planted)
fa_rev = {f["var"] for f in by("reverse_suspected")} - {"B3"}
fa_stab = rows("stable_field_changed") - set(M["P7_gender_flip_t2_rows"])
fa_ext = rows("extreme_change") - set(M["P8_swapped_t2_rows"])
fa = [("careless_multi on clean rows (<= 3 of 280)", fa_care <= 3, fa_care),
      ("reverse_suspected on other items (0)", not fa_rev, sorted(fa_rev)),
      ("stable_field_changed on other rows (0)", not fa_stab, len(fa_stab)),
      ("extreme_change on other rows (<= 2)", len(fa_ext) <= 2, len(fa_ext))]
ok = True
for name, good in res:
    ok &= good
    print(f"{'FOUND ' if good else 'missed'} {name}")
for name, good, n in fa:
    ok &= good
    print(f"{'ok    ' if good else 'FALSE '} {name}: {n}")
print("MEASURE GATE:", "PASS" if ok else "FAIL")
