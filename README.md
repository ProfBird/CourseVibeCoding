# Canvas Course Maintenance Toolkit

A collection of standalone scripts for reading and safely modifying a Canvas LMS course through the Canvas REST API. It began as a minimal Python connection template (`canvas_template.py`) and grew into a set of single-purpose course-maintenance tools used to prepare and re-term a Lane Community College course. The tools live in language folders:

- **`Python/`** — the primary toolkit, built on the [`canvasapi`](https://github.com/ucfopen/canvasapi) library. Run with `python Python/<script>.py`.
- **`Node/`** — Node.js ports (starting with the submission downloader), zero-dependency using the built-in `fetch`. Run with `node Node/<script>.js`.

Each script does one job and is run directly. There is no shared framework. Every script that changes Canvas follows the same safety model — see [Safety model](#-safety-model) below — regardless of language.

The Python scripts are all built on the connection/credential pattern in `canvas_template.py`, which comes from the [ProfBird/canvas-api-template](https://github.com/ProfBird/canvas-api-template) starter template.

> **New here?** Do the [setup](#prerequisites) once, run `python Python/canvas_template.py` to confirm your connection works, then read [The scripts](#-the-scripts).

---

# 🧰 The scripts

### Python (`Python/`)

| Script | What it does | Write trigger |
|---|---|---|
| `canvas_template.py` | Minimal connection test / starting point for new scripts. Lists the course's modules. | read-only |
| `download_course_json.py` | Exports the whole course (info, modules, pages, assignments, discussions, quizzes, file metadata) to `downloads/course_<id>_<timestamp>.json`. | read-only |
| `download_assignment_submissions.py` | Downloads the submitted work (files / text / URL) for one assignment, by ID, into `downloads/assignment_<id>/` with a manifest. | read-only |
| `align_learning_outcomes.py` | Creates Canvas Learning Outcomes from a Markdown file and interactively aligns them to assignments. | `--apply` |
| `post_announcement.py` | Posts the announcement in `content/welcome_announcement.md` (published, or held as a hidden draft). | `--post` |
| `split_grid_flexbox_modules.py` | Splits a combined "CSS Grid and Flexbox" module into two new unpublished modules, duplicating assignments/quizzes via Canvas's own duplicate endpoint. | `--apply` |
| `update_course_dates.py` | Applies an approved due/unlock/lock date mapping (`content/date_mapping.json`) to specific items by ID. Shows old→new diffs, writes an audit log. | `--live` |
| `grade_assignment_poc.py` | Probes whether the current token can submit one grade for one submission via the API. | `--apply` |

### Node.js (`Node/`)

| Script | What it does | Write trigger |
|---|---|---|
| `download_assignment_submissions.js` | Zero-dependency port of the Python script of the same name — downloads one assignment's submitted work into `Node/downloads/assignment_<id>/` with a manifest. | read-only |

---

# 🛡️ Safety model

Every script that can change Canvas obeys the same rules. Preserve these when adding new scripts (they're spelled out in `CLAUDE.md`):

- **Dry-run by default.** No flags = no changes. Writes happen only behind an explicit flag (`--live` / `--apply` / `--post`).
- **Confirm the course first.** Scripts print the course name + ID and require confirmation before writing; bulk/destructive ones make you *type the course ID*.
- **The token is never printed, logged, or written to a file.**
- **Items are matched by Canvas ID, never by title** (this course has duplicate titles).
- **Run against a sandbox course first**, review the diff, then repeat against the real course.

---

# 🚀 Quick Start

1. Open the project in **Codespaces** (or a local dev container / venv)

2. Add your Canvas credentials

Go to:

Settings → Secrets and variables → Codespaces

Create these secrets:

CANVAS_URL  
CANVAS_TOKEN  
COURSE_ID  

3. Confirm your connection:

```bash
python Python/canvas_template.py
```

---

# 📁 Project structure

```
Python/             Python scripts + requirements.txt (the primary toolkit)
  content/           Markdown/JSON inputs the Python scripts read (tracked), plus one small alignment log
  downloads/         Course exports, downloaded submissions, and per-run audit logs (all gitignored)
Node/                Node.js ports + package.json (zero-dependency, uses built-in fetch)
  downloads/         Downloaded submissions from the Node scripts (gitignored)
.env                 Canvas credentials (gitignored) — shared by both languages
```

Each language folder is self-contained: its scripts resolve `content/`/`downloads/` **relative to their own folder** (`Path(__file__).parent` in Python, `path.join(__dirname, ...)` in Node). `content/` is for things worth versioning — inputs like `date_mapping.json`, and `align_learning_outcomes.py`'s small `lo_alignment_log.json`. Everything else a script writes as a disposable record of one run (course/submission exports, `update_course_dates.py`'s audit logs) goes to `downloads/`, which is gitignored — so re-running a script never adds noise to `git status`. The one thing shared across both languages is `.env`, at the repo root; both find it by searching upward from the script.

---

# Prerequisites

- Canvas instance with API access enabled  
- API token from your Canvas account  
- Python 3.11+ (for `Python/`) and/or Node.js 18+ (for `Node/`)  

---

# Full Setup with Detailed Instructions

## 1. Open in Dev Container

### Option A: GitHub Codespaces (Easiest - No Local Setup Required)

In your repository on GitHub:

1. Click the green **Code** button  
2. Select the **Codespaces** tab  
3. Click **Create codespace on main**  

Wait for the container to build automatically (2–3 minutes).

The environment is ready — all dependencies are pre-installed.

---

### Option B: VS Code Desktop

Open this folder in VS Code on your local machine.

When prompted, click **Reopen in Container**

(or press **Ctrl + Shift + P** and search  
`Dev Containers: Reopen in Container`)

Wait for the container to build (dependencies will install automatically).

Codespaces is recommended — you don't need anything installed locally, just a web browser.

---

# 2. Configure Environment

### Option A: Codespaces Secrets (Recommended for Codespaces)

If using GitHub Codespaces, use encrypted secrets for secure credential storage.

Go to your GitHub repo (not inside Codespaces):

Settings → Secrets and variables → Codespaces

Create new secrets:

CANVAS_URL  
CANVAS_TOKEN  
COURSE_ID  

The `.env` will read from these automatically (no file needed).

This keeps your credentials secure and encrypted by GitHub.

---

### Option B: Local `.env` file (Quick Start)

```bash
cp .env.example .env
```

Edit `.env` with your Canvas credentials:

```
CANVAS_URL=https://your-institution.instructure.com
CANVAS_TOKEN=your_api_token_here
COURSE_ID=123456
```

---

# 3. Run the Script

```bash
python Python/download_course_json.py
```

---

# Expected Output

When you run the script successfully, you should see:

```
🔗 Connecting to Canvas: https://your-institution.instructure.com
📚 Fetching course 123456...

✅ Successfully connected!
   Course Name: Introduction to Python
   Course ID: 123456

📋 Course Content:
   Number of modules: 4
   Module Names:
     - Week 1: Getting Started
     - Week 2: Variables and Types
     - Week 3: Functions
     - Week 4: Projects

🎉 Success! Your Canvas API connection is working.
   Next steps: Uncomment examples above or explore the Canvas API docs
```

If you see an error instead, check **TROUBLESHOOTING.md** for solutions.

---

# Running Locally (Optional)

If you prefer running the project locally instead of Codespaces:

```bash
git clone <your-repo-url>
cd canvas-api-template
```

Then open the folder in **VS Code** and select:

```
Dev Containers: Reopen in Container
```

Follow the environment configuration steps above.

---

# Finding Your Course ID

You need your Canvas course ID to get started.

## Method 1: From the URL (Easiest)

Go to your Canvas course.

Look at the URL in your browser.

Find the number after `/courses/`

```
https://your-institution.instructure.com/courses/123456/modules
                                                   ^^^^^^
                                               Course ID
```

---

## Method 2: From Course Settings

In Canvas:

1. Click **Settings** (bottom left of course menu)  
2. Look for **Course ID** displayed on the page  
3. Copy the number (digits only)

---

## Method 3: From Canvas Admin

If you don't have direct access to the course:

- Ask your Canvas instructor or administrator  
- Provide the course name and they can give you the ID  

---

# Getting Your Canvas API Token

1. Log in to Canvas  
2. Click your profile picture → **Settings**  
3. Scroll to **Approved Integrations**  
4. Click **New Access Token**  
5. Copy the generated token (you will only see it once)

---

# Writing a new script

For Python, start from `Python/canvas_template.py` — it has the standard credential-loading and connection block every Python script here uses. For a new Node port, `Node/download_assignment_submissions.js` is the reference for the same conventions in JavaScript. Either way, follow the [Safety model](#-safety-model) and the conventions documented in `CLAUDE.md` (dry-run default, an explicit write flag, a per-run audit log in `downloads/`, never logging the token). New Quizzes in this course are exposed through the assignments endpoint, not the quizzes one; a graded discussion's due date lives on its linked assignment, not the discussion topic — `CLAUDE.md` lists these API gotchas.

---

# Canvas API Documentation

- [Canvas LMS REST API](https://canvas.instructure.com/doc/api/)  
- [Canvas LMS REST API — Instructure Developer Docs](https://developerdocs.instructure.com/services/canvas) — the official, currently-maintained reference for every REST endpoint.
- [`canvasapi` Python library](https://canvasapi.readthedocs.io/) — documentation for the wrapper this project's Python scripts use.
- [`canvasapi` class reference](https://canvasapi.readthedocs.io/en/stable/class-reference.html) — every class's own page; the fastest way to see exactly which REST call a `canvasapi` method makes.
- [`canvasapi` GitHub repository](https://github.com/ucfopen/canvasapi) (UCF Open) — the wrapper's source: classes, methods, arguments, and feature support.
- [Canvas LMS GitHub repository](https://github.com/instructure/canvas-lms/) — Instructure's open-source Canvas itself, for self-hosting on your own VPS/VM.

---

# Troubleshooting

Having issues?

Check **TROUBLESHOOTING.md** for solutions to common problems:

- Configuration errors (missing credentials, invalid Course ID)  
- Connection issues (can't reach Canvas, invalid token)  
- Codespaces-specific problems  
- Debugging steps when nothing else works  

---

# Common Operations

### Update Course Name

```python
course.update(course={'name': 'New Name'})
```

### Get Assignments

```python
assignments = course.get_assignments()
```

### Create an Assignment

```python
course.create_assignment({'name': 'New Assignment'})
```

### Get Students

```python
students = course.get_users(enrollment_type=['student'])
```

---

# Security

Your Canvas API token is a secret key that grants full access to your Canvas account. Treat it like a password.

---

# ✅ Best Practices

## 1. Secure Storage

### For Codespaces Users (Recommended)

Use Codespaces encrypted secrets to store credentials.

GitHub encrypts secrets server-side with AES-256  
Secrets are only decrypted when your codespace runs  
They never appear in logs, code, or git history  

This is the most secure option.

---

### For Local Development

Keep `.env` out of git — the `.gitignore` file ensures `.env` is never accidentally committed.

Before committing:

```
git status
```

Verify `.env` does not appear.

Use `.env` locally only. Never copy credentials into code files.

---

## 2. Never Share Your Token

🚫 Never paste your token in:

- GitHub issues or pull requests  
- Chat applications (Slack, Discord, Teams, etc.)  
- Email or forums  
- Code comments or documentation  
- Stack Overflow or public debugging  

If you accidentally expose a token:

1. Delete it in Canvas **Settings → Approved Integrations**  
2. Create a new token  
3. Update your configuration  

---

## 3. Set Token Expiration

When creating your Canvas API token, set an expiration date.

Go to:

```
Canvas Settings → Approved Integrations
```

Shorter tokens (1–3 months) are more secure.

Recommendation: rotate tokens every 3 months.

---

## 4. What To Do If Compromised

If you suspect your token was exposed:

1. Delete the token immediately  
2. Check Canvas activity logs for suspicious access  
3. Create a new token  
4. Update all machines and environments  

---

## 5. Environment Variable Safety

This template uses environment variables instead of hardcoded credentials.

⚠️ Never debug with credentials.

Bad:

```python
print(f"Token: {CANVAS_TOKEN}")
print(os.getenv('CANVAS_TOKEN'))
```

Good:

```python
print(f"Canvas URL: {CANVAS_URL}")
print("Successfully connected to Canvas")
```

---

## 6. Monitor Canvas Audit Logs

Canvas keeps an audit log of all API access.

```
Canvas Admin → Logs → API Access Logs
```

If suspicious activity appears, delete the token immediately.

---

## 7. Forking This Repository

If you fork this template:

- Verify this repo never had real credentials committed  
- Generate your own API tokens  
- Start with `.env.example`  

Each environment should use separate tokens.

---

# ⚠️ Why This Matters

If someone gains your `CANVAS_TOKEN`, they could:

- Modify grades and assignments  
- Change course content  
- Add or remove students  
- Delete course materials  
- Access sensitive student data  
- Post messages as you  

---

# 🔍 Verification Checklist

Before pushing to GitHub:

```
# Verify .env is NOT in git
git status

# Verify .gitignore includes .env
cat .gitignore | grep env

# Search for hardcoded tokens
git log --all -p | grep -i "token\|canvas"

# Check recent commits
git log --name-only -n 5 | grep env
```
