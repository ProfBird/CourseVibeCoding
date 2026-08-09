"""
Canvas Submission-Upload Discovery (read-only) - Gather everything the
companion upload script (upload_student_submissions.py) needs, and propose a
mapping, WITHOUT touching Canvas or the source files.

The goal is to upload real, anonymized lab work onto a handful of throwaway
"Test Student" accounts so grading automation can be exercised end to end.
Before any of that can happen safely we need to know, by Canvas ID:

  - which enrolled users are the Test Students (matched by name prefix), and
    their user_ids - the upload script addresses submissions by user_id, never
    by name (this course has duplicate titles / names; see CLAUDE.md).
  - the course's real assignments (not quizzes/forums, which get_assignments()
    also returns), with the two fields that gate uploading:
      * submission_types   - must include 'online_upload'
      * allowed_extensions - if non-empty and excludes 'zip', a per-folder zip
        upload is impossible and files must go up individually.
  - what's actually in the source submission tree on disk, and a PROPOSED
    dir -> assignment mapping built by matching the number in each source dir
    name (Lab1..Lab9) against the number token in an assignment name, plus a
    'term project' bucket. This mapping is a starting point for you to review,
    not an authority - assignment names may not line up cleanly.

Output: content/upload_plan_<course_id>.json  (reviewable; consumed by the
upload script) and a printed summary. Read-only: no --apply flag, no writes
to Canvas, nothing copied or zipped. Re-run any time.

Usage:
    python inspect_submission_targets.py
        # uses COURSE_ID from .env and the default source root below

    python inspect_submission_targets.py --source-root /path/to/LabSubmissions
    python inspect_submission_targets.py --student-prefix "Test Student"
    python inspect_submission_targets.py --sample 10
        # how many source student folders to preview per dir (default 10)
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from canvasapi import Canvas
from canvasapi.exceptions import CanvasException, ResourceDoesNotExist

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')

CONTENT_DIR = Path(__file__).parent / "content"
DEFAULT_SOURCE_ROOT = "/Volumes/DataCard/Projects/GradingAutomation/LabSubmissions"
DEFAULT_STUDENT_PREFIX = "Test Student"


def sanitize(text):
    """Strip the Canvas token out of any string before it's printed or logged."""
    text = str(text)
    if CANVAS_TOKEN:
        text = text.replace(CANVAS_TOKEN, "***REDACTED***")
    return text


def is_quiz_or_forum(assignment):
    """True if this assignment shell is actually a quiz (classic or New
    Quizzes) or a graded discussion/forum. Both are returned by
    get_assignments() alongside plain assignments (see CLAUDE.md). Lifted from
    download_assignment_submissions.py so both scripts agree on what counts as
    a real, uploadable assignment."""
    submission_types = getattr(assignment, "submission_types", None) or []
    if "discussion_topic" in submission_types:
        return True
    if getattr(assignment, "quiz_id", None) is not None:
        return True
    if getattr(assignment, "is_quiz_assignment", False):
        return True
    if "external_tool" in submission_types:
        tool = getattr(assignment, "external_tool_tag_attributes", None) or {}
        if "quiz-lti" in (tool.get("url") or ""):
            return True
    return False


def classify_source_dir(dir_name):
    """Given a source directory name, return (kind, number) where kind is
    'lab', 'term_project', or 'unknown'. The number is the lab's sequential
    number (int) for labs, else None."""
    lower = dir_name.lower()
    if "term" in lower or "project" in lower:
        return "term_project", None
    # First run of digits in the dir name - "CIS195_Lab1Submissions" -> 1.
    m = re.search(r"lab\s*0*(\d+)", lower) or re.search(r"0*(\d+)", lower)
    if m:
        return "lab", int(m.group(1))
    return "unknown", None


def name_has_number_token(name, number):
    """True if `name` contains `number` as a standalone token, so that
    number 1 matches 'Lab 1' / 'Assignment 1:' but NOT 'Lab 10' or '2021'."""
    return re.search(rf"(?<!\d){number}(?!\d)", name or "") is not None


