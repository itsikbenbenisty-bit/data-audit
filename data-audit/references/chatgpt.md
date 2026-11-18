# Running data-audit in ChatGPT — handover notes

ChatGPT has no skills folder, so the method travels as a **Custom GPT** (or a Project) whose
instructions are SKILL.md and whose knowledge files are the references and the scripts. Its code tool
(Advanced Data Analysis / Python) runs the same scripts on uploaded files.

## Setting it up (once)

1. Create a GPT (Explore GPTs → Create) or a Project.
2. **Instructions:** paste `chatgpt/GPT_instructions.md` (about 3,800 characters; the limit is 8,000).
3. **Knowledge:** unzip `data-audit_chatgpt_knowledge.zip` (from the release, or built by `python tools/build_release.py`) and upload the files inside it
   (the scripts + one combined references file, under ChatGPT's limit of 20 files).
4. **Capabilities:** turn on Code Interpreter & Data Analysis. Web browsing is not needed.
5. First message of every session: "Copy the scripts from knowledge to /mnt/data and import them."
   (Knowledge files are readable by the code tool, but copying makes the imports reliable.)

## Differences that matter

| | Claude (this skill) | ChatGPT |
|---|---|---|
| where data are processed | on the researcher's machine (device shell) or a private workspace | uploaded to OpenAI's sandbox: **not for identifying data** unless the organisation's agreement allows it |
| packages | pandas, openpyxl, pyreadstat, scipy | pandas, openpyxl, scipy usually present; `pyreadstat` often missing → read `.sav` by exporting to csv/xlsx from SPSS first |
| files between sessions | persist in the work folder | the sandbox resets: download `work/` (versions, manifest, log) at the end and upload it next time |
| questions to the researcher | interactive choices | ask in text, a few findings at a time, numbered options |
| verification | runs automatically in `save_version` | the same: `save_version` raises on failure — tell the model never to catch that error |

## What to tell the user

- Remove identifiers before uploading, or use a synthetic copy, unless the institution approves.
- Download the `work/` folder and the reports before closing; the sandbox will not keep them.
- The method is the same; the guarantee is in the log and the verification, not in the model.
