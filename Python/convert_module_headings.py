"""
Convert Empty Heading Pages in Canvas Modules to Native Text Headings (SubHeaders).

In courses imported from other LMS platforms (like Moodle), section headers
often get imported as empty wiki Pages with ALL CAPS titles (e.g. 'READING',
'LECTURE NOTES', 'ACTIVITIES-5') containing no real body content or only a single
heading tag (e.g. <h4>Reading</h4>).

This script:
  1. Scans modules in the course (default: 3926) for module items of type 'Page'.
  2. Detects pages that are being used purely as section headings:
     - The page title is ALL CAPS (ignoring numbers, hyphens, and spaces).
     - The page body has no real instructional content (is empty, or contains
       only Canvas theme scripts and a heading tag matching the title). Pages
       with paragraphs, lists, links, or media are safely skipped.
  3. Replaces each detected module item with a native Canvas text heading ('SubHeader')
     at the exact same module position and indentation level.
  4. (Optional) With --delete-pages, also deletes the empty backing Page object from
     the course's Pages list. (Default: leaves backing pages intact).

Safety Conventions:
  - Dry run by default: Running without --apply shows every detected heading page,
    its proposed SubHeader title, and its module position without making any changes.
  - Live execution: Requires --apply AND an interactive confirmation where the user
    must type the course ID.
  - Token safety: The Canvas API token is never printed or logged; all errors are sanitized.
  - Audit log: Full record of proposed and applied changes is saved to
    Python/downloads/convert_headings_<course_id>_<timestamp>.json.

Usage:
  python Python/convert_module_headings.py
      # Dry run for course 3926 (shows proposed conversions, makes no changes)

  python Python/convert_module_headings.py --apply
      # Live run for course 3926 (prompts for typed confirmation before writing)

  python Python/convert_module_headings.py --apply --delete-pages
      # Live run, and also delete the empty backing Page objects from Canvas
"""
import os
import sys
import re
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from canvasapi import Canvas
from canvasapi.exceptions import CanvasException

# On Windows consoles, force UTF-8 output to prevent crashes on status emoji
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Paths
DOWNLOAD_ROOT = Path(__file__).parent / "downloads"
DEFAULT_COURSE_ID = 3926

# Load environment variables
load_dotenv()
CANVAS_URL = os.getenv("CANVAS_URL")
CANVAS_TOKEN = os.getenv("CANVAS_TOKEN")


def sanitize(text):
    """Strip the Canvas API token from any string before it is printed or logged."""
    text = str(text)
    if CANVAS_TOKEN:
        text = text.replace(CANVAS_TOKEN, "***REDACTED***")
    return text


def confirm(prompt):
    """Require explicit y/yes confirmation."""
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return answer in ("y", "yes")


def is_all_caps(title):
    """Check if the title contains letters and all alphabetic characters are uppercase."""
    letters = [c for c in title if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def clean_title_fallback(page_title):
    """Generate a clean title-cased heading name by stripping trailing -N from imported titles."""
    # E.g., 'READING-5' -> 'Reading', 'LECTURE NOTES-2' -> 'Lecture Notes'
    base = re.sub(r'-\d+$', '', page_title).strip()
    return base.title()


def detect_empty_heading_page(item_title, page_body):
    """
    Determine if a page module item is an empty page serving as a section heading.

    Criteria:
      1. Title has letters and all letters are uppercase (e.g. 'READING', 'ACTIVITIES-4').
      2. Body has no real instructional content:
         - Body is empty, or
         - Body contains only script tags and/or a heading tag (h1-h6) with short text (<= 60 chars),
           and contains no paragraphs, links, lists, tables, images, or iframes.

    Returns:
      (is_heading_page: bool, proposed_heading_title: str or None)
    """
    if not is_all_caps(item_title):
        return False, None

    default_title = clean_title_fallback(item_title)

    # Strip script tags (such as Canvas theme scripts)
    body_clean = re.sub(r'<script.*?</script>', '', page_body or '', flags=re.DOTALL).strip()
    # Normalize HTML entities and line breaks
    body_clean = re.sub(r'&nbsp;|<br\s*/?>', ' ', body_clean).strip()

    # Case 1: Body is completely empty
    if not body_clean:
        return True, default_title

    # If the body contains content tags (paragraphs, links, lists, tables, media), it's real content!
    forbidden_pattern = re.compile(
        r'<(p|a|ul|ol|li|table|tr|td|th|img|iframe|blockquote|pre|code)\b',
        flags=re.IGNORECASE,
    )
    if forbidden_pattern.search(body_clean):
        return False, None

    # Extract text content
    text_only = re.sub(r'<[^>]+>', ' ', body_clean).strip()
    text_only = ' '.join(text_only.split())

    # Case 2: No visible text left
    if not text_only:
        return True, default_title

    # Case 3: Short heading tag (<= 60 chars) without content tags
    if len(text_only) <= 60:
        remaining_tags = set(re.findall(r'<([a-zA-Z0-9]+)', body_clean))
        allowed_tags = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'span', 'div', 'strong', 'em', 'b', 'i'}
        if remaining_tags.issubset(allowed_tags):
            # If the tag text itself doesn't end with Canvas deduplication numbers (-N), use it;
            # otherwise fall back to the cleaned title
            if not re.search(r'-\d+$', text_only):
                return True, text_only
            return True, default_title

    return False, None


