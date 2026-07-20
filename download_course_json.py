"""
Canvas Course Downloader - Export course contents to a JSON file

This script connects to Canvas (same setup as canvas_template.py) and downloads
the contents of the course into a single JSON file:
1. Course info and syllabus
2. Modules and their items
3. Pages (including page body HTML)
4. Assignments
5. Discussions and announcements
6. Quizzes
7. Files (metadata only, not the file contents themselves)

Output: course_<COURSE_ID>_<timestamp>.json in the current directory.
"""
import os
import json
from datetime import datetime
from dotenv import load_dotenv
from canvasapi import Canvas

# Load environment variables from .env file
# This keeps credentials out of your code
load_dotenv()

# Retrieve Canvas API credentials from environment variables
CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')

# Validate that all required configuration variables are set
if not all([CANVAS_URL, CANVAS_TOKEN, COURSE_ID_STR]):
    raise ValueError(
        "Missing required environment variables!\n"
        "Please ensure your .env file has:\n"
        "  - CANVAS_URL (e.g., https://your-institution.instructure.com)\n"
        "  - CANVAS_TOKEN (your API token from Canvas Settings)\n"
        "  - COURSE_ID (the course ID number, e.g., 123456)"
    )

# Convert COURSE_ID to integer (after validation)
try:
    COURSE_ID = int(COURSE_ID_STR)
except ValueError:
    raise ValueError(
        f"COURSE_ID must be a number, but got: {COURSE_ID_STR}\n"
        "Please check your .env file and use only digits for COURSE_ID"
    )


def safe_getattr(obj, name, default=None):
    """Get an attribute from a Canvas object, returning a default if missing."""
    return getattr(obj, name, default)


def download_section(label, fetch_func):
    """
    Run a fetch function for one section of the course.
    If it fails (e.g., feature disabled or no permission), report the error
    and keep going instead of stopping the whole export.
    """
    print(f"   ⬇️  Downloading {label}...")
    try:
        result = fetch_func()
        count = len(result) if isinstance(result, list) else 1
        print(f"      ✅ {label}: {count} item(s)")
        return result
    except Exception as e:
        print(f"      ⚠️  Skipping {label}: {e}")
        return {"error": str(e)}


def get_course_info(course):
    """Basic course properties and the syllabus."""
    return {
        "id": course.id,
        "name": course.name,
        "course_code": safe_getattr(course, "course_code"),
        "start_at": safe_getattr(course, "start_at"),
        "end_at": safe_getattr(course, "end_at"),
        "workflow_state": safe_getattr(course, "workflow_state"),
        "syllabus_body": safe_getattr(course, "syllabus_body"),
    }


def get_modules(course):
    """Modules and the items inside each module."""
    modules = []
    for module in course.get_modules():
        items = []
        for item in module.get_module_items():
            items.append({
                "id": item.id,
                "title": safe_getattr(item, "title"),
                "type": safe_getattr(item, "type"),
                "position": safe_getattr(item, "position"),
                "content_id": safe_getattr(item, "content_id"),
                "page_url": safe_getattr(item, "page_url"),
                "external_url": safe_getattr(item, "external_url"),
                "html_url": safe_getattr(item, "html_url"),
                "published": safe_getattr(item, "published"),
            })
        modules.append({
            "id": module.id,
            "name": module.name,
            "position": safe_getattr(module, "position"),
            "published": safe_getattr(module, "published"),
            "items": items,
        })
    return modules


def get_pages(course):
    """Wiki pages, including the full body HTML of each page."""
    pages = []
    for page in course.get_pages():
        # get_pages() returns summaries; fetch the full page to get the body
        full_page = course.get_page(page.url)
        pages.append({
            "page_id": safe_getattr(full_page, "page_id"),
            "url": safe_getattr(full_page, "url"),
            "title": safe_getattr(full_page, "title"),
            "body": safe_getattr(full_page, "body"),
            "published": safe_getattr(full_page, "published"),
            "front_page": safe_getattr(full_page, "front_page"),
            "updated_at": safe_getattr(full_page, "updated_at"),
        })
    return pages


