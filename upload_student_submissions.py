"""
Canvas Submission Uploader - Upload real, anonymized lab work onto the
throwaway "Test Student" accounts, submitting on each student's behalf so a
grading pipeline can be exercised end to end.

Reads the reviewed plan written by inspect_submission_targets.py
(content/upload_plan_<course_id>.json) - the frozen source-dir -> assignment
mapping (by Canvas ID) and the list of Test Student user_ids. It never looks
anything up by name and never re-derives the mapping; if the plan is missing
or has an unresolved (null) assignment_id, it refuses to run.

What it does per (source dir -> assignment):
  - Orders the Test Students by user_id (stable) and the source student
    folders alphabetically, then pairs folder[i] with student[i]. The pairing
    is deterministic, so a single-target run picks the same folder a full run
    would (see --user-id below), and it's recorded in the audit log.
  - Zips each chosen source folder (its CONTENTS at the archive root, so the
    submission is one file and any internal images/ subfolder + relative links
    survive) and submits that zip on the student's behalf.

How the "on behalf of" submission works (verified against the installed
canvasapi 3.2.0 - do not collapse into assignment.submit(file=..., user=...),
which would credit YOU, the token owner, not the student):
    1. assignment.upload_to_submission(zip_path, user=<student_id>)
         -> POST .../submissions/:user_id/files  (puts the id in the URL path)
    2. assignment.submit({"submission_type": "online_upload",
                          "file_ids": [<id>], "user_id": <student_id>})
         -> submission[user_id] = "submit on behalf of that user".

!!! KNOWN PERMISSION WALL (tested 2026-07-23 against course 680 at Lane CC) !!!
    With the current CANVAS_TOKEN (a Teacher token that CAN grade and CAN
    upload a file into a student's submission folder), STEP 2 is rejected:
        {"status":"unauthorized","errors":[{"message":"user not authorized
         to perform that action"}]}
    Masquerade (as_user_id) is also denied for this token ("Invalid
    as_user_id"). So creating the submission - by either mechanism - needs a
    permission this token lacks. Grading is NOT the gate (grading works).
    Resolution is with whoever provisions Canvas tokens at the institution:
      - Grant the "submit on behalf" permission  -> run this script AS-IS.
      - Grant the "Users - act as" permission     -> run with --masquerade.
    Both are exercised the same way; only the flag differs.

SAFETY (matches the conventions in CLAUDE.md):
  - Dry run by default: no flags => shows exactly what WOULD be uploaded, makes
    no write call and does not even build the zips.
  - --apply is required to write, and a bulk apply additionally makes you type
    the course ID (this creates real submission records on live Canvas objects).
  - Everything is matched by Canvas ID. The token is never printed or logged.
  - An audit JSON is written to content/ for every run (dry or live).

RUN THE SINGLE-TARGET SMOKE TEST FIRST, to prove the file-upload endpoint
accepts this token before creating ~100 submissions:

    python upload_student_submissions.py --assignment-id 22143 --user-id 311 --apply
        # one folder -> one Test Student -> one assignment; verify it lands in
        # SpeedGrader, THEN run the full thing.

Other usage:
    python upload_student_submissions.py
        # dry run over the whole plan (100 submissions previewed, 0 written)

    python upload_student_submissions.py --apply
        # full run: every source dir x every Test Student (after typed confirm)

    python upload_student_submissions.py --assignment-id 22143 --apply
        # just Lab 1 Submission, all 10 Test Students

    python upload_student_submissions.py --keep-zips
        # leave the generated .zip files in downloads/ for inspection
"""
import os
import re
import sys
import json
import zipfile
import argparse
import tempfile
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
DOWNLOAD_ROOT = Path(__file__).parent / "downloads"


def sanitize(text):
    """Strip the Canvas token out of any string before it's printed or logged."""
    text = str(text)
    if CANVAS_TOKEN:
        text = text.replace(CANVAS_TOKEN, "***REDACTED***")
    return text