def propose_assignment_for_dir(kind, number, assignments):
    """Best-effort match of one source dir to an assignment. Returns a list of
    candidate assignments (dicts) - ideally exactly one; zero or many means a
    human needs to resolve it.

    This course has parallel families ("Lab N Submission" vs "Week N
    Assignment", and "Term Project" vs "Term project code review"), plus
    genuine duplicate titles (three "Lab 8 Submission" records). We narrow to
    the most specific family we can, but never silently pick among true
    duplicates - those come back as multiple candidates for a human to resolve.
    """
    if kind == "term_project":
        # Prefer the exact "Term Project", not "... code review" etc.
        exact = [a for a in assignments if a["name"].strip().lower() == "term project"]
        if exact:
            return exact
        return [
            a for a in assignments
            if "term" in a["name"].lower() or "project" in a["name"].lower()
        ]
    if kind == "lab" and number is not None:
        numbered = [a for a in assignments if name_has_number_token(a["name"], number)]
        # Prefer the "Lab N ..." family over "Week N ..." when both exist.
        lab_family = [a for a in numbered if "lab" in a["name"].lower()]
        return lab_family or numbered
    return []


def scan_source_root(source_root, sample):
    """Inventory the on-disk submission tree. Returns a list of dir summaries."""
    root = Path(source_root)
    if not root.is_dir():
        raise NotADirectoryError(f"Source root not found or not a directory: {source_root}")

    dirs = []
    for child in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        student_folders = sorted(
            p.name for p in child.iterdir() if p.is_dir() and not p.name.startswith(".")
        )
        kind, number = classify_source_dir(child.name)
        # Count total files per sampled student folder, to flag multi-file /
        # nested-structure submissions (term projects with images/, etc.).
        samples = []
        for folder in student_folders[:sample]:
            files = [f for f in (child / folder).rglob("*") if f.is_file() and not f.name.startswith(".")]
            has_subdirs = any(p.is_dir() for p in (child / folder).iterdir())
            samples.append({
                "folder": folder,
                "file_count": len(files),
                "has_subdirs": has_subdirs,
            })
        dirs.append({
            "dir": child.name,
            "kind": kind,
            "number": number,
            "student_folder_count": len(student_folders),
            "sample_folders": samples,
        })
    return dirs


