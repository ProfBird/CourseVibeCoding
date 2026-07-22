"""
Canvas Course Date Updater - Apply an approved due/unlock/lock date mapping
to specific Canvas items, safely.

This script does NOT decide what the new dates should be, and it does NOT
touch assignment/page/discussion body text. It applies a mapping file you
build and approve ahead of time, showing every old -> new value before
anything happens, and only writes to Canvas after --live and a typed final
confirmation.

Mapping file format (JSON, default: content/date_mapping.example.json):

    {
      "semester_start": "2026-09-28T00:00:00Z",
      "semester_end": "2026-12-13T23:59:59Z",
      "items": [
        {
          "type": "assignment",
          "id": 22146,
          "name": "Lab 2 Submission",
          "approved": true,
          "new_due_at": "2026-10-12T06:59:00Z",
          "new_unlock_at": null,
          "new_lock_at": null,
          "note": "Description text also needs a manual edit - not done by this script.",
          "overrides": [
            {"id": 555, "reviewed": true, "new_due_at": "2026-10-13T06:59:00Z"}
          ]
        },
        {
          "type": "manual_review",
          "id": 32158,
          "name": "I accidentally forgot to record... (page)",
          "reason": "Written 'Tuesday's class' / 'Fall 2022' reference in page body - not a structured date field."
        }
      ]
    }

Supported "type" values and the fields each one accepts:
    assignment  - new_due_at, new_unlock_at, new_lock_at (+ optional "overrides")
    quiz        - new_due_at, new_unlock_at, new_lock_at
    discussion  - new_delayed_post_at, new_lock_at
    module      - new_unlock_at
    manual_review - no date fields; requires "reason". Never sent to Canvas;
                    always recorded under "skipped" in the audit log.

Rules enforced:
    - Only items with "approved": true (or reviewed override entries with
      "reviewed": true) are ever proposed for a Canvas write.
    - Every date must be an explicit UTC ISO 8601 string (must end in 'Z' or
      include a numeric UTC offset) - ambiguous/naive dates are a hard error.
    - Any date outside [semester_start, semester_end], any unsupported
      type/field combination, or any unparsable date aborts the ENTIRE run
      before Canvas is even contacted - nothing partial happens.
    - Existing assignment overrides are always reported, and are only ever
      changed if their own ID appears in "overrides" with "reviewed": true.

Usage:
    python update_course_dates.py
        # dry run against the example mapping - shows diffs, makes no changes

    python update_course_dates.py --mapping content/date_mapping.json ^
        --semester-start 2026-09-28T00:00:00Z --semester-end 2026-12-13T23:59:59Z
        # dry run against your real mapping

    python update_course_dates.py --mapping content/date_mapping.json ^
        --semester-start 2026-09-28T00:00:00Z --semester-end 2026-12-13T23:59:59Z --live
        # same, then (after a typed confirmation) actually writes to Canvas

Always run against a sandbox course first (point COURSE_ID at it in .env).
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
from canvasapi.exceptions import CanvasException

# Some Windows consoles (cmd.exe, or a non-UTF-8 codepage) can't print the
# emoji/checkmarks used below and would crash mid-run on a print(). Force
# UTF-8 output where possible; silently keep going if that's not supported.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Load environment variables from .env file
# This keeps credentials out of your code
load_dotenv()

# Retrieve Canvas API credentials from environment variables
CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')

CONTENT_DIR = Path(__file__).parent / "content"
DOWNLOAD_ROOT = Path(__file__).parent / "downloads"
DEFAULT_MAPPING_PATH = CONTENT_DIR / "date_mapping.example.json"

# ISO 8601 datetime that must be explicit about UTC: a trailing 'Z' or a
# numeric +HH:MM / -HH:MM offset. Naive datetimes are rejected on purpose -
# a script that writes due dates should never have to guess a timezone.
_TZ_EXPLICIT_RE = re.compile(r"(Z|[+-]\d{2}:\d{2})$")


# ---------------------------------------------------------------------------
# Type registry: how to fetch and update each kind of Canvas item, and which
# mapping fields are valid for it.
# ---------------------------------------------------------------------------

def _assignment_fetch(course, item_id):
    return course.get_assignment(item_id)


def _assignment_update(obj, payload):
    return obj.edit(assignment=payload)


def _quiz_fetch(course, item_id):
    return course.get_quiz(item_id)


def _quiz_update(obj, payload):
    return obj.edit(quiz=payload)


def _discussion_fetch(course, item_id):
    return course.get_discussion_topic(item_id)


def _discussion_update(obj, payload):
    return obj.update(**payload)


def _module_fetch(course, item_id):
    return course.get_module(item_id)


def _module_update(obj, payload):
    return obj.edit(module=payload)


TYPE_HANDLERS = {
    "assignment": {
        "label": "Assignment",
        "fetch": _assignment_fetch,
        "update": _assignment_update,
        "fields": {"new_due_at": "due_at", "new_unlock_at": "unlock_at", "new_lock_at": "lock_at"},
        "supports_overrides": True,
    },
    "quiz": {
        "label": "Quiz",
        "fetch": _quiz_fetch,
        "update": _quiz_update,
        "fields": {"new_due_at": "due_at", "new_unlock_at": "unlock_at", "new_lock_at": "lock_at"},
        "supports_overrides": False,
    },
    "discussion": {
        "label": "Discussion",
        "fetch": _discussion_fetch,
        "update": _discussion_update,
        "fields": {"new_delayed_post_at": "delayed_post_at", "new_lock_at": "lock_at"},
        "supports_overrides": False,
    },
    "module": {
        "label": "Module",
        "fetch": _module_fetch,
        "update": _module_update,
        "fields": {"new_unlock_at": "unlock_at"},
        "supports_overrides": False,
    },
}


class MappingError(Exception):
    """Raised for problems in the mapping file itself (structure, dates, bounds).

    Any MappingError aborts the whole run before Canvas is contacted -
    these are all things a human reviewing the mapping file could have
    caught, so a partial run isn't an acceptable outcome.
    """


def sanitize(text):
    """Strip the Canvas token out of any string before it's printed or logged."""
    text = str(text)
    if CANVAS_TOKEN:
        text = text.replace(CANVAS_TOKEN, "***REDACTED***")
    return text