def confirm(prompt):
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def require_typed_course_id(course):
    print("\n" + "!" * 72)
    print(f"!  ABOUT TO CREATE SUBMISSIONS ON THE LIVE CANVAS COURSE")
    print(f"!  \"{course.name}\" (id {course.id})")
    print("!" * 72)
    typed = input(f"Type the course ID ({course.id}) to proceed, anything else to abort: ").strip()
    return typed == str(course.id)


def load_plan(course_id):
    path = CONTENT_DIR / f"upload_plan_{course_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No plan at {path}. Run inspect_submission_targets.py first to generate it."
        )
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("course_id") != course_id:
        raise ValueError(
            f"Plan {path} is for course {plan.get('course_id')}, but COURSE_ID is {course_id}."
        )
    return plan, path


def safe_component(name):
    """Turn a name into a safe filename component (for the generated zip)."""
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", name).strip("_") or "x"


def iter_source_files(folder):
    """Yield (absolute_path, arcname) for every real file under `folder`,
    with arcname relative to `folder` (contents at the archive root). Skips
    hidden files (.DS_Store etc.)."""
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(folder).parts):
            continue
        yield path, path.relative_to(folder).as_posix()


def make_zip(folder, zip_path):
    """Zip the CONTENTS of `folder` into `zip_path`. Returns the file count."""
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for abspath, arcname in iter_source_files(folder):
            zf.write(abspath, arcname)
            count += 1
    return count


def list_student_folders(source_dir_path):
    return sorted(
        p.name for p in source_dir_path.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )


def build_tasks(plan, source_root, ordered_students, args):
    """Produce the list of upload tasks (before any zipping/writing), applying
    the --assignment-id / --user-id filters. Each task pairs source folder[i]
    with ordered_students[i], so filtering by user keeps the same folder a full
    run would use."""
    tasks = []
    warnings = []
    student_by_id = {s["user_id"]: idx for idx, s in enumerate(ordered_students)}

    for entry in plan["dir_to_assignment"]:
        assignment_id = entry.get("assignment_id")
        source_dir = entry["source_dir"]

        if args.assignment_id is not None and assignment_id != args.assignment_id:
            continue
        if assignment_id is None:
            warnings.append(f"{source_dir}: unresolved assignment_id in plan - skipped.")
            continue

        source_dir_path = Path(source_root) / source_dir
        if not source_dir_path.is_dir():
            warnings.append(f"{source_dir}: source folder missing on disk - skipped.")
            continue

        folders = list_student_folders(source_dir_path)
        for idx, student in enumerate(ordered_students):
            if args.user_id is not None and student["user_id"] != args.user_id:
                continue
            if idx >= len(folders):
                warnings.append(
                    f"{source_dir}: only {len(folders)} source folders, no match for "
                    f"student #{idx + 1} ({student['name']}) - skipped."
                )
                continue
            tasks.append({
                "source_dir": source_dir,
                "assignment_id": assignment_id,
                "assignment_name": entry.get("assignment_name"),
                "student_index": idx,
                "test_student_user_id": student["user_id"],
                "test_student_name": student["name"],
                "source_folder": folders[idx],
                "source_folder_path": str(source_dir_path / folders[idx]),
            })
    return tasks, warnings


def write_log(course_id, log):
    CONTENT_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scope = log.get("mode", "run")
    path = CONTENT_DIR / f"upload_submissions_{course_id}_{scope}_{ts}.json"
    path.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print(f"\U0001f4dd Log written: {path}")
    return path


