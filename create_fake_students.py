"""
Canvas Fake Student Populator - Create a batch of throwaway student accounts
and enroll them in a course, for testing group work, peer review, discussions,
gradebook, etc. Also supports removing a batch it created.

IMPORTANT - permissions: creating a brand-new Canvas user (as opposed to
enrolling one that already exists) calls POST /accounts/:account_id/users,
which requires account-admin rights ("Users - manage" at the account level).
A course-scoped Teacher token cannot do this - Canvas will return a 401/403.
If that happens, you need an admin-scoped CANVAS_TOKEN (ask your Canvas
admin), or an admin API token on the account that owns the sandbox course.

IMPORTANT - only use this against a sandbox course. It creates real, if
fake, user records in Canvas (visible in the account's People list). Fake
accounts use the @example.invalid domain (reserved by RFC 2606 - it can
never resolve to a real mailbox) and are created with confirmations skipped,
so Canvas never emails anyone. Nothing here touches real students.

Every batch is stamped with a --tag (default: a UTC timestamp) and recorded
in a JSON log under content/. The `remove` subcommand replays that log to
conclude the enrollments and delete the fake user accounts again - keep the
log file around until you've cleaned up.

Usage:
    python create_fake_students.py create
        # dry run - shows the course and the batch of fake students that
        # WOULD be created, makes no changes

    python create_fake_students.py create --count 8 --apply
        # after confirming the course and typing its ID, actually creates 8
        # fake students and enrolls them as active students

    python create_fake_students.py remove --tag fake20260722T153000Z
        # dry run - shows which fake students from that batch WOULD be removed

    python create_fake_students.py remove --tag fake20260722T153000Z --apply
        # after confirming, concludes their enrollments and deletes the
        # fake user accounts

Always run against a sandbox course first (point COURSE_ID at it in .env).
"""
import os
import sys
import json
import time
import argparse
from pathlib import Path
from dotenv import load_dotenv
from canvasapi import Canvas
from canvasapi.exceptions import CanvasException, Unauthorized, Forbidden, InvalidAccessToken

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
FAKE_EMAIL_DOMAIN = "example.invalid"


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
    print(f"!  ABOUT TO WRITE TO THE LIVE CANVAS COURSE")
    print(f"!  \"{course.name}\" (id {course.id})")
    print("!" * 72)
    typed = input(f"Type the course ID ({course.id}) to proceed, anything else to abort: ").strip()
    return typed == str(course.id)


def log_path(course_id, tag, action):
    return CONTENT_DIR / f"fake_students_{action}_{course_id}_{tag}.json"


def write_log(path, data):
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"\U0001f4dd Log written: {path}")


