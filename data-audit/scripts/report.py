"""Render findings / verification results as a researcher-facing report (he or en)."""
from __future__ import annotations

CHECK = {
    "empty_rows": ("שורות ריקות", "Empty rows"),
    "duplicate_column_names": ("שם עמודה כפול", "Duplicate column names"),
    "unnamed_columns": ("עמודות בלי שם", "Unnamed columns"),
    "header_residue": ("שארית כותרת בתוך הנתונים", "Header residue in data"),
    "test_rows": ("שורות בדיקה", "Test rows"),
    "not_in_dictionary": ("עמודות שאינן במילון", "Columns not in dictionary"),
    "missing_from_data": ("משתני מילון שחסרים בקובץ", "Dictionary variables missing from file"),
    "empty_column": ("עמודה ריקה", "Empty column"),
    "constant_column": ("עמודה קבועה", "Constant column"),
    "missing_id": ("מזהה חסר", "Missing ID"),
    "junk_id": ("מזהה לא תקין", "Malformed ID"),
    "id_length_mismatch": ("מזהה באורך חריג", "ID of unusual length"),
    "duplicate_id_identical": ("מזהה כפול, תשובות זהות", "Duplicate ID, identical answers"),
    "duplicate_id_conflicting": ("מזהה כפול, תשובות שונות", "Duplicate ID, conflicting answers"),
    "duplicate_rows": ("שורות כפולות", "Duplicate rows"),
    "near_duplicates": ("משיבים כמעט זהים", "Near-duplicate respondents"),
    "text_answers": ("תשובות כטקסט (יש מיפוי)", "Text answers (map exists)"),
    "non_numeric": ("ערך לא מספרי", "Non-numeric value"),
    "missing_codes_present": ("קודי חסר כמספרים", "Missing codes stored as numbers"),
    "out_of_range": ("מחוץ לטווח", "Out of range"),
    "non_integer": ("ערך לא שלם", "Non-integer value"),
    "label_variant": ("וריאציית תווית", "Label variant"),
    "not_allowed": ("ערך לא מותר", "Value not allowed"),
    "bad_date": ("תאריך לא תקין", "Unparseable date"),
    "outside_window": ("מחוץ לחלון האיסוף", "Outside collection window"),
    "partial_response": ("מילוי חלקי", "Partial response"),
    "abandoned_block": ("שאלון שנזנח באמצע", "Abandoned block"),
    "skip_logic_violation": ("הפרת לוגיקת דילוג", "Skip-logic violation"),
    "required_missing": ("חסר בשדה חובה", "Required value missing"),
    "rule_violation": ("הפרת כלל", "Rule violation"),
    "straightlining": ("תשובה זהה לאורך כמה סולמות", "Straight-lining across scales"),
    "straightlining_single": ("תשובה זהה בסולם יחיד", "Straight-lining on one scale"),
    "routed_blocks": ("בלוקים בהפניה (או-או)", "Routed (either/or) blocks"),
    "item_missing": ("ערכים חסרים בודדים", "Isolated missing values"),
}
SEV = {"blocking": ("חוסם", "Blocking"), "decision": ("דורש החלטה", "Needs decision"), "info": ("לידיעה", "Info")}
ORDER = {"blocking": 0, "decision": 1, "info": 2}


def _l(pair, lang):
    return pair[0] if lang == "he" else pair[1]


