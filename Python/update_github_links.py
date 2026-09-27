"""
Update GitHub Pages Course Material Links in Canvas.

This script scans a Canvas course (default: 3926) for links to course materials
hosted on GitHub Pages, and updates them from CS123 to CS112:
  - Base domain: https://lcc-cit.github.io/CS123-CourseMaterials/
              -> https://lcc-cit.github.io/CS112-CourseMaterials/
  - File prefix: Files prefixed with 'CS123' are updated to 'CS112'
                 (e.g., CS123-Topic01-1-CourseIntro.html -> CS112-Topic01-1-CourseIntro.html,
                        CS123_GettingStartedGuide.html   -> CS112_GettingStartedGuide.html)

Scanned Canvas objects:
  1. Modules & Module Items (external URLs)
  2. Wiki Pages (body HTML)
  3. Assignments & New Quizzes (description HTML)
  4. Classic Quizzes (description HTML)
  5. Discussions & Announcements (message HTML)
  6. Course Syllabus (syllabus_body HTML)

Safety Conventions:
  - Dry run by default: Running without --apply shows every proposed change and
    writes an audit log to downloads/, making NO changes to Canvas.
  - Live execution: Requires --apply AND an interactive confirmation where the
    user must type the course ID.
  - Token safety: The Canvas API token is never printed or logged; all errors
    are sanitized.
  - Audit log: Full record of proposed and applied changes is saved to
    Python/downloads/update_links_<course_id>_<timestamp>.json.

Usage:
  python Python/update_github_links.py
      # Dry run for course 3926 (shows proposed changes, makes no API writes)

  python Python/update_github_links.py --apply
      # Live run for course 3926 (prompts for typed confirmation before writing)

  python Python/update_github_links.py --course-id 680
      # Dry run for sandbox course 680
"""
import os
import sys
import re
import json
import argparse
import posixpath
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
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
CONTENT_DIR = Path(__file__).parent / "content"
DOWNLOAD_ROOT = Path(__file__).parent / "downloads"

# Default configuration for course 3926 migration
DEFAULT_COURSE_ID = 3926
DEFAULT_OLD_DOMAIN = "https://lcc-cit.github.io/CS123-CourseMaterials/"
DEFAULT_NEW_DOMAIN = "https://lcc-cit.github.io/CS112-CourseMaterials/"
DEFAULT_OLD_PREFIX = "CS123"
DEFAULT_NEW_PREFIX = "CS112"

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


def transform_url(url, old_domain, new_domain, old_prefix, new_prefix):
    """
    Transform a URL from old_domain to new_domain, and rename file prefix
    if filename starts with old_prefix.

    Example:
      https://lcc-cit.github.io/CS123-CourseMaterials/LectureNotes/CS123-Topic01-1-CourseIntro.html
      -> https://lcc-cit.github.io/CS112-CourseMaterials/LectureNotes/CS112-Topic01-1-CourseIntro.html
    """
    # Normalize old_domain prefix to check (handle both http and https)
    old_base_stripped = re.sub(r"^https?://", "", old_domain).rstrip("/") + "/"
    url_stripped = re.sub(r"^https?://", "", url)

    if not url_stripped.startswith(old_base_stripped):
        return url

    rel_path_with_query = url_stripped[len(old_base_stripped):]
    parts = urlsplit(rel_path_with_query)
    path = parts.path

    dirname, filename = posixpath.split(path)
    if old_prefix and filename.startswith(old_prefix):
        new_filename = new_prefix + filename[len(old_prefix):]
    else:
        new_filename = filename

    new_path = posixpath.join(dirname, new_filename) if dirname else new_filename
    new_rel = urlunsplit(("", "", new_path, parts.query, parts.fragment))

    # Prepend new_domain
    base = new_domain.rstrip("/") + "/"
    return base + new_rel


def replace_urls_in_html(html_text, old_domain, new_domain, old_prefix, new_prefix):
    """
    Find and replace all matching URLs in an HTML string.
    Returns (new_html, replacements_list) where each entry in replacements_list
    is a tuple (old_url, new_url).
    """
    if not html_text:
        return html_text, []

    # Regex matching the domain with http or https up to quote/space/bracket
    domain_escaped = re.escape(re.sub(r"^https?://", "", old_domain).rstrip("/"))
    pattern = re.compile(rf"https?://{domain_escaped}/[^\s\"\'<>]+")

    replacements = []

    def _sub_callback(match):
        old_url = match.group(0)
        new_url = transform_url(old_url, old_domain, new_domain, old_prefix, new_prefix)
        if new_url != old_url:
            replacements.append((old_url, new_url))
        return new_url

    new_html = pattern.sub(_sub_callback, html_text)
    return new_html, replacements


