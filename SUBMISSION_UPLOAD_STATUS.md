# Test-Student Submission Upload — Status & Resume Plan

**Goal:** Upload real, anonymized lab work onto the 10 throwaway *Test Student*
accounts in the sandbox course, submitting on each student's behalf, so a
grading pipeline can be exercised end to end.

**Status (2026-07-23):** Both scripts built and verified up to a Canvas
**permission wall**. A single live smoke-test submission was attempted and
failed at the submission-creation step. **Blocked pending admin action**
(permissions and/or fake-student passwords have been requested).

---

## Course & scope (frozen)

- Course: **Credit Course Sandbox 1 (Bird DEV)** — id **680** (sandbox).
- Test Students: 10 accounts, user_ids **311–320** ("Test Student One"…"Ten").
- Source work: `/Volumes/DataCard/Projects/GradingAutomation/LabSubmissions/`
  (dirs `CIS195_Lab1Submissions` … `Lab9`, `CIS195_TermProjects`).
- Approach: **zip each student folder** (contents at archive root; preserves
  the mini-website structure + `images/` + relative links) and submit the zip.
  All target assignments accept `online_upload` with no extension restriction,
  so no assignment edits are needed.
- Pairing: students ordered by user_id, source folders sorted alphabetically,
  `folder[i] -> student[i]`. Deterministic (a single-target run picks the same
  folder a full run would). 100 submissions total (10 dirs × 10 students).

### Frozen source-dir → assignment mapping (by Canvas ID)

| Source dir | Assignment | ID |
|---|---|---|
| CIS195_Lab1Submissions | Lab 1 Submission | 22143 |
| CIS195_Lab2Submissions | Lab 2 Submission | 22146 |
| CIS195_Lab3Submissions | Lab 3 Submission | 22147 |
| CIS195_Lab4Submissions | Lab 4 Submission | 22148 |
| CIS195_Lab5Submissions | Lab 5 Submission | 22149 |
| CIS195_Lab6Submissions | Lab 6 Submission | 22150 |
| CIS195_Lab7Submissions | Lab 7 Submission | 22151 |
| CIS195_Lab8Submissions | Lab 8 Submission (the **published** one; other two are unpublished dupes) | 22154 |
| CIS195_Lab9Submissions | Lab 9 Submission | 22155 |
| CIS195_TermProjects | Term Project | 22174 |

Full machine-readable plan: `content/upload_plan_680.json`.

---

## The blocker (tested against course 680, this CANVAS_TOKEN)

The Canvas "submit on behalf" flow is two steps; only step 2 is blocked:

| Action | Result |
|---|---|
| Grade a student's submission | ✅ works (grade POC landed 10/75/99) |
| Upload a file into a student's submission folder (`upload_to_submission`) | ✅ works |
| **Create the submission on their behalf** (`POST .../submissions` w/ `submission[user_id]`) | ❌ `user not authorized to perform that action` |
| Masquerade / act-as (`as_user_id`) | ❌ denied ("Invalid as_user_id") |

Grading is **not** the gate (it works); submission-creation specifically is.

### The exact permission needed (confirmed 2026-07-23)

The gate is the granular Canvas permission **"Submit on behalf of student"**
(internal id `proxy_assignment_submission`). This is the SAME capability behind
the gradebook **"Submit for Student"** button (Grade Detail Tray) — the UI button
and this script's `submission[user_id]` API call are two front-ends to it. So:

- It **is** API-accessible — granting the permission makes THIS SCRIPT work as-is.
  Playwright/browser automation is **not** required.
- Only works for **file-upload** assignments (our zip approach — fine).
- An instructor's submission **counts as one of the student's attempts** (fine
  for throwaway test students).
- Ask the admin for the permission by that exact name / id.
  Ref: developerdocs.instructure.com → permissions →
  `file.permissions_proxy_assignment_submission`.

---

## Resume steps — pick the branch that matches what the admin grants

### Branch A — "Submit on behalf of students" API permission granted
Run the scripts **as-is**. No code change.
```bash
# 1. smoke test: one folder -> one student -> one assignment
.venv/bin/python upload_student_submissions.py --assignment-id 22143 --user-id 311 --apply
# 2. verify it shows as submitted in SpeedGrader, then the full run:
.venv/bin/python upload_student_submissions.py --apply
```

### Branch B — "Users – act as" (masquerade) permission granted
Same, but add `--masquerade` (already implemented; sends `as_user_id` and drops
`user_id`):
```bash
.venv/bin/python upload_student_submissions.py --assignment-id 22143 --user-id 311 --apply --masquerade
.venv/bin/python upload_student_submissions.py --apply --masquerade
```
Note: Canvas may restrict submitting while acting-as; if step 2 still fails
under masquerade, fall back to Branch C.

### Branch C — Browser fallback (last resort, NOT yet built)
Only if the API somehow still refuses after `proxy_assignment_submission` is
granted (unlikely — same capability). Preferred browser form: a Playwright
script that drives the **"Submit for Student"** button in the Grade Detail Tray
**while logged in as the teacher/admin — NO student passwords needed**. (The
older idea of logging in as each student is inferior and only relevant if the
"Submit for Student" button itself isn't available.) Reuse the zip-building and
mapping logic from `upload_student_submissions.py`; the human owns the browser
login (Claude does not enter passwords).

---

## Housekeeping / notes

- An inert probe file `_probe.txt` (Canvas file id **85470**) was left in **Test
  Student One's** sandbox files during testing. Not a submission; harmless;
  delete in the Canvas UI if desired.
- Re-run `inspect_submission_targets.py` after any change to students/assignments
  to regenerate `content/upload_plan_680.json` (then re-pin Lab 8 → 22154, which
  is a true duplicate title the auto-mapper leaves ambiguous).
- Files: `inspect_submission_targets.py` (read-only discovery),
  `upload_student_submissions.py` (uploader, dry-run by default).
