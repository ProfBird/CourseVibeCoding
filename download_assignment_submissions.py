"""
Canvas Assignment Submission Downloader (proof of concept) - Download the
actual work students submitted for ONE assignment, matched by Canvas ID.

This is different from download_course_json.py, which exports assignment
METADATA (name, description, due date, points) for every assignment in the
course but explicitly skips file contents. This script pulls the submitted
work itself for a single assignment:

  - online_upload      -> downloads every attached file
  - online_text_entry  -> saves the submitted text as an .html file
  - online_url         -> recorded in the manifest (nothing to download)
  - anything else (media_recording, online_quiz, discussion_topic, or no
    submission yet) -> recorded in the manifest only, not downloaded

Output: downloads/assignment_<assignment_id>/
  - one subfolder per submitter: user_<user_id>/
  - manifest.json summarizing what was found for every submitter

Read-only: this never writes to Canvas, so there's no --apply flag. But the
files it downloads are real student work (a FERPA-protected education
record once run against real enrollments, not just the Test Student) -
downloads/ is gitignored, and nothing here prints, commits, or transmits
that content anywhere beyond your local disk.

Usage:
    python download_assignment_submissions.py
        # lists the course's assignments and asks you to pick one, then
        # downloads every submitter's work for it

    python download_assignment_submissions.py --assignment-id 8846
        # skips the picker, downloads every submitter's work for that
        # assignment directly

    python download_assignment_submissions.py --assignment-id 8846 --user-id 1494
        # downloads just one submitter's work (e.g. the Test Student)
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime
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

DOWNLOAD_ROOT = Path(__file__).parent / "downloads"

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def sanitize(text):
    """Strip the Canvas token out of any string before it's printed or logged."""
    text = str(text)
    if CANVAS_TOKEN:
        text = text.replace(CANVAS_TOKEN, "***REDACTED***")
    return text


def is_quiz_or_forum(assignment):
    """True if this assignment shell is actually a quiz (classic or New
    Quizzes) or a graded discussion/forum, per CLAUDE.md's note that both
    get exposed through get_assignments() alongside plain assignments.

    submission_types alone can't tell New Quizzes apart from some other
    external-tool assignment, so that case also checks the LTI launch URL
    for Instructure's quiz-lti domain (verified against this course's real
    quiz assignments).
    """
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


def choose_assignment(course):
    """List the course's assignments and ask the user to pick one, or cancel.
    Returns None if the user cancels."""
    assignments = [a for a in course.get_assignments() if not is_quiz_or_forum(a)]
    if not assignments:
        raise ValueError(f"No plain assignments found in course {course.id} (only quizzes/forums?).")

    print(f"\nAssignments in \"{course.name}\":")
    for i, a in enumerate(assignments, start=1):
        due = getattr(a, "due_at", None) or "no due date"
        flag = "" if getattr(a, "published", True) else "  [unpublished]"
        print(f"  {i}. {a.name}  (id {a.id}, due {due}){flag}")
    print("  0. Cancel")

    while True:
        raw = input(f"Choose an assignment # (1-{len(assignments)}, or 0 to cancel): ").strip()
        if raw == "0":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(assignments):
            return assignments[int(raw) - 1]
        print("Please enter a valid number from the list, or 0 to cancel.")


def safe_filename(name):
    """Collapse a Canvas-provided filename to a safe, flat basename."""
    name = os.path.basename(name or "file")
    name = _UNSAFE_FILENAME_RE.sub("_", name)
    return name or "file"


