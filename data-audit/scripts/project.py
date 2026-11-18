#!/usr/bin/env python3
"""The project profile: what THIS study needs, so that project-specific checks run only where they apply.

Some checks are general (duplicates, ranges, verification). Others depend on the study and must not
become defaults: team-level aggregation (ICC/rwg), the k for small-cell privacy, effective-dated HR
records, which identifier links the files, whether exclusion rules were preregistered, the citation
style of the Method section. The skill asks about these at the start and records the answers here,
with who answered and when; every script that runs such a check reads this file and says when a
setting is missing instead of guessing.

  project.py init RUN [--lang he]          writes RUN/project.json with every setting unset ("ask")
  project.py show RUN
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

FIELDS = {
    "language": ("he", "report language: he / en"),
    "design": ("ask", "cross-sectional / longitudinal (waves) / diary-EMA / sensor / records"),
    "id_column": ("ask", "the identifier that links files and waves, per file if it differs"),
    "nested": ("ask", "are respondents nested in groups (teams, units, classes)? the group column, or 'no'"),
    "aggregate_to_group": ("ask", "will scale scores be aggregated to the group level? yes/no - ICC/rwg run only if yes"),
    "rwg_null": ("uniform", "null distribution for rwg (uniform unless the researcher argues for a skewed null)"),
    "quasi_identifiers": ("ask", "columns that together could identify a person (rank, unit, age, gender...)"),
    "k_anonymity": ("ask", "smallest group size that may be reported or shared (often 5; the ethics approval decides)"),
    "hr_records": ("ask", "one row per person, or effective-dated rows (start/end per assignment)?"),
    "asof_date": ("ask", "the date tenure and age are computed at (the extraction date)"),
    "date_order": ("ask", "how dates were typed: day-first / month-first / ISO"),
    "preregistered_rules": ("ask", "exclusion and missing-data rules fixed in advance? where are they written?"),
    "reverse_key_source": ("ask", "where reverse keys come from (instrument manual, publication); data never decide"),
    "citation_style": ("APA 7", "style of the Method section"),
    "method_audience": ("ask", "thesis / journal article / course report - decides how much of the cleaning is reported"),
}


def init(run, lang="he"):
    os.makedirs(run, exist_ok=True)
    p = os.path.join(run, "project.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    prof = {k: {"value": v[0] if k != "language" else lang, "meaning": v[1],
                "set_by": "default" if v[0] != "ask" else None, "date": str(date.today())}
            for k, v in FIELDS.items()}
    json.dump(prof, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return prof


def get(run, key, default=None):
    """Value of a setting, or None when it is still 'ask' (the caller must then skip or ask)."""
    p = os.path.join(run, "project.json") if run else None
    if not p or not os.path.exists(p):
        return default
    v = json.load(open(p, encoding="utf-8")).get(key, {}).get("value", default)
    return None if v == "ask" else v


def set_value(run, key, value, by):
    p = os.path.join(run, "project.json")
    prof = json.load(open(p, encoding="utf-8"))
    prof[key] = {**prof.get(key, {}), "value": value, "set_by": by, "date": str(date.today())}
    json.dump(prof, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    cmd, run = sys.argv[1], sys.argv[2]
    if cmd == "init":
        init(run, sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "he")
    prof = json.load(open(os.path.join(run, "project.json"), encoding="utf-8"))
    for k, v in prof.items():
        print(f"{k:22} {str(v['value']):28} {v['meaning']}")