def scan_course_items(course, old_domain, new_domain, old_prefix, new_prefix):
    """
    Scan all supported Canvas items for matching links.
    Returns a list of proposed change dicts.
    """
    items_to_update = []

    print("\n📋 Scanning course content for links...")

    # 1. Modules & Module Items (ExternalUrl)
    print("   🔍 Checking Modules and Module Items...")
    try:
        modules = course.get_modules()
        for module in modules:
            try:
                for item in module.get_module_items():
                    ext_url = getattr(item, "external_url", None)
                    if ext_url:
                        new_url = transform_url(ext_url, old_domain, new_domain, old_prefix, new_prefix)
                        if new_url != ext_url:
                            items_to_update.append({
                                "type": "module_item",
                                "id": item.id,
                                "module_id": module.id,
                                "module_name": module.name,
                                "title": item.title,
                                "old_url": ext_url,
                                "new_url": new_url,
                                "canvas_object": item,
                            })
            except Exception as e:
                print(f"      ⚠️  Could not read items for module {module.id}: {sanitize(e)}")
    except Exception as e:
        print(f"      ⚠️  Could not list modules: {sanitize(e)}")

    # 2. Wiki Pages (body)
    print("   🔍 Checking Pages...")
    try:
        pages = course.get_pages()
        for page_summary in pages:
            try:
                full_page = course.get_page(page_summary.url)
                body = getattr(full_page, "body", "") or ""
                new_body, replacements = replace_urls_in_html(
                    body, old_domain, new_domain, old_prefix, new_prefix
                )
                if replacements:
                    items_to_update.append({
                        "type": "page",
                        "id": getattr(full_page, "page_id", full_page.url),
                        "url": full_page.url,
                        "title": full_page.title,
                        "replacements": replacements,
                        "old_content": body,
                        "new_content": new_body,
                        "canvas_object": full_page,
                    })
            except Exception as e:
                print(f"      ⚠️  Could not read page {page_summary.url}: {sanitize(e)}")
    except Exception as e:
        print(f"      ⚠️  Could not list pages: {sanitize(e)}")

    # 3. Assignments (description) - covers both standard assignments and New Quizzes
    print("   🔍 Checking Assignments...")
    try:
        assignments = course.get_assignments()
        for assignment in assignments:
            try:
                desc = getattr(assignment, "description", "") or ""
                new_desc, replacements = replace_urls_in_html(
                    desc, old_domain, new_domain, old_prefix, new_prefix
                )
                if replacements:
                    items_to_update.append({
                        "type": "assignment",
                        "id": assignment.id,
                        "title": assignment.name,
                        "replacements": replacements,
                        "old_content": desc,
                        "new_content": new_desc,
                        "canvas_object": assignment,
                    })
            except Exception as e:
                print(f"      ⚠️  Could not read assignment {assignment.id}: {sanitize(e)}")
    except Exception as e:
        print(f"      ⚠️  Could not list assignments: {sanitize(e)}")

    # 4. Classic Quizzes (description)
    print("   🔍 Checking Classic Quizzes...")
    try:
        quizzes = course.get_quizzes()
        for quiz in quizzes:
            try:
                desc = getattr(quiz, "description", "") or ""
                new_desc, replacements = replace_urls_in_html(
                    desc, old_domain, new_domain, old_prefix, new_prefix
                )
                if replacements:
                    items_to_update.append({
                        "type": "quiz",
                        "id": quiz.id,
                        "title": quiz.title,
                        "replacements": replacements,
                        "old_content": desc,
                        "new_content": new_desc,
                        "canvas_object": quiz,
                    })
            except Exception as e:
                print(f"      ⚠️  Could not read quiz {quiz.id}: {sanitize(e)}")
    except Exception as e:
        print(f"      ⚠️  Could not list quizzes: {sanitize(e)}")

    # 5. Discussions & Announcements (message)
    print("   🔍 Checking Discussions and Announcements...")
    try:
        discussions = course.get_discussion_topics()
        for topic in discussions:
            try:
                msg = getattr(topic, "message", "") or ""
                new_msg, replacements = replace_urls_in_html(
                    msg, old_domain, new_domain, old_prefix, new_prefix
                )
                if replacements:
                    is_announcement = getattr(topic, "is_announcement", False)
                    items_to_update.append({
                        "type": "announcement" if is_announcement else "discussion",
                        "id": topic.id,
                        "title": topic.title,
                        "replacements": replacements,
                        "old_content": msg,
                        "new_content": new_msg,
                        "canvas_object": topic,
                    })
            except Exception as e:
                print(f"      ⚠️  Could not read discussion {topic.id}: {sanitize(e)}")
    except Exception as e:
        print(f"      ⚠️  Could not list discussions: {sanitize(e)}")

    # 6. Syllabus
    print("   🔍 Checking Course Syllabus...")
    try:
        syl = getattr(course, "syllabus_body", "") or ""
        new_syl, replacements = replace_urls_in_html(
            syl, old_domain, new_domain, old_prefix, new_prefix
        )
        if replacements:
            items_to_update.append({
                "type": "syllabus",
                "id": course.id,
                "title": f"Syllabus for course {course.id}",
                "replacements": replacements,
                "old_content": syl,
                "new_content": new_syl,
                "canvas_object": course,
            })
    except Exception as e:
        print(f"      ⚠️  Could not check syllabus: {sanitize(e)}")

    return items_to_update