def connect_and_confirm_course(course_id):
    print(f"\U0001f517 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)
    course = canvas.get_course(course_id)
    print(f"\U0001f4da Course: \"{course.name}\" (id {course.id}, status: {course.workflow_state})")
    if not confirm("Is this the correct course to process?"):
        print("Aborted. Nothing was read or changed.")
        return None, None
    return canvas, course


def check_admin_access(canvas, course):
    """Probe whether this token can even read the account that owns the
    course. Creating users needs account-admin rights; a course-scoped
    Teacher token typically can't read the account either, so this catches
    the eventual 401/403 early - before the user types the course ID to
    confirm a live run.
    """
    try:
        canvas.get_account(course.account_id)
        return True
    except (Unauthorized, Forbidden, InvalidAccessToken) as e:
        print(
            f"\n\U0001f6d1 Warning: this token can't read account {course.account_id} "
            f"({sanitize(e)}).\n"
            "   Creating new Canvas users requires account-admin rights - a "
            "course-level Teacher token won't be able to do this. The 'create' "
            "step below will likely fail the same way. You'll need an "
            "admin-scoped CANVAS_TOKEN (ask your Canvas admin), or run this "
            "against an instance/sub-account where you hold admin rights."
        )
        return False
    except CanvasException as e:
        print(f"\n⚠️  Warning: couldn't check account access ({sanitize(e)}).")
        return False


def build_batch(course_id, tag, count, prefix):
    batch = []
    for i in range(1, count + 1):
        login_id = f"fakestudent.{tag}.{i:02d}@{FAKE_EMAIL_DOMAIN}"
        batch.append({
            "index": i,
            "name": f"{prefix} {i:02d}",
            "sortable_name": f"{i:02d}, {prefix}",
            "login_id": login_id,
        })
    return batch


def print_batch(batch):
    print(f"\nPlanned fake students ({len(batch)}):")
    for entry in batch:
        print(f"  - {entry['name']}  <{entry['login_id']}>")


def create_batch(canvas, course, batch):
    account = canvas.get_account(course.account_id)
    completed, failed = [], []
    for entry in batch:
        try:
            user = account.create_user(
                pseudonym={"unique_id": entry["login_id"], "send_confirmation": False},
                user={
                    "name": entry["name"],
                    "short_name": entry["name"],
                    "sortable_name": entry["sortable_name"],
                    "terms_of_use": True,
                },
                communication_channel={
                    "type": "email",
                    "address": entry["login_id"],
                    "skip_confirmation": True,
                },
            )
            enrollment = course.enroll_user(
                user,
                enrollment={
                    "type": "StudentEnrollment",
                    "enrollment_state": "active",
                    "notify": False,
                },
            )
            completed.append({
                **entry,
                "user_id": user.id,
                "enrollment_id": enrollment.id,
            })
            print(f"  ✅ Created + enrolled: {entry['name']} (user {user.id}, enrollment {enrollment.id})")
        except CanvasException as e:
            msg = sanitize(e)
            print(f"  ❌ Failed: {entry['name']} - {msg}")
            failed.append({**entry, "error": msg})
            if isinstance(e, (Unauthorized, Forbidden, InvalidAccessToken)):
                print(
                    "\n\U0001f6d1 This is a permissions error, not a one-off failure. "
                    "Creating new Canvas users requires an account-admin token "
                    "(POST /accounts/:id/users) - a course-level Teacher token can't "
                    "do this. Stopping instead of retrying the remaining "
                    f"{len(batch) - len(completed) - len(failed)} student(s)."
                )
                break
    return completed, failed


def remove_batch(canvas, course, records):
    from canvasapi.enrollment import Enrollment

    completed, failed = [], []
    canvas_account = canvas.get_account(course.account_id)
    for entry in records:
        name = entry.get("name", f"user {entry.get('user_id')}")
        try:
            enrollment = Enrollment(
                course._requester, {"id": entry["enrollment_id"], "course_id": course.id}
            )
            enrollment.deactivate("conclude")
        except CanvasException as e:
            print(f"  ⚠️  Could not conclude enrollment for {name}: {sanitize(e)}")
        try:
            canvas_account.delete_user(entry["user_id"])
            print(f"  ✅ Removed: {name} (user {entry['user_id']})")
            completed.append(entry)
        except CanvasException as e:
            msg = sanitize(e)
            print(f"  ❌ Failed to delete user for {name}: {msg}")
            failed.append({**entry, "error": msg})
    return completed, failed


def cmd_create(args):
    if not all([CANVAS_URL, CANVAS_TOKEN, COURSE_ID_STR]):
        raise ValueError("Missing CANVAS_URL / CANVAS_TOKEN / COURSE_ID in .env")
    course_id = int(COURSE_ID_STR)
    tag = args.tag or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    canvas, course = connect_and_confirm_course(course_id)
    if course is None:
        return

    has_admin_access = check_admin_access(canvas, course)

    batch = build_batch(course_id, tag, args.count, args.prefix)
    print_batch(batch)

    if not args.apply:
        write_log(
            log_path(course_id, tag, "created"),
            {"mode": "dry_run", "course_id": course_id, "tag": tag, "planned": batch},
        )
        print("\nDry run complete. No changes were made. Re-run with --apply to create these.")
        return

    if not has_admin_access:
        print("\nAborted: an admin-scoped token is required to create users. See warning above.")
        return

    if not require_typed_course_id(course):
        print("Aborted. Nothing was created.")
        return

    print(f"\nCreating {len(batch)} fake student(s)...")
    completed, failed = create_batch(canvas, course, batch)

    write_log(
        log_path(course_id, tag, "created"),
        {
            "mode": "live",
            "course_id": course_id,
            "tag": tag,
            "completed": completed,
            "failed": failed,
        },
    )

    print(f"\n\U0001f389 Done. {len(completed)} created, {len(failed)} failed.")
    if completed:
        print(f"   To remove this batch later: python create_fake_students.py remove --tag {tag} --apply")


def cmd_remove(args):
    if not all([CANVAS_URL, CANVAS_TOKEN, COURSE_ID_STR]):
        raise ValueError("Missing CANVAS_URL / CANVAS_TOKEN / COURSE_ID in .env")
    course_id = int(COURSE_ID_STR)

    src = log_path(course_id, args.tag, "created")
    if not src.exists():
        print(f"❌ No log found at {src}. Pass the --tag used when the batch was created.")
        return
    data = json.loads(src.read_text(encoding="utf-8"))
    records = data.get("completed", [])
    if not records:
        print(f"Nothing to remove - log at {src} has no completed entries (was it a dry run?).")
        return

    canvas, course = connect_and_confirm_course(course_id)
    if course is None:
        return

    print(f"\nFake students to remove ({len(records)}):")
    for entry in records:
        print(f"  - {entry['name']}  <{entry['login_id']}>  (user {entry['user_id']})")

    if not args.apply:
        print("\nDry run complete. No changes were made. Re-run with --apply to remove these.")
        return

    if not require_typed_course_id(course):
        print("Aborted. Nothing was removed.")
        return

    print(f"\nRemoving {len(records)} fake student(s)...")
    completed, failed = remove_batch(canvas, course, records)

    write_log(
        log_path(course_id, args.tag, "removed"),
        {"mode": "live", "course_id": course_id, "tag": args.tag, "completed": completed, "failed": failed},
    )

    print(f"\n\U0001f389 Done. {len(completed)} removed, {len(failed)} failed.")


def main():
    parser = argparse.ArgumentParser(
        description="Create or remove a batch of fake/test students in a Canvas course."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create fake students and enroll them.")
    p_create.add_argument("--count", type=int, default=5, help="How many fake students to create (default: 5).")
    p_create.add_argument("--prefix", default="Test Student", help="Display name prefix (default: 'Test Student').")
    p_create.add_argument("--tag", default=None, help="Batch tag, used in emails and the log filename (default: UTC timestamp).")
    p_create.add_argument("--apply", action="store_true", help="Actually create + enroll after a typed confirmation. Without this, only prints the plan.")
    p_create.set_defaults(func=cmd_create)

    p_remove = sub.add_parser("remove", help="Remove a previously created batch of fake students.")
    p_remove.add_argument("--tag", required=True, help="The --tag (or auto-generated timestamp) from the 'create' run.")
    p_remove.add_argument("--apply", action="store_true", help="Actually conclude enrollments + delete users after a typed confirmation. Without this, only prints the plan.")
    p_remove.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except CanvasException as e:
        print(f"\n❌ Canvas API error:\n\n{sanitize(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error:\n\n{sanitize(e)}")
        raise
