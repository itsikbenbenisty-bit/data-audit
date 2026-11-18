#!/usr/bin/env python3
"""Build the two release files into dist/:
  data-audit-skill.zip              - the skill, ready to upload to Claude (tests excluded)
  data-audit_chatgpt_knowledge.zip  - the scripts + one combined references file, for a Custom GPT
usage: python tools/build_release.py"""
import glob, os, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SK = os.path.join(ROOT, "data-audit"); DIST = os.path.join(ROOT, "dist"); os.makedirs(DIST, exist_ok=True)
skip = lambda p: "__pycache__" in p or os.sep + "tests" + os.sep in p + os.sep or p.endswith(".pyc")
with zipfile.ZipFile(os.path.join(DIST, "data-audit-skill.zip"), "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(SK):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            if not skip(p): z.write(p, os.path.relpath(p, ROOT))
with zipfile.ZipFile(os.path.join(DIST, "data-audit_chatgpt_knowledge.zip"), "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(glob.glob(os.path.join(SK, "scripts", "*.py"))): z.write(f, os.path.basename(f))
    z.writestr("references_all.md", "\n\n".join(f"<!-- {os.path.basename(f)} -->\n" + open(f, encoding="utf-8").read()
                                                for f in sorted(glob.glob(os.path.join(SK, "references", "*.md")))))
for f in sorted(os.listdir(DIST)): print(os.path.join("dist", f), len(zipfile.ZipFile(os.path.join(DIST, f)).namelist()), "files")