def main():
    parser = argparse.ArgumentParser(
        description="Read-only discovery for the submission-upload workflow."
    )
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT,
                        help=f"Root of the lab submission tree (default: {DEFAULT_SOURCE_ROOT}).")
    parser.add_argument("--student-prefix", default=DEFAULT_STUDENT_PREFIX,
                        help=f"Display-name prefix identifying the throwaway students (default: '{DEFAULT_STUDENT_PREFIX}').")
    parser.add_argument("--sample", type=int, default=10,
                        help="How many source student folders to preview per dir (default: 10).")
    args = parser.parse_args()

    if not all([CANVAS_URL, CANVAS_TOKEN, COURSE_ID_STR]):
        raise ValueError(
            "Missing required environment variables!\n"
            "Please ensure your .env file has CANVAS_URL, CANVAS_TOKEN, and COURSE_ID."
        )
    course_id = int(COURSE_ID_STR)

    print(f"\U0001f517 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)
    try:
        course = canvas.get_course(course_id)
    except ResourceDoesNotExist:
        print(f"\n❌ Course {course_id} (from COURSE_ID in .env) does not exist, or this token can't see it.")
        sys.exit(1)
    print(f"\U0001f4da Course: \"{course.name}\" (id {course.id}, status: {course.workflow_state})")

    # --- Students: all enrolled, then the Test Students by name prefix ------
    print(f"\n\U0001f465 Fetching enrolled students...")
    all_students = list(course.get_users(enrollment_type=["student"]))
    prefix_lower = args.student_prefix.lower()
    test_students = sorted(
        (
            {
                "user_id": u.id,
                "name": u.name,
                "sortable_name": getattr(u, "sortable_name", None),
            }
            for u in all_students
            if (u.name or "").lower().startswith(prefix_lower)
        ),
        key=lambda s: s["sortable_name"] or s["name"] or "",
    )
    print(f"   {len(all_students)} student enrollment(s) total; "
          f"{len(test_students)} match prefix \"{args.student_prefix}\":")
    for s in test_students:
        print(f"     - {s['name']}  (user {s['user_id']})")
    if not test_students:
        print(f"   ⚠️  No students matched the prefix. Check --student-prefix "
              f"against the actual display names (e.g. 'Test Student One').")

    # --- Assignments: real ones only, with the gating fields ----------------
    print(f"\n\U0001f4dd Fetching assignments (excluding quizzes/forums)...")
    assignments = []
    for a in course.get_assignments():
        if is_quiz_or_forum(a):
            continue
        assignments.append({
            "id": a.id,
            "name": a.name,
            "submission_types": getattr(a, "submission_types", None) or [],
            "allowed_extensions": getattr(a, "allowed_extensions", None) or [],
            "published": getattr(a, "published", None),
            "points_possible": getattr(a, "points_possible", None),
            "due_at": getattr(a, "due_at", None),
        })
    for a in assignments:
        exts = ",".join(a["allowed_extensions"]) if a["allowed_extensions"] else "(any)"
        types = ",".join(a["submission_types"]) or "(none)"
        upload_ok = "✅" if "online_upload" in a["submission_types"] else "⚠️ "
        print(f"   {upload_ok} {a['name']}  (id {a['id']})  types=[{types}]  ext=[{exts}]")

    # --- Source tree on disk ------------------------------------------------
    print(f"\n\U0001f4c1 Scanning source root: {args.source_root}")
    source_dirs = scan_source_root(args.source_root, args.sample)
    for d in source_dirs:
        print(f"   - {d['dir']}  kind={d['kind']} number={d['number']}  "
              f"({d['student_folder_count']} student folders)")

    # --- Proposed dir -> assignment mapping (REVIEW THIS) -------------------
    print(f"\n\U0001f9ed Proposed source-dir -> assignment mapping (review carefully):")
    mapping = []
    for d in source_dirs:
        cands = propose_assignment_for_dir(d["kind"], d["number"], assignments)
        entry = {
            "source_dir": d["dir"],
            "kind": d["kind"],
            "number": d["number"],
            "candidate_assignment_ids": [c["id"] for c in cands],
            "candidate_assignment_names": [c["name"] for c in cands],
            "assignment_id": cands[0]["id"] if len(cands) == 1 else None,
            "assignment_name": cands[0]["name"] if len(cands) == 1 else None,
        }
        mapping.append(entry)
        if len(cands) == 1:
            c = cands[0]
            upload_ok = "online_upload" in c["submission_types"]
            zip_ok = (not c["allowed_extensions"]) or ("zip" in [e.lower() for e in c["allowed_extensions"]])
            flags = []
            if not upload_ok:
                flags.append("NOT online_upload")
            if not zip_ok:
                flags.append("zip NOT allowed")
            note = ("  ⚠️  " + "; ".join(flags)) if flags else ""
            print(f"   ✅ {d['dir']}  ->  \"{c['name']}\" (id {c['id']}){note}")
        elif len(cands) == 0:
            print(f"   ❌ {d['dir']}  ->  no assignment matched  (resolve manually)")
        else:
            print(f"   ⚠️  {d['dir']}  ->  {len(cands)} candidates, ambiguous:")
            for c in cands:
                print(f"          id {c['id']}: \"{c['name']}\"")

    # --- Write the reviewable plan ------------------------------------------
    zip_globally_viable = all(
        (not a["allowed_extensions"]) or ("zip" in [e.lower() for e in a["allowed_extensions"]])
        for a in assignments
        if a["id"] in {m["assignment_id"] for m in mapping if m["assignment_id"]}
    )

    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "READ-ONLY discovery output. Review dir_to_assignment before running the upload script. "
                "Nothing here has been written to Canvas.",
        "course_id": course.id,
        "course_name": course.name,
        "source_root": args.source_root,
        "student_prefix": args.student_prefix,
        "test_students": test_students,
        "assignments": assignments,
        "source_dirs": source_dirs,
        "dir_to_assignment": mapping,
        "zip_viable_for_mapped_assignments": zip_globally_viable,
    }
    CONTENT_DIR.mkdir(exist_ok=True)
    plan_path = CONTENT_DIR / f"upload_plan_{course.id}.json"
    plan_path.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")

    print(f"\n\U0001f4dd Plan written: {plan_path}")
    print(f"\U0001f389 Discovery complete. {len(test_students)} test student(s), "
          f"{len(assignments)} assignment(s), {len(source_dirs)} source dir(s).")
    print(f"   zip viable for all mapped assignments? "
          f"{'yes' if zip_globally_viable else 'NO - some restrict extensions'}")
    print("\n   Next: review the mapping above, then build/run the upload script.")


if __name__ == "__main__":
    try:
        main()
    except CanvasException as e:
        print(f"\n❌ Canvas API error:\n\n{sanitize(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error:\n\n{sanitize(e)}")
        raise