def print_plan(items_to_update):
    """Display proposed changes in a clear, formatted summary."""
    if not items_to_update:
        print("\n✅ No matching links found! Everything is already up to date.")
        return

    print("\n" + "=" * 78)
    print(f" PROPOSED CHANGES: {len(items_to_update)} item(s) found with links to update")
    print("=" * 78)

    for idx, item in enumerate(items_to_update, 1):
        item_type = item["type"]
        item_id = item["id"]
        title = item["title"]

        if item_type == "module_item":
            mod_name = item["module_name"]
            print(f"\n[{idx}] 🔗 Module Item (ID: {item_id}) in '{mod_name}'")
            print(f"    Title:    {title}")
            print(f"    Old URL:  {item['old_url']}")
            print(f"    New URL:  {item['new_url']}")
        else:
            rep_count = len(item["replacements"])
            print(f"\n[{idx}] 📝 {item_type.capitalize()} (ID: {item_id}): \"{title}\" ({rep_count} link(s))")
            for old_u, new_u in item["replacements"]:
                print(f"    Old URL:  {old_u}")
                print(f"    New URL:  {new_u}")

    print("\n" + "=" * 78)


def execute_updates(course, items_to_update):
    """
    Apply updates live to Canvas.
    Returns (completed_list, failed_list).
    """
    completed = []
    failed = []

    print("\n🚀 Applying changes to Canvas...")

    for idx, item in enumerate(items_to_update, 1):
        item_type = item["type"]
        item_id = item["id"]
        title = item["title"]
        obj = item["canvas_object"]

        prefix = f"[{idx}/{len(items_to_update)}]"
        try:
            if item_type == "module_item":
                obj.edit(module_item={"external_url": item["new_url"]})
                print(f"  {prefix} ✅ Updated Module Item {item_id}: {title}")
                completed.append({
                    "type": item_type,
                    "id": item_id,
                    "title": title,
                    "old_url": item["old_url"],
                    "new_url": item["new_url"],
                })

            elif item_type == "page":
                obj.edit(wiki_page={"body": item["new_content"]})
                print(f"  {prefix} ✅ Updated Page '{item['url']}': {title}")
                completed.append({
                    "type": item_type,
                    "id": item_id,
                    "url": item["url"],
                    "title": title,
                    "replacements": item["replacements"],
                })

            elif item_type == "assignment":
                obj.edit(assignment={"description": item["new_content"]})
                print(f"  {prefix} ✅ Updated Assignment {item_id}: {title}")
                completed.append({
                    "type": item_type,
                    "id": item_id,
                    "title": title,
                    "replacements": item["replacements"],
                })

            elif item_type == "quiz":
                obj.edit(quiz={"description": item["new_content"]})
                print(f"  {prefix} ✅ Updated Quiz {item_id}: {title}")
                completed.append({
                    "type": item_type,
                    "id": item_id,
                    "title": title,
                    "replacements": item["replacements"],
                })

            elif item_type in ("discussion", "announcement"):
                obj.update(message=item["new_content"])
                print(f"  {prefix} ✅ Updated {item_type.capitalize()} {item_id}: {title}")
                completed.append({
                    "type": item_type,
                    "id": item_id,
                    "title": title,
                    "replacements": item["replacements"],
                })

            elif item_type == "syllabus":
                course.update(course={"syllabus_body": item["new_content"]})
                print(f"  {prefix} ✅ Updated Syllabus for course {item_id}")
                completed.append({
                    "type": item_type,
                    "id": item_id,
                    "title": title,
                    "replacements": item["replacements"],
                })

        except Exception as e:
            err_msg = sanitize(str(e))
            print(f"  {prefix} ❌ Failed to update {item_type} {item_id} ({title}): {err_msg}")
            failed.append({
                "type": item_type,
                "id": item_id,
                "title": title,
                "error": err_msg,
            })

    return completed, failed


