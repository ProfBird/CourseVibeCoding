# CLAUDE.md

Guidance for working in this repo. Read the README for setup/onboarding; this file is about how the code here is meant to be written and run.

## What this is

A collection of standalone Python scripts that read and modify a real Canvas LMS course through the Canvas REST API (via the `canvasapi` library). Each script is a single-purpose tool for a course-maintenance task — downloading a course, aligning learning outcomes, posting an announcement, splitting a module, updating due dates. There is no shared package or framework; scripts are run directly with `python <script>.py`.

The institution is Lane Community College (`https://canvas.lanecc.edu`). Work targets one course at a time, set by `COURSE_ID`.

## Non-negotiable safety conventions

These are followed by every script here and must be preserved in any new one:

- **Dry-run / preview by default.** Running a script with no flags must make *no* changes — and usually no write connection at all. Mutation happens only behind an explicit flag: `--live` (update_course_dates), `--apply` (align_learning_outcomes, split_grid_flexbox_modules), `--post` (post_announcement). Match the existing flag's spirit; don't invent a new default-on write path.
- **Confirm the course before acting.** Print the course name + ID (+ workflow_state where relevant) and require interactive confirmation before writes. `update_course_dates.py` additionally requires the user to *type the course ID* before a live run — use that pattern for anything destructive or bulk.
- **Never print, log, or write the Canvas token.** Read it once into the `Canvas()` client and never reference `CANVAS_TOKEN` again. `update_course_dates.py` has a `sanitize()` helper that scrubs the token from any error text before printing/logging — reuse that approach when surfacing exceptions.
- **Match items by Canvas ID, not by title.** This course contains duplicate titles (multiple "Lab 8 Submission", "Quiz 7", "Quiz/Exam Template" records). Title matching is ambiguous and unsafe for writes.
- **Don't do broad text replacement across course content.** Structured date/setting fields are fair game to script; body HTML (assignment/page/discussion text) is edited by hand. When written text needs changing, flag it for manual review rather than regex-replacing it.
- **`.env` holds real credentials and is gitignored.** Never commit it. `downloads/` is also gitignored (contains exported course data).

## Standard script shape

Every script starts the same way, and new ones should too:

```python
load_dotenv()
CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')
# validate all three are present; convert COURSE_ID to int with a clear error
canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)
course = canvas.get_course(int(COURSE_ID_STR))
```

Other shared conventions:
- `argparse` for flags; a `confirm(prompt)` helper where only `y`/`yes` counts as approval.
- Write an audit/log JSON for anything that changes Canvas (see `update_course_dates.py`'s proposed/completed/skipped/failed buckets, and `content/lo_alignment_log.json`).
- User-facing prints use emoji status markers (✅ ⚠️ ❌ 🔗 📚 📝). On Windows this can crash on a non-UTF-8 console — `update_course_dates.py` calls `sys.stdout.reconfigure(encoding="utf-8")` on win32; keep that guard in new scripts.

## Canvas API specifics that bite

- **New Quizzes are exposed as assignments**, not through `get_quizzes()`. In this course `get_quizzes()` returns 0 items; the quizzes live in `course.get_assignments()`. Set quiz dates via the assignment object.
- **A graded discussion/forum's due date lives on its linked *assignment* shell, not the `DiscussionTopic`.** e.g. "Week 1 Forum" is DiscussionTopic id 4072 but its due date is set on assignment id 8659.
- Update calls are nested per object type: `assignment.edit(assignment={...})`, `quiz.edit(quiz={...})`, `module.edit(module={...})`, `override.edit(assignment_override={...})`, but `discussion_topic.update(**flat_kwargs)` takes flat keyword args. Verify a method's shape in the installed `canvasapi` source before assuming it.
- Assignment availability uses `unlock_at` / `lock_at`; discussions use `delayed_post_at` / `lock_at`. A plain export via `download_course_json.py` does **not** include these fields — fetch them directly from the live object when you need them.
- All datetimes sent to Canvas are UTC ISO 8601 (`...Z`). The course runs in `America/Los_Angeles`; compute local wall-clock times with `zoneinfo` and convert to UTC (watch the PDT→PST shift on Nov 1 — offsets change mid-term).

## Layout

- `*.py` at root — one task each. `canvas_template.py` is the minimal connection example / starting point for new scripts.
- `content/` — Markdown/JSON inputs the scripts read (learning outcomes, announcement text, date mappings) and the JSON logs they write.
- `downloads/` — course exports from `download_course_json.py` (gitignored).
- Env: dev container is Python 3.11; a local `.venv` also exists. `pip install -r requirements.txt`.

## Testing changes

There is no automated test suite. The workflow is: run against a **sandbox course** first (point `COURSE_ID` at it), review the dry-run diff, then run live on the sandbox, verify in the Canvas UI, and only then repeat against the real published course. Always re-run the dry run after switching `COURSE_ID` — diffs are computed against whatever is currently live in that course.