def parse_utc_iso8601(value, field_label):
    """Parse a mapping date string into an aware, UTC datetime.

    Requires an explicit 'Z' or numeric UTC offset - naive datetimes are
    rejected so the script never has to guess what timezone was intended.
    """
    if not isinstance(value, str) or not _TZ_EXPLICIT_RE.search(value):
        raise MappingError(
            f"{field_label}: '{value}' is not a UTC ISO 8601 string. "
            "Use an explicit 'Z' suffix, e.g. '2026-10-12T06:59:00Z'."
        )
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise MappingError(f"{field_label}: could not parse '{value}' as a date ({e}).")
    return dt.astimezone(timezone.utc)


def load_mapping(path):
    """Load and JSON-parse the mapping file. Raises MappingError on bad JSON
    or an unexpected top-level shape."""
    if not path.exists():
        raise MappingError(f"Mapping file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise MappingError(f"{path} is not valid JSON: {e}")

    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise MappingError(f"{path} must be a JSON object with an 'items' list.")
    return data


def resolve_semester_bounds(mapping, args):
    """CLI --semester-start/--semester-end override the mapping file's
    semester_start/semester_end. At least one source must supply each."""
    start_raw = args.semester_start or mapping.get("semester_start")
    end_raw = args.semester_end or mapping.get("semester_end")
    if not start_raw or not end_raw:
        raise MappingError(
            "Semester start/end must be supplied via --semester-start/--semester-end "
            "or 'semester_start'/'semester_end' in the mapping file."
        )
    start = parse_utc_iso8601(start_raw, "semester_start")
    end = parse_utc_iso8601(end_raw, "semester_end")
    if start >= end:
        raise MappingError(f"semester_start ({start}) must be before semester_end ({end}).")
    return start, end


def validate_items(items, semester_start, semester_end):
    """Static validation of every item in the mapping file - no Canvas calls.

    Collects every problem found (rather than stopping at the first one) so
    a mapping file can be fixed in one pass. Raises MappingError listing all
    of them if any exist.
    """
    errors = []

    for idx, item in enumerate(items):
        where = f"items[{idx}]"
        item_type = item.get("type")

        if item_type == "manual_review":
            if not item.get("reason"):
                errors.append(f"{where}: type 'manual_review' requires a 'reason'.")
            if not item.get("id") or not item.get("name"):
                errors.append(f"{where}: manual_review entries need 'id' and 'name' for the log.")
            continue

        if item_type not in TYPE_HANDLERS:
            errors.append(
                f"{where}: unknown type '{item_type}'. Must be one of "
                f"{sorted(TYPE_HANDLERS) + ['manual_review']}."
            )
            continue

        handler = TYPE_HANDLERS[item_type]
        where = f"{where} ({handler['label']} id={item.get('id')})"

        if not isinstance(item.get("id"), int):
            errors.append(f"{where}: 'id' must be an integer Canvas item ID.")

        requested_fields = [k for k in item if k.startswith("new_")]
        unsupported = [k for k in requested_fields if k not in handler["fields"]]
        for key in unsupported:
            errors.append(
                f"{where}: '{key}' is not valid for type '{item_type}'. "
                f"Valid fields: {sorted(handler['fields'])}."
            )

        has_overrides = bool(item.get("overrides"))
        real_requested = [k for k in requested_fields if k not in unsupported and item[k] is not None]
        if not real_requested and not has_overrides:
            errors.append(f"{where}: no new_* date fields and no overrides - nothing to change.")

        for key in real_requested:
            try:
                dt = parse_utc_iso8601(item[key], f"{where}.{key}")
                if not (semester_start <= dt <= semester_end):
                    errors.append(
                        f"{where}.{key}: {item[key]} falls outside the semester "
                        f"({semester_start.isoformat()} - {semester_end.isoformat()})."
                    )
            except MappingError as e:
                errors.append(str(e))

        if has_overrides and not handler["supports_overrides"]:
            errors.append(f"{where}: type '{item_type}' does not support 'overrides'.")
        elif has_overrides:
            for ov_idx, ov in enumerate(item["overrides"]):
                ov_where = f"{where}.overrides[{ov_idx}] (id={ov.get('id')})"
                if not isinstance(ov.get("id"), int):
                    errors.append(f"{ov_where}: 'id' must be an integer override ID.")
                if "reviewed" not in ov or not isinstance(ov["reviewed"], bool):
                    errors.append(f"{ov_where}: 'reviewed' must be true or false, explicitly.")
                ov_fields = [k for k in ov if k.startswith("new_")]
                for key in ov_fields:
                    if key not in handler["fields"]:
                        errors.append(f"{ov_where}: '{key}' is not a valid override field.")
                        continue
                    if ov[key] is None:
                        continue
                    try:
                        dt = parse_utc_iso8601(ov[key], f"{ov_where}.{key}")
                        if not (semester_start <= dt <= semester_end):
                            errors.append(
                                f"{ov_where}.{key}: {ov[key]} falls outside the semester."
                            )
                    except MappingError as e:
                        errors.append(str(e))

    if errors:
        raise MappingError(
            f"{len(errors)} problem(s) found in the mapping file - fixing all of them "
            "before contacting Canvas:\n  - " + "\n  - ".join(errors)
        )


def confirm(prompt):
    """Ask a yes/no question on the console. Only an explicit 'y'/'yes' counts as approval."""
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def build_plan(course, items):
    """Fetch current values for every approved, non-manual-review item and
    build a diff plan. Canvas fetch failures fail only that one item.

    Returns (plan, skipped, failed):
      plan   - list of dicts with a live '_obj' (and '_override_objs') for
               execution, plus a JSON-safe view for the audit log/printing.
      skipped - manual_review and not-approved items (JSON-safe, no fetch).
      failed  - items whose Canvas fetch failed (JSON-safe).
    """
    plan, skipped, failed = [], [], []

    for item in items:
        item_type = item.get("type")

        if item_type == "manual_review":
            skipped.append({
                "type": "manual_review", "id": item.get("id"), "name": item.get("name"),
                "reason": item.get("reason"),
            })
            continue

        if not item.get("approved"):
            skipped.append({
                "type": item_type, "id": item.get("id"), "name": item.get("name"),
                "reason": "not marked approved in mapping file",
            })
            continue

        handler = TYPE_HANDLERS[item_type]
        try:
            obj = handler["fetch"](course, item["id"])
        except (CanvasException, Exception) as e:
            failed.append({
                "type": item_type, "id": item["id"], "name": item.get("name"),
                "stage": "fetch", "error": sanitize(e),
            })
            continue

        changes = {}
        for new_key, api_field in handler["fields"].items():
            if item.get(new_key) is not None:
                changes[api_field] = {"old": getattr(obj, api_field, None), "new": item[new_key]}

        existing_overrides = []
        override_objs = {}
        if handler["supports_overrides"]:
            try:
                existing_overrides = list(obj.get_overrides())
                override_objs = {o.id: o for o in existing_overrides}
            except (CanvasException, Exception) as e:
                skipped.append({
                    "type": item_type, "id": item["id"], "name": item.get("name"),
                    "reason": f"could not fetch existing overrides to check them: {sanitize(e)}",
                })

        mentioned_override_ids = set()
        override_rows = []
        for ov in item.get("overrides", []):
            mentioned_override_ids.add(ov["id"])
            if not ov.get("reviewed"):
                skipped.append({
                    "type": item_type, "id": item["id"], "name": item.get("name"),
                    "reason": f"override id {ov['id']} not marked reviewed - left untouched",
                })
                continue
            existing_ov = override_objs.get(ov["id"])
            ov_changes = {}
            for new_key, api_field in handler["fields"].items():
                if ov.get(new_key) is not None:
                    ov_changes[api_field] = {
                        "old": getattr(existing_ov, api_field, None) if existing_ov else None,
                        "new": ov[new_key],
                    }
            if ov_changes:
                override_rows.append({"id": ov["id"], "changes": ov_changes})

        untouched_override_ids = [o.id for o in existing_overrides if o.id not in mentioned_override_ids]

        if not changes and not override_rows:
            # Everything about this item was overrides that got skipped above.
            continue

        plan.append({
            "type": item_type, "id": item["id"], "name": item.get("name"),
            "note": item.get("note"),
            "changes": changes,
            "override_changes": override_rows,
            "untouched_override_ids": untouched_override_ids,
            "_obj": obj,
            "_override_objs": override_objs,
        })

    return plan, skipped, failed


def print_plan(plan, skipped, failed):
    print("\n" + "=" * 72)
    print("PROPOSED CHANGES")
    print("=" * 72)
    if not plan:
        print("  (none)")
    for row in plan:
        print(f"\n[{TYPE_HANDLERS[row['type']]['label']}] {row['name']} (id {row['id']})")
        for field, vals in row["changes"].items():
            print(f"    {field:12s}: {vals['old']}  ->  {vals['new']}")
        for ov in row["override_changes"]:
            print(f"    override id {ov['id']}:")
            for field, vals in ov["changes"].items():
                print(f"        {field:12s}: {vals['old']}  ->  {vals['new']}")
        if row["untouched_override_ids"]:
            print(f"    ({len(row['untouched_override_ids'])} override(s) "
                  f"{row['untouched_override_ids']} exist and will be left untouched)")
        if row["note"]:
            print(f"    note: {row['note']}")

    print("\n" + "-" * 72)
    print(f"SKIPPED ({len(skipped)}) - no Canvas call made for these")
    print("-" * 72)
    for row in skipped:
        print(f"  - {row.get('type')} id {row.get('id')} \"{row.get('name')}\": {row['reason']}")

    if failed:
        print("\n" + "-" * 72)
        print(f"FAILED TO FETCH ({len(failed)}) - could not read current values from Canvas")
        print("-" * 72)
        for row in failed:
            print(f"  - {row.get('type')} id {row.get('id')} \"{row.get('name')}\": {row['error']}")
    print()


def to_json_safe(plan):
    """Strip the live canvasapi object references out before logging/printing as JSON."""
    return [{k: v for k, v in row.items() if not k.startswith("_")} for row in plan]


def execute_plan(plan):
    """Apply every change in the plan to Canvas. Each item (and each of its
    overrides) is wrapped separately so one failure doesn't stop the rest."""
    completed, failed = [], []

    for row in plan:
        obj = row["_obj"]
        try:
            if row["changes"]:
                payload = {field: vals["new"] for field, vals in row["changes"].items()}
                TYPE_HANDLERS[row["type"]]["update"](obj, payload)
            completed.append({
                "type": row["type"], "id": row["id"], "name": row["name"],
                "changes": row["changes"],
            })
        except (CanvasException, Exception) as e:
            failed.append({
                "type": row["type"], "id": row["id"], "name": row["name"],
                "stage": "update", "error": sanitize(e),
            })

        for ov in row["override_changes"]:
            ov_obj = row["_override_objs"].get(ov["id"])
            if ov_obj is None:
                failed.append({
                    "type": row["type"], "id": row["id"], "override_id": ov["id"],
                    "stage": "override-update", "error": "override object not found at execution time",
                })
                continue
            try:
                payload = {field: vals["new"] for field, vals in ov["changes"].items()}
                ov_obj.edit(assignment_override=payload)
                completed.append({
                    "type": row["type"], "id": row["id"], "override_id": ov["id"],
                    "changes": ov["changes"],
                })
            except (CanvasException, Exception) as e:
                failed.append({
                    "type": row["type"], "id": row["id"], "override_id": ov["id"],
                    "stage": "override-update", "error": sanitize(e),
                })

    return completed, failed


def write_audit_log(course, args, mapping_path, semester_start, semester_end,
                     proposed, skipped, failed, completed, live_failed, mode):
    DOWNLOAD_ROOT.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = DOWNLOAD_ROOT / f"date_update_audit_{course.id}_{timestamp}.json"
    log = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "course_id": course.id,
        "course_name": course.name,
        "mapping_file": str(mapping_path),
        "semester_start": semester_start.isoformat(),
        "semester_end": semester_end.isoformat(),
        "proposed": proposed,
        "completed": completed,
        "skipped": skipped,
        "failed": failed + live_failed,
    }
    out_path.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print(f"\U0001f4dd Audit log written to {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Apply an approved due/unlock/lock date mapping to Canvas items."
    )
    parser.add_argument(
        "--mapping", default=str(DEFAULT_MAPPING_PATH),
        help=f"Path to the mapping JSON file (default: {DEFAULT_MAPPING_PATH})."
    )
    parser.add_argument("--semester-start", default=None, help="UTC ISO 8601, e.g. 2026-09-28T00:00:00Z")
    parser.add_argument("--semester-end", default=None, help="UTC ISO 8601, e.g. 2026-12-13T23:59:59Z")
    parser.add_argument(
        "--live", action="store_true",
        help="Actually write to Canvas after a final typed confirmation. Without this, "
             "the script only reads from Canvas and prints a diff - no changes are made."
    )
    args = parser.parse_args()

    if not all([CANVAS_URL, CANVAS_TOKEN, COURSE_ID_STR]):
        raise ValueError(
            "Missing required environment variables!\n"
            "Please ensure your .env file has:\n"
            "  - CANVAS_URL (e.g., https://your-institution.instructure.com)\n"
            "  - CANVAS_TOKEN (your API token from Canvas Settings)\n"
            "  - COURSE_ID (the course ID number, e.g., 123456)"
        )
    try:
        course_id = int(COURSE_ID_STR)
    except ValueError:
        raise ValueError(f"COURSE_ID must be a number, but got: {COURSE_ID_STR}")

    mapping_path = Path(args.mapping)
    mapping = load_mapping(mapping_path)
    semester_start, semester_end = resolve_semester_bounds(mapping, args)

    print(f"Semester window: {semester_start.isoformat()} - {semester_end.isoformat()}")
    print(f"Validating {len(mapping['items'])} mapping item(s) from {mapping_path}...")
    validate_items(mapping["items"], semester_start, semester_end)
    print("✅ Mapping file passed validation.\n")

    print(f"\U0001f517 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)
    course = canvas.get_course(course_id)
    print(f"\U0001f4da Course: \"{course.name}\" (id {course.id}, status: {course.workflow_state})")

    if not confirm("Is this the correct course to process?"):
        print("Aborted. Nothing was read or changed.")
        return

    plan, skipped, failed = build_plan(course, mapping["items"])
    print_plan(plan, skipped, failed)

    proposed_json = to_json_safe(plan)

    if not args.live:
        write_audit_log(
            course, args, mapping_path, semester_start, semester_end,
            proposed_json, skipped, failed, completed=[], live_failed=[], mode="dry_run",
        )
        print("Dry run complete. No changes were made. Re-run with --live to apply them.")
        return

    if not plan:
        print("Nothing approved and ready to apply. Nothing to do.")
        write_audit_log(
            course, args, mapping_path, semester_start, semester_end,
            proposed_json, skipped, failed, completed=[], live_failed=[], mode="live",
        )
        return

    print("\n" + "!" * 72)
    print(f"!  ABOUT TO WRITE {len(plan)} CHANGE(S) TO THE LIVE CANVAS COURSE")
    print(f"!  \"{course.name}\" (id {course.id})")
    print("!" * 72)
    typed = input(f"Type the course ID ({course.id}) to proceed, anything else to abort: ").strip()
    if typed != str(course.id):
        print("Aborted. Nothing was changed.")
        write_audit_log(
            course, args, mapping_path, semester_start, semester_end,
            proposed_json, skipped, failed, completed=[], live_failed=[], mode="aborted",
        )
        return

    completed, live_failed = execute_plan(plan)
    write_audit_log(
        course, args, mapping_path, semester_start, semester_end,
        proposed_json, skipped, failed, completed, live_failed, mode="live",
    )

    print(f"\n\U0001f389 Done. {len(completed)} change(s) applied, {len(live_failed)} failed.")
    if live_failed:
        print("Failures:")
        for row in live_failed:
            print(f"  - {row}")


if __name__ == "__main__":
    try:
        main()
    except MappingError as e:
        print(f"\n❌ Mapping file problem - nothing was changed:\n\n{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error occurred:")
        print(f"   {sanitize(e)}")
        print(f"\nCommon issues:")
        print(f"   - Invalid COURSE_ID: Check that the course ID exists")
        print(f"   - Invalid token: Your API token may have expired")
        print(f"   - Permission denied: Your account may not have access to this course")
        raise