def render_findings(res: dict, lang: str = "he") -> str:
    he = lang == "he"
    out = []
    out.append(("# דוח בדיקה: " if he else "# Audit report: ") + res["file"])
    out.append("")
    s = res["summary"]
    if he:
        out.append(f"נבדקו {res['rows']} שורות ({res['rows_live']} אחרי שורות ריקות ושורות בדיקה). "
                   f"עמודת המזהה: `{res['id_column'] or 'לא נמצאה'}`. "
                   f"נמצאו {s.get('blocking', 0)} ממצאים חוסמים, {s.get('decision', 0)} שדורשים החלטה ו-{s.get('info', 0)} לידיעה.")
    else:
        out.append(f"{res['rows']} rows scanned ({res['rows_live']} after empty and test rows). "
                   f"ID column: `{res['id_column'] or 'not found'}`. "
                   f"{s.get('blocking', 0)} blocking, {s.get('decision', 0)} needing a decision, {s.get('info', 0)} info.")
    out.append("")
    out.append("| # | שכבה | בדיקה | חומרה | משתנה | שורות | ראיה |" if he else
               "| # | Layer | Check | Severity | Variable | Rows | Evidence |")
    out.append("|---|---|---|---|---|---|---|")
    for f in sorted(res["findings"], key=lambda f: (ORDER[f["severity"]], f["layer"], f["id"])):
        rows = ", ".join(map(str, f["rows"][:12])) + ("…" if f["count"] > 12 else "")
        ev = str(f["evidence"]).replace("|", "/")
        extra = f.get("examples") or f.get("mapping")
        if extra:
            ev += f" — {extra}"
        out.append(f"| {f['id']} | {f['layer']} | {_l(CHECK.get(f['check'], (f['check'],) * 2), lang)} | "
                   f"{_l(SEV[f['severity']], lang)} | {f['var']} | {f['count']}: {rows} | {ev[:300]} |")
    out.append("")
    out.append("## " + ("אפשרויות לכל ממצא" if he else "Options per finding"))
    for f in sorted(res["findings"], key=lambda f: (ORDER[f["severity"]], f["id"])):
        if f["options"]:
            out.append(f"- **{f['id']}** {_l(CHECK.get(f['check'], (f['check'],) * 2), lang)}: " + " · ".join(f["options"]))
    out.append("")
    out.append(("מספרי השורות הם `_row`: מספר השורה בקובץ המקור, אחרי הכותרת. ההחלטות נרשמות ב-decisions_template.csv."
                if he else "Row numbers are `_row`: the data-row number in the source file. Record decisions in decisions_template.csv."))
    return "\n".join(out) + "\n"


def render_verify(res: dict, lang: str = "he") -> str:
    he = lang == "he"
    ok = res["passed"]
    out = [("# וידוא מעבר " if he else "# Verification ") + f"{res['from']} → {res['to']}: " +
           ("עבר" if ok and he else "נכשל" if he else "PASS" if ok else "FAIL"), ""]
    for layer in ("A", "B"):
        out.append(f"## {'שכבה' if he else 'Layer'} {layer}")
        for c in res["checks"]:
            if c["layer"] == layer:
                mark = "✔" if c["ok"] else "✘"
                out.append(f"- {mark} `{c['check']}` — {c['detail']}")
        out.append("")
    return "\n".join(out) + "\n"


def render_nflow(flow: list[dict], lang: str = "he") -> str:
    he = lang == "he"
    out = ["| גרסה | שלב | שורות | הוסרו | סיבה |" if he else "| Version | Step | Rows | Removed | Reason |",
           "|---|---|---|---|---|"]
    for f in flow:
        out.append(f"| {f['version']} | {f.get('step', '')} | {f['rows']} | {f.get('removed', '')} | {f.get('reason', '')} |")
    return "\n".join(out) + "\n"


CHECK.update({
    "reverse_suspected": ("פריט שמתאם שלילית עם הסולם (הפוך?)", "Item correlates negatively (reverse-keyed?)"),
    "reverse_already_applied": ("פריט מסומן הפוך אך נראה כבר הפוך בקובץ", "Marked reverse but already reversed in the file?"),
    "reverse_not_supported": ("היפוך שהנתונים לא תומכים בו", "Reversal not supported by the data"),
    "mixed_item_ranges": ("פריטים בטווחים שונים באותו סולם", "Items on different ranges"),
    "low_reliability": ("מהימנות נמוכה", "Low reliability"),
    "weak_item": ("פריט חלש", "Weak item"),
    "reverse_hypothesis": ("השערת היפוך מנוסח הפריט, מול הנתונים", "Reverse keying suggested by wording, tested on data"),
    "ranking_items": ("פריטי דירוג (איפסטיביים)", "Ranking (ipsative) items"),
    "careless_multi": ("מענה לא קשוב (כמה מדדים)", "Careless responding (several indices)"),
    "careless_single": ("מענה לא קשוב (מדד אחד)", "Careless responding (one index)"),
    "speeding": ("מענה מהיר מדי", "Speeding"),
    "stable_field_changed": ("שדה יציב שהשתנה בין גלים", "Stable field changed between waves"),
    "extreme_change": ("שינוי קיצוני בין גלים", "Extreme change between waves"),
    "unmatched_ids": ("מזהים שלא הותאמו בין גלים", "IDs not matched between waves"),
})