def write_audit_log(course_id, course_name, mode, plan, completed, failed, old_domain, new_domain, old_prefix, new_prefix):
    """Save an audit log JSON file to downloads/."""
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = DOWNLOAD_ROOT / f"update_links_{course_id}_{timestamp}.json"

    # Make plan JSON serializable (strip canvas_object)
    serializable_plan = []
    for item in plan:
        entry = {k: v for k, v in item.items() if k not in ("canvas_object", "old_content", "new_content")}
        serializable_plan.append(entry)

    log_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "course_id": course_id,
        "course_name": course_name,
        "migration_settings": {
            "old_domain": old_domain,
            "new_domain": new_domain,
            "old_prefix": old_prefix,
            "new_prefix": new_prefix,
        },
        "proposed_total": len(plan),
        "proposed": serializable_plan,
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
        description="Update GitHub Pages course material links in Canvas from CS123 to CS112."
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
        "--old-domain",
        default=DEFAULT_OLD_DOMAIN,
        help=f"Old base URL (default: {DEFAULT_OLD_DOMAIN})",
    )
    parser.add_argument(
        "--new-domain",
        default=DEFAULT_NEW_DOMAIN,
        help=f"New base URL (default: {DEFAULT_NEW_DOMAIN})",
    )
    parser.add_argument(
        "--old-prefix",
        default=DEFAULT_OLD_PREFIX,
        help=f"Old filename prefix to replace (default: {DEFAULT_OLD_PREFIX})",
    )
    parser.add_argument(
        "--new-prefix",
        default=DEFAULT_NEW_PREFIX,
        help=f"New filename prefix to use (default: {DEFAULT_NEW_PREFIX})",
    )

    args = parser.parse_args()

    # Validate required credentials
    if not CANVAS_URL or not CANVAS_TOKEN:
        print("❌ Error: Missing CANVAS_URL or CANVAS_TOKEN in .env file.")
        sys.exit(1)

    course_id = args.course_id
    mode = "live" if args.apply else "dry_run"

    print("\n" + "=" * 78)
    print("  CANVAS GITHUB PAGES LINK UPDATER")
    print(f"  Mode:        {'LIVE WRITE (--apply)' if args.apply else 'DRY RUN (no changes)'}")
    print(f"  Course ID:   {course_id}")
    print(f"  Old Domain:  {args.old_domain}")
    print(f"  New Domain:  {args.new_domain}")
    print(f"  Old Prefix:  {args.old_prefix}")
    print(f"  New Prefix:  {args.new_prefix}")
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

    # Interactive confirmation of course
    if not confirm("Is this the correct course to process?"):
        print("Aborted. Nothing was read or changed.")
        return

    # Scan course content
    plan = scan_course_items(
        course,
        args.old_domain,
        args.new_domain,
        args.old_prefix,
        args.new_prefix,
    )

    # Display proposed changes
    print_plan(plan)

    if not plan:
        write_audit_log(
            course_id, course_name, mode, plan, [], [],
            args.old_domain, args.new_domain, args.old_prefix, args.new_prefix
        )
        return

    # Dry run stops here
    if not args.apply:
        write_audit_log(
            course_id, course_name, "dry_run", plan, [], [],
            args.old_domain, args.new_domain, args.old_prefix, args.new_prefix
        )
        print("\n💡 Dry run complete. No changes were made to Canvas.")
        print(f"   To apply these {len(plan)} change(s) live, re-run with:\n")
        print(f"   python Python/update_github_links.py --apply --course-id {course_id}\n")
        return

    # Live run requires typed confirmation
    print("\n" + "!" * 78)
    print(f"!  WARNING: ABOUT TO WRITE {len(plan)} CHANGE(S) TO LIVE CANVAS COURSE")
    print(f"!  \"{course_name}\" (id {course_id})")
    print("!" * 78)
    typed = input(f"Type the course ID ({course_id}) to proceed, anything else to abort: ").strip()
    if typed != str(course_id):
        print("Aborted. Nothing was changed.")
        write_audit_log(
            course_id, course_name, "aborted", plan, [], [],
            args.old_domain, args.new_domain, args.old_prefix, args.new_prefix
        )
        return

    completed, failed = execute_updates(course, plan)
    write_audit_log(
        course_id, course_name, "live", plan, completed, failed,
        args.old_domain, args.new_domain, args.old_prefix, args.new_prefix
    )

    print(f"\n🎉 Done. {len(completed)} change(s) applied, {len(failed)} failed.")
    if failed:
        print("⚠️  Failures:")
        for f in failed:
            print(f"  - {f['type']} {f['id']} ({f['title']}): {f['error']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Error occurred: {sanitize(e)}")
        sys.exit(1)
