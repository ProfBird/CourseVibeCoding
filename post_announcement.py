"""
Canvas Announcement Poster - Post the welcome announcement to Canvas

This script connects to Canvas (same setup as canvas_template.py) and posts
the announcement stored in content/welcome_announcement.md.

  Dry run (default):
    Prints the announcement exactly as it would be sent. Makes NO connection
    to Canvas and NO changes whatsoever.

  Post mode (--post):
    Connects to Canvas, shows the same preview, then asks in the console
    whether to:
      [Y]es   - post it, published and visible to students immediately
      [D]raft - post it hidden from students (Canvas announcements can't be
                true unpublished drafts, so this uses delayed_post_at set
                far in the future; you can still open and review it, and
                make it visible later by editing that date in Canvas)
      [N]o    - abort, nothing is posted

Usage:
    python post_announcement.py          # dry run - preview only, no API calls
    python post_announcement.py --post   # connect, then choose Yes/Draft/No
"""
import os
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import markdown
from dotenv import load_dotenv
from canvasapi import Canvas

# Load environment variables from .env file
# This keeps credentials out of your code
load_dotenv()

# Retrieve Canvas API credentials from environment variables
CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')

# The announcement content lives here as Markdown, with the title as the
# first line (a level-1 heading) and the rest of the file as the body.
ANNOUNCEMENT_PATH = Path(__file__).parent / "content" / "welcome_announcement.md"

# How long a "Draft" announcement stays hidden from students, via
# delayed_post_at, until someone edits that date in Canvas.
DRAFT_HOLD_DAYS = 365


def load_announcement():
    """Read the announcement Markdown file and split it into title + HTML body."""
    text = ANNOUNCEMENT_PATH.read_text(encoding="utf-8").strip()

    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise ValueError(
            f"{ANNOUNCEMENT_PATH} must start with a level-1 heading (e.g. '# Welcome to...') "
            "to use as the announcement title."
        )
    title = lines[0][2:].strip()
    body_markdown = "\n".join(lines[1:]).strip()
    body_html = markdown.markdown(body_markdown)
    return title, body_html


def confirm(prompt):
    """Ask a yes/no question on the console. Only an explicit 'y'/'yes' counts as approval."""
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def ask_post_choice():
    """Ask whether to post live, as a hidden draft, or not at all. Returns 'yes', 'draft', or 'no'."""
    while True:
        answer = input("Post this announcement? [Y]es / [D]raft (hidden from students) / [N]o: ").strip().lower()
        if answer in ("y", "yes"):
            return "yes"
        if answer in ("d", "draft"):
            return "draft"
        if answer in ("n", "no"):
            return "no"
        print("Please answer Yes, Draft, or No.")


def print_preview(title, body_html, dry_run):
    print("\n" + "=" * 60)
    print("DRY RUN - no changes will be made" if dry_run else "ANNOUNCEMENT PREVIEW")
    print("=" * 60)
    print(f"Title: {title}\n")
    print(body_html)
    print("=" * 60 + "\n")


def post_announcement(title, body_html):
    # Validate required configuration only when we're actually about to call
    # the Canvas API - a dry run should work with no .env file at all.
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
        raise ValueError(
            f"COURSE_ID must be a number, but got: {COURSE_ID_STR}\n"
            "Please check your .env file and use only digits for COURSE_ID"
        )

    print(f"🔗 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)

    print(f"📚 Fetching course {course_id}...")
    course = canvas.get_course(course_id)
    print(f"✅ Connected to course: {course.name}\n")

    choice = ask_post_choice()

    if choice == "no":
        print("Aborted. Nothing was posted.")
        return

    # Determine parameters based on user choice
    is_published = (choice == "yes")
    delayed_at = None
    hold_until = None
    
    if not is_published:
        # Draft mode logic
        hold_until = datetime.now(timezone.utc) + timedelta(days=DRAFT_HOLD_DAYS)
        delayed_at = hold_until.strftime("%Y-%m-%dT%H:%M:%SZ")

    topic = course.create_discussion_topic(
        title=title,
        message=body_html,
        is_announcement=True,
        published=is_published,
        delayed_post_at=delayed_at,
    )

    if is_published:
        print(f"🎉 Announcement published: {topic.html_url}")
    else:
        print(f"📝 Created, hidden from students until {hold_until.isoformat()}: {topic.html_url}")
        print("   To make it visible sooner, edit the 'Available from' date on the announcement in Canvas.")


def main():
    parser = argparse.ArgumentParser(description="Post the welcome announcement to Canvas.")
    parser.add_argument(
        "--post", action="store_true",
        help="Actually connect to Canvas and post (after confirmation). Without this flag, it's a dry run only."
    )
    args = parser.parse_args()

    title, body_html = load_announcement()

    print_preview(title, body_html, dry_run=not args.post)

    if not args.post:
        print("This was a dry run - no connection to Canvas was made and nothing was posted.")
        print("Re-run with --post to actually post it.")
        return

    post_announcement(title, body_html)


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