def render_measure(res: dict, lang: str = "he") -> str:
    he = lang == "he"
    out = [("# מדידה, איכות מענה ועקביות בין גלים: " if he else "# Measurement, response quality and waves: ") + res["file"], ""]
    rel = res.get("reliability", {})
    if rel:
        out.append("## " + ("מהימנות לפי סולם" if he else "Reliability by scale"))
        out.append("| סולם | פריטים | n שלם | α (כפי שהנתונים) | α (לפי המילון) | רווח סמך 95% | פריטים מסומנים הפוכים |" if he else
                   "| Scale | Items | Complete n | α (as data stand) | α (as scored) | 95% CI | Marked reverse |")
        out.append("|---|---|---|---|---|---|---|")
        for sc, r in rel.items():
            out.append(f"| {sc} | {r['items']} | {r['n_complete']} | {r.get('alpha_raw', '')} | {r.get('alpha_scored', '')} | "
                       f"{r.get('alpha_ci', '')} | {', '.join(r.get('reverse_marked', []))} |")
        out.append("")
    care = res.get("careless", {})
    if care:
        out.append("## " + ("מענה לא קשוב" if he else "Careless responding"))
        out.append((f"נבדקו {care.get('rows_assessed')} משיבים. מספר המשיבים שסומנו בכל מדד: {care.get('indices')}. "
                    "משיב מסומן כ'לא קשוב' רק כששניים או יותר מהמדדים מסכימים." if he else
                    f"{care.get('rows_assessed')} respondents assessed. Flagged per index: {care.get('indices')}. "
                    "A respondent is flagged only when two or more indices agree."))
        out.append("")
    lon = res.get("longitudinal", {})
    if lon:
        out.append("## " + ("שני גלים" if he else "Two waves"))
        out.append((f"מזהים ייחודיים: {lon.get('ids_t1')} בגל א, {lon.get('ids_t2')} בגל ב; הותאמו {lon.get('matched')}. "
                    f"יציבות שדות: {lon.get('agreement_by_field', {})}. מתאם מבחן-מבחן חוזר: {lon.get('test_retest', {})}."
                    if he else
                    f"Unique IDs: {lon.get('ids_t1')} in wave 1, {lon.get('ids_t2')} in wave 2; {lon.get('matched')} matched. "
                    f"Field agreement: {lon.get('agreement_by_field', {})}. Test-retest r: {lon.get('test_retest', {})}."))
        out.append("")
    out.append("## " + ("ממצאים" if he else "Findings"))
    out.append("| # | שכבה | בדיקה | חומרה | משתנה | שורות | ראיה |" if he else "| # | Layer | Check | Severity | Variable | Rows | Evidence |")
    out.append("|---|---|---|---|---|---|---|")
    for f in sorted(res["findings"], key=lambda f: (ORDER[f["severity"]], f["layer"], f["id"])):
        rows = ", ".join(map(str, f["rows"][:12])) + ("…" if f["count"] > 12 else "")
        out.append(f"| {f['id']} | {f['layer']} | {_l(CHECK.get(f['check'], (f['check'],) * 2), lang)} | "
                   f"{_l(SEV[f['severity']], lang)} | {f['var']} | {f['count']}: {rows} | {str(f['evidence']).replace('|', '/')[:300]} |")
    out.append("")
    out.append("## " + ("אפשרויות" if he else "Options"))
    for f in res["findings"]:
        if f["options"]:
            out.append(f"- **{f['id']}**: " + " · ".join(f["options"]))
    return "\n".join(out) + "\n"
