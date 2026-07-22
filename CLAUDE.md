# CLAUDE.md

Guidance for working in this repo. Read the README for setup/onboarding; this file is about how the code here is meant to be written and run.

## What this is

A collection of standalone scripts that read and modify a real Canvas LMS course through the Canvas REST API. Each script is a single-purpose tool for a course-maintenance task — downloading a course, downloading submissions, aligning learning outcomes, posting an announcement, splitting a module, updating due dates. There is no shared package or framework; scripts are run directly.

The code is organized by language:
- **`Python/`** — the primary and most complete toolkit, built on the `canvasapi` library. Run with `python Python/<script>.py`.
- **`Node/`** — Node.js ports of individual Python scripts, written zero-dependency against Node's built-in global `fetch` (no `canvasapi` equivalent). Run with `node Node/<script>.js`.

When a script exists in both languages, they must stay behavior-equivalent — the Node version is a port, not a redesign. Both languages obey the same safety conventions below.

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

Every Python script starts the same way, and new ones should too:

```python
load_dotenv()
CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')
# validate all three are present; convert COURSE_ID to int with a clear error
canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)
course = canvas.get_course(int(COURSE_ID_STR))
```

Other shared conventions (both languages):
- Flags via `argparse` (Python) / a small hand-rolled parser (Node); a `confirm(prompt)` helper where only `y`/`yes` counts as approval.
- Write an audit/log JSON for anything that changes Canvas. **Disposable per-run records go to `downloads/`** (gitignored) — e.g. `update_course_dates.py`'s proposed/completed/skipped/failed buckets, `grade_assignment_poc.py`'s grade log — so re-running a script doesn't add untracked noise. Every script that writes there must create the directory itself (`DOWNLOAD_ROOT.mkdir(exist_ok=True)` or equivalent) since, unlike `content/`, it's gitignored and won't exist on a fresh clone. The one exception is `align_learning_outcomes.py`'s `Python/content/lo_alignment_log.json`, which is a small cumulative checklist worth keeping in version control, not a per-run dump — don't use it as the template for a new script's logging.
- User-facing prints use emoji status markers (✅ ⚠️ ❌ 🔗 📚 📝). On Windows this can crash on a non-UTF-8 console — Python scripts call `sys.stdout.reconfigure(encoding="utf-8")` on win32; keep that guard in new ones. (Node prints UTF-8 by default.)
- `sanitize()` (token → `***REDACTED***`) exists in both languages; route every printed/logged exception through it.

**Node specifics:** the ports have no third-party dependencies — Node's global `fetch` replaces `canvasapi`, so `canvasapi`'s automatic pagination must be done by hand (follow the `Link` header's `rel="next"`), and `.env` is read by a small built-in parser rather than a library. That parser must strip inline comments from unquoted values (this repo's `.env` uses `COURSE_ID=680 # sandbox`) and must not override vars already in the real environment, matching `python-dotenv`.

## Canvas API specifics that bite

- **New Quizzes are exposed as assignments**, not through `get_quizzes()`. In this course `get_quizzes()` returns 0 items; the quizzes live in `course.get_assignments()`. Set quiz dates via the assignment object.
- **A graded discussion/forum's due date lives on its linked *assignment* shell, not the `DiscussionTopic`.** e.g. "Week 1 Forum" is DiscussionTopic id 4072 but its due date is set on assignment id 8659.
- Update calls are nested per object type: `assignment.edit(assignment={...})`, `quiz.edit(quiz={...})`, `module.edit(module={...})`, `override.edit(assignment_override={...})`, but `discussion_topic.update(**flat_kwargs)` takes flat keyword args. Verify a method's shape in the installed `canvasapi` source before assuming it.
- Assignment availability uses `unlock_at` / `lock_at`; discussions use `delayed_post_at` / `lock_at`. A plain export via `download_course_json.py` does **not** include these fields — fetch them directly from the live object when you need them.
- All datetimes sent to Canvas are UTC ISO 8601 (`...Z`). The course runs in `America/Los_Angeles`; compute local wall-clock times with `zoneinfo` and convert to UTC (watch the PDT→PST shift on Nov 1 — offsets change mid-term).

## Layout

Each language folder is self-contained — code, inputs, and outputs all live under it:

- `Python/` — the Python scripts (one task each) plus `requirements.txt`.
  - `Python/canvas_template.py` — minimal connection example / starting point for new Python scripts.
  - `Python/content/` — tracked Markdown/JSON inputs the scripts read (learning outcomes, announcement text, date mappings), plus `lo_alignment_log.json` (see below).
  - `Python/downloads/` — everything disposable: course exports, downloaded submissions (incl. FERPA-protected student work), and per-run audit logs like `update_course_dates.py`'s (gitignored).
- `Node/` — Node.js ports plus `package.json`.
  - `Node/download_assignment_submissions.js` — the reference for these conventions in JavaScript.
  - `Node/downloads/` — downloaded submissions from the Node scripts (gitignored).
- `.env` at the **repo root** — the one thing shared across languages. Both Python (`load_dotenv()`) and the Node scripts' hand-rolled loader search upward from the script to find it, so one credential file serves both.
- Env: dev container is Python 3.11 (`pip install -r Python/requirements.txt`) and Node 18+; a local `.venv` also exists (under `Python/`).

**Why per-language, not shared:** the scripts resolve their data dirs as `Path(__file__).parent / "content"` (Python) / `path.join(__dirname, "downloads")` (Node) — i.e. always relative to the script's own folder, never the repo root or CWD. Keep new scripts consistent with that: don't hardcode a root-relative `content/` or `downloads/` path, and don't assume the two languages share a data directory.

## Testing changes

There is no automated test suite. The workflow is: run against a **sandbox course** first (point `COURSE_ID` at it), review the dry-run diff, then run live on the sandbox, verify in the Canvas UI, and only then repeat against the real published course. Always re-run the dry run after switching `COURSE_ID` — diffs are computed against whatever is currently live in that course.