def process_submission(submission, out_dir):
    """Download whatever's downloadable for one submission; return a manifest entry."""
    user_id = submission.user_id
    submission_type = getattr(submission, "submission_type", None)
    workflow_state = getattr(submission, "workflow_state", "unknown")

    entry = {
        "user_id": user_id,
        "submission_type": submission_type,
        "workflow_state": workflow_state,
        "submitted_at": getattr(submission, "submitted_at", None),
        "files": [],
        "text_saved": False,
        "url": None,
        "note": None,
    }

    if workflow_state == "unsubmitted" or submission_type is None:
        entry["note"] = "No submission yet."
        print(f"  ➖ user {user_id}: no submission yet")
        return entry

    user_dir = out_dir / f"user_{user_id}"

    if submission_type == "online_upload":
        attachments = getattr(submission, "attachments", None) or []
        if not attachments:
            entry["note"] = "online_upload with no attachments."
            print(f"  ⚠️  user {user_id}: online_upload but no attachments found")
            return entry
        user_dir.mkdir(parents=True, exist_ok=True)
        for att in attachments:
            filename = safe_filename(getattr(att, "filename", None) or getattr(att, "display_name", None))
            dest = user_dir / filename
            att.download(str(dest))
            size = dest.stat().st_size
            entry["files"].append({"filename": filename, "size": size})
            print(f"  ✅ user {user_id}: downloaded {filename} ({size} bytes)")

    elif submission_type == "online_text_entry":
        body = getattr(submission, "body", None) or ""
        user_dir.mkdir(parents=True, exist_ok=True)
        dest = user_dir / "submission.html"
        dest.write_text(body, encoding="utf-8")
        entry["text_saved"] = True
        print(f"  ✅ user {user_id}: saved text submission to {dest.name}")

    elif submission_type == "online_url":
        entry["url"] = getattr(submission, "url", None)
        print(f"  \U0001f517 user {user_id}: submitted URL recorded in manifest ({entry['url']})")

    else:
        entry["note"] = f"submission_type '{submission_type}' not downloaded by this POC."
        print(f"  ℹ️  user {user_id}: submission_type '{submission_type}' recorded, not downloaded")

    return entry


def main():
    parser = argparse.ArgumentParser(
        description="Proof of concept: download submitted work for one Canvas assignment."
    )
    parser.add_argument("--assignment-id", type=int, default=None, help="Canvas assignment ID. If omitted, lists the course's assignments to choose from.")
    parser.add_argument("--user-id", type=int, default=None, help="Limit to one submitter's Canvas user ID (e.g. the Test Student). Default: all submitters.")
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

    if args.assignment_id is not None:
        try:
            assignment = course.get_assignment(args.assignment_id)
        except ResourceDoesNotExist:
            print(
                f"\n❌ Assignment {args.assignment_id} does not exist in course {course.id} "
                f"(\"{course.name}\").\n"
                "   Double-check --assignment-id - it's the number in the assignment's Canvas URL."
            )
            sys.exit(1)
    else:
        assignment = choose_assignment(course)
        if assignment is None:
            print("Cancelled. Nothing was downloaded.")
            return
    print(f"\U0001f4dd Assignment: \"{assignment.name}\" (id {assignment.id})")

    if args.user_id is not None:
        try:
            submissions = [assignment.get_submission(args.user_id)]
        except ResourceDoesNotExist:
            print(
                f"\n❌ No submission record for user {args.user_id} on assignment "
                f"{assignment.id}. Double-check --user-id and that they're enrolled here."
            )
            sys.exit(1)
    else:
        submissions = list(assignment.get_submissions())

    out_dir = DOWNLOAD_ROOT / f"assignment_{assignment.id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nProcessing {len(submissions)} submission(s) -> {out_dir}")
    manifest_entries = []
    for submission in submissions:
        try:
            manifest_entries.append(process_submission(submission, out_dir))
        except CanvasException as e:
            print(f"  ❌ user {getattr(submission, 'user_id', '?')}: download failed - {sanitize(e)}")
            manifest_entries.append({
                "user_id": getattr(submission, "user_id", None),
                "error": sanitize(e),
            })

    manifest = {
        "downloaded_at": datetime.now().isoformat(),
        "course_id": course.id,
        "course_name": course.name,
        "assignment_id": assignment.id,
        "assignment_name": assignment.name,
        "submissions": manifest_entries,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    downloaded_files = sum(len(e.get("files", [])) for e in manifest_entries)
    print(f"\n\U0001f389 Done. {len(manifest_entries)} submission(s) processed, {downloaded_files} file(s) downloaded.")
    print(f"\U0001f4dd Manifest: {manifest_path}")


if __name__ == "__main__":
    try:
        main()
    except CanvasException as e:
        print(f"\n❌ Canvas API error:\n\n{sanitize(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error:\n\n{sanitize(e)}")
        raise