def get_assignments(course):
    """Assignments with their descriptions, due dates, and points."""
    assignments = []
    for assignment in course.get_assignments():
        assignments.append({
            "id": assignment.id,
            "name": assignment.name,
            "description": safe_getattr(assignment, "description"),
            "due_at": safe_getattr(assignment, "due_at"),
            "points_possible": safe_getattr(assignment, "points_possible"),
            "submission_types": safe_getattr(assignment, "submission_types"),
            "published": safe_getattr(assignment, "published"),
            "html_url": safe_getattr(assignment, "html_url"),
        })
    return assignments


def get_discussions(course):
    """Discussion topics and announcements (announcements are a type of discussion)."""
    discussions = []
    for topic in course.get_discussion_topics():
        discussions.append({
            "id": topic.id,
            "title": safe_getattr(topic, "title"),
            "message": safe_getattr(topic, "message"),
            "is_announcement": safe_getattr(topic, "is_announcement", False),
            "posted_at": safe_getattr(topic, "posted_at"),
            "published": safe_getattr(topic, "published"),
            "html_url": safe_getattr(topic, "html_url"),
        })
    return discussions


def get_quizzes(course):
    """Quizzes with their descriptions and settings."""
    quizzes = []
    for quiz in course.get_quizzes():
        quizzes.append({
            "id": quiz.id,
            "title": safe_getattr(quiz, "title"),
            "description": safe_getattr(quiz, "description"),
            "quiz_type": safe_getattr(quiz, "quiz_type"),
            "due_at": safe_getattr(quiz, "due_at"),
            "points_possible": safe_getattr(quiz, "points_possible"),
            "question_count": safe_getattr(quiz, "question_count"),
            "published": safe_getattr(quiz, "published"),
            "html_url": safe_getattr(quiz, "html_url"),
        })
    return quizzes


def get_files(course):
    """File metadata (name, size, URL). Does not download the actual files."""
    files = []
    for f in course.get_files():
        files.append({
            "id": f.id,
            "display_name": safe_getattr(f, "display_name"),
            "filename": safe_getattr(f, "filename"),
            "content_type": safe_getattr(f, "content-type") or safe_getattr(f, "content_type"),
            "size": safe_getattr(f, "size"),
            "url": safe_getattr(f, "url"),
            "updated_at": safe_getattr(f, "updated_at"),
        })
    return files


def main():
    # Initialize Canvas API connection
    print(f"\n🔗 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)

    # Retrieve the course object from Canvas
    # include syllabus_body so the syllabus HTML is part of the export
    print(f"📚 Fetching course {COURSE_ID}...")
    course = canvas.get_course(COURSE_ID, include=["syllabus_body"])
    print(f"✅ Connected to course: {course.name}\n")

    # Download each section of the course
    course_data = {
        "exported_at": datetime.now().isoformat(),
        "canvas_url": CANVAS_URL,
        "course": download_section("course info", lambda: get_course_info(course)),
        "modules": download_section("modules", lambda: get_modules(course)),
        "pages": download_section("pages", lambda: get_pages(course)),
        "assignments": download_section("assignments", lambda: get_assignments(course)),
        "discussions": download_section("discussions & announcements", lambda: get_discussions(course)),
        "quizzes": download_section("quizzes", lambda: get_quizzes(course)),
        "files": download_section("files (metadata)", lambda: get_files(course)),
    }

    # Write everything to a JSON file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"course_{COURSE_ID}_{timestamp}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(course_data, f, indent=2, ensure_ascii=False)

    print(f"\n🎉 Done! Course contents saved to: {output_file}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error occurred:")
        print(f"   {e}")
        print(f"\nCommon issues:")
        print(f"   - Invalid COURSE_ID: Check that the course ID exists")
        print(f"   - Invalid token: Your API token may have expired")
        print(f"   - Permission denied: Your account may not have access to this course")
        raise