def do_upload(course, task, zip_dir, keep_zips, masquerade=False):
    """Zip the source folder and submit it on the student's behalf. Returns a
    result dict. Raises nothing - CanvasExceptions are captured into result.

    Two ways to attribute the submission to the student, depending on which
    Canvas permission the token has (see the module docstring):
      - default (proxy submit): submit with submission[user_id]=<student>.
        Needs the "submit on behalf" permission.
      - masquerade=True (act as): send as_user_id=<student> on both calls and
        DON'T set user_id - Canvas treats every call as the student's own.
        Needs the "Users - act as" permission.
    """
    result = {
        "source_dir": task["source_dir"],
        "assignment_id": task["assignment_id"],
        "assignment_name": task["assignment_name"],
        "test_student_user_id": task["test_student_user_id"],
        "test_student_name": task["test_student_name"],
        "source_folder": task["source_folder"],
        "zip_name": None,
        "file_count": None,
        "uploaded_file_id": None,
        "submission_state": None,
        "submitted_at": None,
        "result": None,
        "error": None,
    }

    zip_name = f"{safe_component(task['test_student_name'])}_{safe_component(task['source_dir'])}.zip"
    zip_path = zip_dir / zip_name
    result["zip_name"] = zip_name

    try:
        file_count = make_zip(Path(task["source_folder_path"]), zip_path)
        result["file_count"] = file_count
        if file_count == 0:
            result["result"] = "skipped"
            result["error"] = "source folder had no files to zip"
            print(f"  ⚠️  {task['test_student_name']} / {task['source_dir']}: no files, skipped")
            return result

        assignment = course.get_assignment(task["assignment_id"])
        student_id = task["test_student_user_id"]
        act_as = {"as_user_id": student_id} if masquerade else {}

        # Step 1: upload the zip into the student's submission file area.
        # (This step works with a plain Teacher token; it's step 2 that's gated.)
        success, upload_resp = assignment.upload_to_submission(
            str(zip_path), user=student_id, **act_as
        )
        if not success:
            result["result"] = "failed"
            result["error"] = f"file upload failed: {sanitize(upload_resp)}"
            print(f"  ❌ {task['test_student_name']} / {task['source_dir']}: upload failed")
            return result
        file_id = upload_resp["id"]
        result["uploaded_file_id"] = file_id

        # Step 2: create the submission attributed to the student.
        submission_dict = {"submission_type": "online_upload", "file_ids": [file_id]}
        if not masquerade:
            submission_dict["user_id"] = student_id  # proxy-submit path
        submission = assignment.submit(submission_dict, **act_as)
        result["submission_state"] = getattr(submission, "workflow_state", None)
        result["submitted_at"] = getattr(submission, "submitted_at", None)
        result["result"] = "success"
        print(f"  ✅ {task['test_student_name']} <- {task['source_folder']} "
              f"({file_count} files, file {file_id}) on \"{task['assignment_name']}\" "
              f"[{result['submission_state']}]")
    except CanvasException as e:
        result["result"] = "failed"
        result["error"] = sanitize(e)
        print(f"  ❌ {task['test_student_name']} / {task['source_dir']}: {sanitize(e)}")
    finally:
        if not keep_zips and zip_path.exists():
            try:
                zip_path.unlink()
            except OSError:
                pass
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Upload anonymized lab work onto Test Students, submitting on their behalf."
    )
    parser.add_argument("--assignment-id", type=int, default=None,
                        help="Limit to one assignment (Canvas ID). Default: every mapped assignment.")
    parser.add_argument("--user-id", type=int, default=None,
                        help="Limit to one Test Student (Canvas user ID). Default: all Test Students.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually upload + submit. Without this, only previews (no writes, no zips).")
    parser.add_argument("--keep-zips", action="store_true",
                        help="Keep the generated .zip files under downloads/ instead of deleting them.")
    parser.add_argument("--masquerade", action="store_true",
                        help="Act-as path: submit as each student via as_user_id (needs the "
                             "'Users - act as' permission). Default uses submission[user_id] "
                             "proxy submit (needs the 'submit on behalf' permission).")
    args = parser.parse_args()

    if not all([CANVAS_URL, CANVAS_TOKEN, COURSE_ID_STR]):
        raise ValueError(
            "Missing required environment variables!\n"
            "Please ensure your .env file has CANVAS_URL, CANVAS_TOKEN, and COURSE_ID."
        )
    course_id = int(COURSE_ID_STR)

    plan, plan_path = load_plan(course_id)
    source_root = plan["source_root"]
    print(f"\U0001f4c4 Plan: {plan_path}")
    print(f"   source root: {source_root}")

    # Stable student order: by user_id (Test Student One=311 .. Ten=320).
    ordered_students = sorted(plan["test_students"], key=lambda s: s["user_id"])
    if not ordered_students:
        print("❌ Plan has no test_students. Re-run inspect_submission_targets.py.")
        sys.exit(1)

    tasks, warnings = build_tasks(plan, source_root, ordered_students, args)
    for w in warnings:
        print(f"   ⚠️  {w}")
    if not tasks:
        print("\nNothing to do (no tasks matched the filters, or the plan is unresolved).")
        sys.exit(0)

    print(f"\U0001f517 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)
    try:
        course = canvas.get_course(course_id)
    except ResourceDoesNotExist:
        print(f"\n❌ Course {course_id} (from COURSE_ID in .env) does not exist, or this token can't see it.")
        sys.exit(1)
    print(f"\U0001f4da Course: \"{course.name}\" (id {course.id}, status: {course.workflow_state})")

    scope = []
    if args.assignment_id is not None:
        scope.append(f"assignment {args.assignment_id}")
    if args.user_id is not None:
        scope.append(f"user {args.user_id}")
    scope_str = ", ".join(scope) if scope else "ALL mapped assignments x ALL Test Students"
    print(f"\n\U0001f4cb Planned uploads ({len(tasks)}) - scope: {scope_str}:")
    for t in tasks:
        print(f"   {t['test_student_name']:22s} <- {t['source_dir']}/{t['source_folder']:20s} "
              f"-> \"{t['assignment_name']}\" (id {t['assignment_id']})")

    base_log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "course_id": course.id,
        "course_name": course.name,
        "source_root": source_root,
        "filters": {"assignment_id": args.assignment_id, "user_id": args.user_id},
        "student_order": [
            {"index": i, "user_id": s["user_id"], "name": s["name"]}
            for i, s in enumerate(ordered_students)
        ],
        "planned": tasks,
    }

    if not args.apply:
        base_log["mode"] = "dry_run"
        write_log(course.id, base_log)
        print(f"\nDry run complete. {len(tasks)} submission(s) previewed, 0 written. "
              f"Re-run with --apply to upload.")
        print("   Tip: prove the pipeline first with e.g. "
              f"--assignment-id {tasks[0]['assignment_id']} --user-id {tasks[0]['test_student_user_id']} --apply")
        return

    # --- live write path ---
    is_bulk = len(tasks) > 1
    if not confirm(f"\nUpload {len(tasks)} submission(s) to \"{course.name}\"?"):
        print("Aborted. Nothing was uploaded.")
        return
    if is_bulk and not require_typed_course_id(course):
        print("Aborted. Nothing was uploaded.")
        return

    if args.keep_zips:
        DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        zip_ctx = None
        zip_dir = DOWNLOAD_ROOT
    else:
        zip_ctx = tempfile.TemporaryDirectory(prefix="canvas_upload_")
        zip_dir = Path(zip_ctx.name)

    results = []
    try:
        print(f"\nUploading {len(tasks)} submission(s)...")
        for t in tasks:
            results.append(do_upload(course, t, zip_dir, args.keep_zips, masquerade=args.masquerade))
    finally:
        if zip_ctx is not None:
            zip_ctx.cleanup()

    succeeded = sum(1 for r in results if r["result"] == "success")
    failed = sum(1 for r in results if r["result"] == "failed")
    skipped = sum(1 for r in results if r["result"] == "skipped")

    base_log["mode"] = "live"
    base_log["results"] = results
    base_log["summary"] = {"success": succeeded, "failed": failed, "skipped": skipped}
    write_log(course.id, base_log)

    print(f"\n\U0001f389 Done. {succeeded} uploaded, {failed} failed, {skipped} skipped.")
    if failed:
        print("   Some uploads failed - see the log above/JSON for the Canvas error text.")


if __name__ == "__main__":
    try:
        main()
    except CanvasException as e:
        print(f"\n❌ Canvas API error:\n\n{sanitize(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error:\n\n{sanitize(e)}")
        raise
