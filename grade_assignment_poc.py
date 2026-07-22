"""
Canvas Grading Proof-of-Concept - Check whether this CANVAS_TOKEN can submit
a grade for one assignment submission via the API.

This is a minimal probe, not a bulk grading tool: it targets exactly one
assignment + one user (matched by Canvas ID, never by name) and shows the
current submission state before touching anything.

  Dry run (default):
    Connects, fetches the assignment and the target user's current
    submission, and prints what WOULD change. No write call is made.

  Apply mode (--apply):
    Same preview, then asks for confirmation, then does one
    PUT .../submissions/:user_id call and prints Canvas's response
    (score, grade, graded_at, grader_id) so you can see the write actually
    landed - or see exactly why it didn't (e.g. a 401/403 means this token
    can't grade in this course).

Usage:
    python grade_assignment_poc.py --assignment-id 22146 --user-id 987654 --grade 10
        # dry run - shows the assignment, the user, and the current submission

    python grade_assignment_poc.py --assignment-id 22146 --user-id 987654 --grade 10 --apply
        # after confirming, actually submits the grade

Find --user-id from the Test Student's submission in SpeedGrader (or People
page for a real enrollment) - this script does not look anyone up by name.
"""
import os
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


def sanitize(text):
    """Strip the Canvas token out of any string before it's printed or logged."""
    text = str(text)
    if CANVAS_TOKEN:
        text = text.replace(CANVAS_TOKEN, "***REDACTED***")
    return text


def confirm(prompt):
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def main():
    parser = argparse.ArgumentParser(
        description="Proof of concept: submit one grade via the Canvas API."
    )
    parser.add_argument("--assignment-id", type=int, required=True, help="Canvas assignment ID.")
    parser.add_argument("--user-id", type=int, required=True, help="Canvas user ID to grade (e.g. the Test Student).")
    parser.add_argument("--grade", required=True, help="Grade to submit, e.g. '10', '100%%', or 'A'.")
    parser.add_argument("--comment", default=None, help="Optional grader comment to attach.")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually submit the grade after confirmation. Without this, only previews."
    )
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

    try:
        assignment = course.get_assignment(args.assignment_id)
    except ResourceDoesNotExist:
        print(
            f"\n❌ Assignment {args.assignment_id} does not exist in course {course.id} "
            f"(\"{course.name}\").\n"
            "   Double-check --assignment-id - it's the number in the assignment's Canvas URL."
        )
        sys.exit(1)
    print(f"\U0001f4dd Assignment: \"{assignment.name}\" (id {assignment.id}, points possible: {assignment.points_possible})")

    try:
        submission = assignment.get_submission(args.user_id)
    except ResourceDoesNotExist:
        print(
            f"\n❌ No submission found for user {args.user_id} on assignment {assignment.id} "
            f"(\"{assignment.name}\") in course {course.id}.\n"
            f"   Canvas returns this same 'Not Found' whether user {args.user_id} doesn't "
            "exist at all, or exists but isn't enrolled in this course - it can't tell you "
            "which. Double-check --user-id (from the SpeedGrader URL or the course's People "
            "page) and make sure that user is actually enrolled here."
        )
        sys.exit(1)
    current_grade = getattr(submission, "grade", None)
    current_score = getattr(submission, "score", None)
    workflow_state = getattr(submission, "workflow_state", "unknown")
    print(f"\U0001f464 User {args.user_id} - current grade: {current_grade!r} (score: {current_score!r}, state: {workflow_state})")
    print(f"\nProposed grade: {args.grade!r}" + (f"  comment: {args.comment!r}" if args.comment else ""))

    log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "course_id": course.id,
        "assignment_id": assignment.id,
        "assignment_name": assignment.name,
        "user_id": args.user_id,
        "before": {"grade": current_grade, "score": current_score, "workflow_state": workflow_state},
        "proposed_grade": args.grade,
        "comment": args.comment,
    }

    if not args.apply:
        log["mode"] = "dry_run"
        write_log(course.id, assignment.id, args.user_id, log)
        print("\nDry run complete. No changes were made. Re-run with --apply to submit this grade.")
        return

    if not confirm(f"Submit grade {args.grade!r} for user {args.user_id} on \"{assignment.name}\"?"):
        print("Aborted. Nothing was submitted.")
        log["mode"] = "aborted"
        write_log(course.id, assignment.id, args.user_id, log)
        return

    submission_payload = {"posted_grade": args.grade}
    kwargs = {"submission": submission_payload}
    if args.comment:
        kwargs["comment"] = {"text_comment": args.comment}

    try:
        submission.edit(**kwargs)
        print(f"\n✅ Grade submitted. Canvas now reports:")
        print(f"   grade: {submission.grade!r}  score: {submission.score!r}")
        print(f"   graded_at: {getattr(submission, 'graded_at', None)}  grader_id: {getattr(submission, 'grader_id', None)}")
        log["mode"] = "live"
        log["after"] = {
            "grade": getattr(submission, "grade", None),
            "score": getattr(submission, "score", None),
            "graded_at": getattr(submission, "graded_at", None),
            "grader_id": getattr(submission, "grader_id", None),
        }
        log["result"] = "success"
    except CanvasException as e:
        msg = sanitize(e)
        print(f"\n❌ Grade submission failed: {msg}")
        log["mode"] = "live"
        log["result"] = "failed"
        log["error"] = msg
    finally:
        write_log(course.id, assignment.id, args.user_id, log)


def write_log(course_id, assignment_id, user_id, log):
    CONTENT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = CONTENT_DIR / f"grade_poc_{course_id}_{assignment_id}_{user_id}_{timestamp}.json"
    path.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print(f"\U0001f4dd Log written: {path}")


if __name__ == "__main__":
    try:
        main()
    except CanvasException as e:
        print(f"\n❌ Canvas API error:\n\n{sanitize(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error:\n\n{sanitize(e)}")
        raise