def scan_modules_for_heading_pages(course):
    """
    Scan all modules in the course for Page items that should be SubHeaders.
    Returns (plan, skipped_all_caps).
    """
    plan = []
    skipped_all_caps = []

    print("\n📋 Scanning modules for empty heading pages...")

    modules = list(course.get_modules())
    for module in modules:
        try:
            items = list(module.get_module_items())
            for item in items:
                if item.type != "Page":
                    continue

                # Check if title is ALL CAPS
                if not is_all_caps(item.title):
                    continue

                # Fetch backing page
                page_url = getattr(item, "page_url", None)
                if not page_url:
                    continue

                try:
                    page = course.get_page(page_url)
                    body = getattr(page, "body", "") or ""
                except Exception as e:
                    print(f"   ⚠️  Could not read page '{page_url}' in module '{module.name}': {sanitize(e)}")
                    continue

                is_heading, proposed_title = detect_empty_heading_page(item.title, body)

                if is_heading:
                    plan.append({
                        "module_id": module.id,
                        "module_name": module.name,
                        "module_obj": module,
                        "item_id": item.id,
                        "item_obj": item,
                        "old_title": item.title,
                        "new_title": proposed_title,
                        "position": item.position,
                        "indent": getattr(item, "indent", 0),
                        "page_url": page_url,
                        "page_obj": page,
                    })
                else:
                    skipped_all_caps.append({
                        "module_name": module.name,
                        "item_id": item.id,
                        "title": item.title,
                        "page_url": page_url,
                        "reason": "Contains non-heading body content (links, paragraphs, or lists)",
                    })
        except Exception as e:
            print(f"   ⚠️  Could not read items for module '{module.name}' ({module.id}): {sanitize(e)}")

    return plan, skipped_all_caps


def print_plan(plan, skipped_all_caps):
    """Display the proposed conversion plan."""
    if not plan:
        print("\n✅ No empty heading pages found. Modules are already using text headings!")
        return

    print("\n" + "=" * 78)
    print(f" PROPOSED CONVERSIONS: {len(plan)} empty page(s) to convert to SubHeaders")
    print("=" * 78)

    current_mod = None
    for idx, item in enumerate(plan, 1):
        if item["module_name"] != current_mod:
            current_mod = item["module_name"]
            print(f"\n📚 Module: {current_mod} (ID: {item['module_id']})")

        print(
            f"   [{idx:2d}] Item {item['item_id']} (pos {item['position']}, indent {item['indent']}):\n"
            f"        Page:       \"{item['old_title']}\" (url: {item['page_url']})\n"
            f"        -> SubHeader: \"{item['new_title']}\""
        )

    if skipped_all_caps:
        print("\n" + "-" * 78)
        print(f" ℹ️  SKIPPED ALL-CAPS PAGES WITH CONTENT: {len(skipped_all_caps)} item(s)")
        print("    (Preserved as real Pages because they contain paragraphs, links, or media)")
        print("-" * 78)
        for s in skipped_all_caps:
            print(f"   - [{s['item_id']}] \"{s['title']}\" in '{s['module_name']}'")

    print("\n" + "=" * 78)


def execute_plan(plan, delete_pages=False):
    """
    Apply conversions live in Canvas:
      1. Create a new SubHeader module item at the target position.
      2. Delete the old Page module item.
      3. (Optional) Delete the backing Page object if delete_pages is True.
    """
    completed = []
    failed = []

    print("\n🚀 Converting module items to SubHeaders in Canvas...")

    for idx, item in enumerate(plan, 1):
        prefix = f"[{idx}/{len(plan)}]"
        module = item["module_obj"]
        old_item = item["item_obj"]
        page = item["page_obj"]

        try:
            # 1. Create the new SubHeader item at the exact position & indent
            new_sub = module.create_module_item(module_item={
                "type": "SubHeader",
                "title": item["new_title"],
                "position": item["position"],
                "indent": item["indent"],
            })

            # 2. Delete the old Page module item
            old_item.delete()

            # 3. Optionally delete the backing Page object
            page_deleted = False
            if delete_pages:
                try:
                    page.delete()
                    page_deleted = True
                except Exception as pe:
                    print(f"      ⚠️  Could not delete backing page '{item['page_url']}': {sanitize(pe)}")

            print(
                f"  {prefix} ✅ Replaced Page {item['item_id']} (\"{item['old_title']}\") "
                f"with SubHeader {new_sub.id} (\"{item['new_title']}\")"
                + (" [backing page deleted]" if page_deleted else "")
            )

            completed.append({
                "module_id": item["module_id"],
                "module_name": item["module_name"],
                "old_item_id": item["item_id"],
                "new_item_id": new_sub.id,
                "old_title": item["old_title"],
                "new_title": item["new_title"],
                "position": item["position"],
                "indent": item["indent"],
                "page_url": item["page_url"],
                "backing_page_deleted": page_deleted,
            })

        except Exception as e:
            err = sanitize(str(e))
            print(f"  {prefix} ❌ Failed to convert item {item['item_id']} (\"{item['old_title']}\"): {err}")
            failed.append({
                "item_id": item["item_id"],
                "title": item["old_title"],
                "error": err,
            })

    return completed, failed


def write_audit_log(course_id, course_name, mode, plan, completed, failed, skipped):
    """Write disposable audit log JSON to downloads/."""
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = DOWNLOAD_ROOT / f"convert_headings_{course_id}_{timestamp}.json"

    # Make plan serializable (exclude Canvas objects)
    serializable_plan = []
    for item in plan:
        entry = {k: v for k, v in item.items() if not k.endswith("_obj")}
        serializable_plan.append(entry)

    log_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "course_id": course_id,
        "course_name": course_name,
        "proposed_total": len(plan),
        "proposed": serializable_plan,
        "skipped_all_caps_pages": skipped,
        "completed": completed,
        "failed": failed,
    }

    try:
        log_file.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
        print(f"\n📁 Audit log written to: {log_file}")
    except Exception as e:
        print(f"\n⚠️  Could not write audit log: {sanitize(e)}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert empty heading pages in Canvas modules to native text headings (SubHeaders)."
    )
    parser.add_argument(
        "--course-id",
        type=int,
        default=DEFAULT_COURSE_ID,
        help=f"Canvas Course ID to target (default: {DEFAULT_COURSE_ID})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to Canvas live (default: dry run / preview only)",
    )
    parser.add_argument(
        "--delete-pages",
        action="store_true",
        help="Also delete the unlinked empty Page objects from Canvas (default: keep pages in Canvas)",
    )

    args = parser.parse_args()

    # Validate Canvas credentials
    if not CANVAS_URL or not CANVAS_TOKEN:
        print("❌ Error: Missing CANVAS_URL or CANVAS_TOKEN in .env file.")
        sys.exit(1)

    course_id = args.course_id
    mode = "live" if args.apply else "dry_run"

    print("\n" + "=" * 78)
    print("  CANVAS MODULE HEADING CONVERTER")
    print(f"  Mode:         {'LIVE WRITE (--apply)' if args.apply else 'DRY RUN (no changes)'}")
    print(f"  Course ID:    {course_id}")
    print(f"  Delete Pages: {'Yes (--delete-pages)' if args.delete_pages else 'No (pages preserved)'}")
    print("=" * 78)

    print(f"\n🔗 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)

    try:
        course = canvas.get_course(course_id)
        course_name = getattr(course, "name", f"Course {course_id}")
        workflow_state = getattr(course, "workflow_state", "unknown")
        print(f"📚 Course: \"{course_name}\" (id {course.id}, status: {workflow_state})")
    except CanvasException as e:
        print(f"❌ Could not access course {course_id}: {sanitize(e)}")
        sys.exit(1)

    # Interactive confirmation
    if not confirm("Is this the correct course to process?"):
        print("Aborted. Nothing was read or changed.")
        return

    # Scan modules
    plan, skipped = scan_modules_for_heading_pages(course)

    # Print plan
    print_plan(plan, skipped)

    if not plan:
        write_audit_log(course_id, course_name, mode, plan, [], [], skipped)
        return

    # Dry run stops here
    if not args.apply:
        write_audit_log(course_id, course_name, "dry_run", plan, [], [], skipped)
        print("\n💡 Dry run complete. No changes were made to Canvas.")
        print(f"   To convert these {len(plan)} item(s) live, re-run with:\n")
        print(f"   uv run python Python/convert_module_headings.py --apply --course-id {course_id}\n")
        return

    # Live run requires typed confirmation
    print("\n" + "!" * 78)
    print(f"!  WARNING: ABOUT TO CONVERT {len(plan)} ITEM(S) IN LIVE CANVAS COURSE")
    print(f"!  \"{course_name}\" (id {course_id})")
    if args.delete_pages:
        print("!  AND DELETE THEIR UNLINKED EMPTY BACKING PAGES FROM CANVAS")
    print("!" * 78)
    typed = input(f"Type the course ID ({course_id}) to proceed, anything else to abort: ").strip()
    if typed != str(course_id):
        print("Aborted. Nothing was changed.")
        write_audit_log(course_id, course_name, "aborted", plan, [], [], skipped)
        return

    completed, failed = execute_plan(plan, delete_pages=args.delete_pages)
    write_audit_log(course_id, course_name, "live", plan, completed, failed, skipped)

    print(f"\n🎉 Done. {len(completed)} heading(s) converted, {len(failed)} failed.")
    if failed:
        print("⚠️  Failures:")
        for f in failed:
            print(f"  - [{f['item_id']}] \"{f['title']}\": {f['error']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error occurred: {sanitize(e)}")
        sys.exit(1)
