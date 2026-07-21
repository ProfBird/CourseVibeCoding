"""
Canvas Learning Outcome Aligner - Create Learning Outcomes from a Markdown
file, then interactively align each one to Canvas assignments and/or quizzes.

Input file format (default: content/learning_outcomes.md) - two are supported:

  1. Headings, one outcome per '## ' section:

        # Course Learning Outcomes

        ## Explain fundamental concepts
        Students will be able to explain the fundamental concepts of
        programming, including variables, control flow, and functions.

        ## Apply debugging techniques
        Students will be able to apply systematic debugging techniques to
        identify and fix errors in code.

     The level-1 heading (optional) names the Canvas outcome group these LOs
     are grouped under. Each level-2 heading becomes one Learning Outcome;
     everything below it, up to the next level-2 heading, is the description.

  2. A plain numbered list (any intro text before the first numbered line is
     ignored):

        Upon successful completion of this course, the student will be able to:

        1. Demonstrate an understanding of the purpose uses of the internet
           and the HTTP protocol.
        2. Use common HTML elements and attributes in web pages they create.

     Each numbered line becomes one Learning Outcome titled "LO1: <short
     phrase>", "LO2: <short phrase>", etc., with the full sentence as its
     description.

What this script does:
  1. Lists the .md files in content/ and asks you to pick one (or skip).
     Parses the LOs out of it (no Canvas connection needed for this step).
  2. Connects to Canvas and creates any outcome that doesn't already exist
     (matched by title, anywhere in the course) in an outcome group named
     after the level-1 heading.
  3. For each outcome, asks which assignment(s) (if any) to align it to.
     You can enter multiple numbers separated by commas (e.g. '1,3'). Canvas
     aligns outcomes to assignments via a rubric: a rubric with one
     criterion linked to the outcome gets attached to the assignment. It is
     NOT set to affect grading, so it won't change the assignment's points.
  4. For each outcome, also asks which quiz(zes) it belongs to, the same
     way - a numbered list, comma-separated for multiple.
     IMPORTANT: Canvas's public API has no endpoint to align outcomes to
     quizzes or quiz item/question banks - that can only be done by hand in
     the Canvas UI. This script just records your answer to
     content/lo_alignment_log.json so you have a checklist of what to go
     tag manually.
  At either prompt, 'q' quits aligning entirely: remaining outcomes are
  still created/reused in Canvas, they just won't be prompted for alignment.

Usage:
    python align_learning_outcomes.py            # pick a file from content/, preview only
    python align_learning_outcomes.py --apply     # pick a file from content/, then connect and create/align
    python align_learning_outcomes.py --file content/CIS195_CourseLearningOutcomes.md --apply
                                                   # skip the file prompt
"""
import os
import re
import json
import argparse
from pathlib import Path
import markdown
from dotenv import load_dotenv
from canvasapi import Canvas

NUMBERED_ITEM_RE = re.compile(r"^\s*\d+[\.\)]\s+(.*\S)\s*$")

# Load environment variables from .env file
# This keeps credentials out of your code
load_dotenv()

# Retrieve Canvas API credentials from environment variables
CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')

CONTENT_DIR = Path(__file__).parent / "content"
LOG_PATH = CONTENT_DIR / "lo_alignment_log.json"


def choose_lo_file(content_dir):
    """List the Markdown files in content_dir and ask the user to pick one,
    or skip. Returns None if the user chooses to skip."""
    md_files = sorted(p for p in content_dir.glob("*.md") if p.is_file())
    if not md_files:
        raise FileNotFoundError(f"No .md files found in {content_dir}.")

    print(f"Learning outcome files in {content_dir}:")
    for i, p in enumerate(md_files, start=1):
        print(f"  {i}. {p.name}")
    print("  0. Skip (exit without processing a file)")

    while True:
        raw = input(f"Choose a file # (1-{len(md_files)}, or 0 to skip): ").strip()
        if raw == "0":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(md_files):
            return md_files[int(raw) - 1]
        print("Please enter a valid number from the list, or 0 to skip.")


def _make_short_title(n, sentence, max_len=60):
    """Build a 'LO<n>: <short phrase>' title from a full outcome sentence."""
    prefix = f"LO{n}: "
    remaining = max_len - len(prefix)
    if len(sentence) <= remaining:
        return prefix + sentence
    truncated = sentence[:remaining].rsplit(" ", 1)[0].rstrip(",.;: ")
    return f"{prefix}{truncated}..."


def _parse_headings(lines):
    """Parse '## Title' + body sections into a list of outcome dicts."""
    outcomes = []
    current_title = None
    current_body = []

    def flush():
        if current_title is None:
            return
        body_md = "\n".join(current_body).strip()
        if not body_md:
            raise ValueError(f"Learning outcome '{current_title}' has no description.")
        outcomes.append({
            "title": current_title,
            "description_md": body_md,
            "description_html": markdown.markdown(body_md),
        })

    for line in lines:
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip()
            current_body = []
        elif current_title is not None:
            current_body.append(line)

    flush()
    return outcomes


def _parse_numbered_list(lines):
    """Parse a plain numbered list ('1. ...', '2. ...') into outcome dicts.

    Any intro text before the first numbered line is ignored. Each item
    becomes one outcome: title "LO<n>: <short phrase>", description = the
    full sentence.
    """
    outcomes = []
    n = 0
    for line in lines:
        match = NUMBERED_ITEM_RE.match(line)
        if not match:
            continue
        n += 1
        sentence = match.group(1).strip()
        outcomes.append({
            "title": _make_short_title(n, sentence),
            "description_md": sentence,
            "description_html": markdown.markdown(sentence),
        })
    return outcomes


def parse_learning_outcomes(path):
    """Read the LO Markdown file and split it into a group title + a list of
    {"title", "description_md", "description_html"} dicts, one per LO.

    Supports two formats - see the module docstring for examples:
      - '## ' headings, one section per outcome
      - a plain numbered list ('1. ...', '2. ...')
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Create it with either a '## ' heading per "
            "learning outcome or a plain numbered list - see the format "
            "described at the top of this script."
        )

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"{path} is empty.")

    lines = text.splitlines()
    group_title = "Course Learning Outcomes"
    if lines and lines[0].startswith("# "):
        group_title = lines[0][2:].strip()
        lines = lines[1:]

    if any(line.startswith("## ") for line in lines):
        outcomes = _parse_headings(lines)
    else:
        outcomes = _parse_numbered_list(lines)

    if not outcomes:
        raise ValueError(
            f"No learning outcomes found in {path}. Use either a '## ' "
            "heading per outcome or a plain numbered list ('1. ...', '2. ...')."
        )

    return group_title, outcomes


def confirm(prompt):
    """Ask a yes/no question on the console. Only an explicit 'y'/'yes' counts as approval."""
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def get_or_create_outcome_group(course, title):
    """Find a course outcome subgroup with this title, or create it under the root group."""
    root = course.get_root_outcome_group()
    for sub in root.get_subgroups():
        if sub.title == title:
            return sub
    print(f"   Creating outcome group '{title}'...")
    return root.create_subgroup(title)


def _linked_outcomes_by_title(group, existing):
    """Add {lowercased title: outcome id} for outcomes linked directly to this group."""
    for link in group.get_linked_outcomes():
        outcome_id = link.outcome.get("id") if isinstance(link.outcome, dict) else None
        outcome_title = link.outcome.get("title") if isinstance(link.outcome, dict) else None
        if outcome_id and not outcome_title:
            outcome_title = link.get_outcome().title
        if outcome_id and outcome_title:
            existing[outcome_title.strip().lower()] = outcome_id


def all_existing_outcomes_by_title(course):
    """Map lowercased outcome title -> outcome id for every outcome linked
    anywhere in the course (not just the group this script manages), so
    outcomes entered by hand elsewhere in Canvas are recognized and reused
    instead of duplicated."""
    existing = {}

    def walk(group):
        _linked_outcomes_by_title(group, existing)
        for sub in group.get_subgroups():
            walk(sub)

    walk(course.get_root_outcome_group())
    return existing


def create_or_reuse_outcome(group, existing, lo):
    """Create the outcome in Canvas, or reuse one that already has this title."""
    key = lo["title"].strip().lower()
    if key in existing:
        print(f"   ↪️  '{lo['title']}' already exists in Canvas, reusing it.")
        return existing[key]

    link = group.link_new(title=lo["title"], description=lo["description_html"])
    outcome_id = link.outcome["id"]
    print(f"   ✅ Created outcome '{lo['title']}' (id {outcome_id})")
    return outcome_id


class QuitAlignment(Exception):
    """Raised when the user asks to stop the alignment prompts entirely."""


def _parse_number_list(raw, count):
    """Parse a comma-separated list of 1-based numbers (e.g. '1,3') into a
    list of unique 0-based indices, preserving the order entered. Returns
    None if any token isn't a valid number in range."""
    indices = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            return None
        n = int(token)
        if not (1 <= n <= count):
            return None
        if n - 1 not in indices:
            indices.append(n - 1)
    return indices


def prompt_numbered_choice(items, label_fn, plural, singular):
    """Show a numbered list of items and ask which one(s), if any, to select.
    Multiple items can be chosen as a comma-separated list of numbers, e.g.
    '1,3'. Raises QuitAlignment if the user asks to stop aligning altogether.
    """
    if not items:
        print(f"   (No {plural} in this course to align to.)")
        return []

    print(f"   {plural.capitalize()}:")
    for i, item in enumerate(items, start=1):
        print(f"     {i}. {label_fn(item)}")

    while True:
        raw = input(
            f"   Align with {singular} #(s) (comma-separated, blank to skip, "
            "'q' to quit aligning): "
        ).strip()
        if raw.lower() in ("q", "quit"):
            raise QuitAlignment()
        if raw == "":
            return []
        indices = _parse_number_list(raw, len(items))
        if indices is not None and indices:
            return [items[i] for i in indices]
        print(f"   Please enter valid number(s) from the list (e.g. '1,3'), "
              "'q' to quit, or leave blank to skip.")


def align_outcome_to_assignment(course, outcome_id, lo, assignment):
    """Attach a rubric with one outcome-linked criterion to the assignment.

    This is how Canvas actually represents an outcome/assignment alignment.
    use_for_grading is False so this doesn't change the assignment's points.

    Guards against duplicating work: if the assignment's existing rubric (if
    any) already has a criterion linked to this outcome, nothing is created.
    If it has a rubric not linked to this outcome, the user is asked before
    a second, competing rubric gets attached.

    Returns True if the assignment ends up aligned to this outcome (already
    was, or a new rubric was created), False if the user declined.
    """
    existing_criteria = getattr(assignment, "rubric", None) or []
    for criterion in existing_criteria:
        if isinstance(criterion, dict) and criterion.get("learning_outcome_id") == outcome_id:
            existing_title = (getattr(assignment, "rubric_settings", None) or {}).get("title", "?")
            print(f"   ↪️  '{assignment.name}' is already aligned to '{lo['title']}' via "
                  f"rubric '{existing_title}' - skipping.")
            return True

    existing_rubric_settings = getattr(assignment, "rubric_settings", None)
    if existing_rubric_settings:
        print(f"   ⚠️  '{assignment.name}' already has a rubric "
              f"('{existing_rubric_settings.get('title', '?')}') not linked to this outcome.")
        if not confirm(f"   Attach an additional rubric for '{lo['title']}' to this assignment anyway?"):
            print("   Skipped - not aligned.")
            return False

    result = course.create_rubric(
        rubric={
            "title": f"{lo['title']} (Outcome Alignment)",
            "free_form_criterion_comments": False,
            "criteria": {
                "0": {
                    "description": lo["title"],
                    "long_description": lo["description_md"],
                    "points": 5,
                    "learning_outcome_id": outcome_id,
                    "ratings": {
                        "0": {"description": "Mastery", "points": 5},
                        "1": {"description": "Does Not Meet Expectations", "points": 0},
                    },
                },
            },
        },
        rubric_association={
            "association_id": assignment.id,
            "association_type": "Assignment",
            "purpose": "grading",
            "use_for_grading": False,
        },
    )
    rubric = result.get("rubric")
    rubric_title = rubric.title if rubric else "(unknown)"
    print(f"   ✅ Aligned to assignment '{assignment.name}' via rubric '{rubric_title}'")
    return True


def run_preview(group_title, outcomes):
    print(f"Found {len(outcomes)} learning outcome(s), group '{group_title}':\n")
    for lo in outcomes:
        print(f"  - {lo['title']}")
        print(f"      {lo['description_md'][:100]}{'...' if len(lo['description_md']) > 100 else ''}")
    print("\nThis was a preview only - no connection to Canvas was made.")
    print("Re-run with --apply to create these in Canvas and align them.")


def run_apply(group_title, outcomes):
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

    print(f"\U0001f517 Connecting to Canvas: {CANVAS_URL}")
    canvas = Canvas(CANVAS_URL, CANVAS_TOKEN)

    print(f"\U0001f4da Fetching course {course_id}...")
    course = canvas.get_course(course_id)
    print(f"✅ Connected to course: {course.name}\n")

    print(f"About to create/reuse {len(outcomes)} learning outcome(s) in the "
          f"'{group_title}' outcome group, then ask you how to align each one.")
    if not confirm("Continue?"):
        print("Aborted. Nothing was changed.")
        return

    group = get_or_create_outcome_group(course, group_title)
    existing = all_existing_outcomes_by_title(course)
    assignments = list(course.get_assignments(include=["rubric_association"]))
    quizzes = list(course.get_quizzes())

    log = []
    quit_index = None
    for i, lo in enumerate(outcomes):
        print(f"\n=== {lo['title']} ===")
        outcome_id = create_or_reuse_outcome(group, existing, lo)

        aligned_assignments = []
        selected_quizzes = []
        if quit_index is None:
            try:
                selected_assignments = prompt_numbered_choice(
                    assignments, lambda a: a.name, "assignments", "assignment"
                )
                for a in selected_assignments:
                    if align_outcome_to_assignment(course, outcome_id, lo, a):
                        aligned_assignments.append(a)

                selected_quizzes = prompt_numbered_choice(
                    quizzes, lambda q: q.title, "quizzes", "quiz"
                )
            except QuitAlignment:
                quit_index = i
                print(f"\nStopping alignment prompts. The remaining "
                      f"{len(outcomes) - i} learning outcome(s) will still be "
                      "created/reused in Canvas, just not aligned.")

        log.append({
            "learning_outcome": lo["title"],
            "canvas_outcome_id": outcome_id,
            "aligned_assignments": [
                {"name": a.name, "id": a.id} for a in aligned_assignments
            ],
            "aligned_quizzes": [
                {"name": q.title, "id": q.id} for q in selected_quizzes
            ],
        })

    merged_log = {}
    if LOG_PATH.exists():
        try:
            for entry in json.loads(LOG_PATH.read_text(encoding="utf-8")):
                if isinstance(entry, dict) and "learning_outcome" in entry:
                    merged_log[entry["learning_outcome"]] = entry
        except json.JSONDecodeError:
            pass
    for entry in log:
        merged_log[entry["learning_outcome"]] = entry

    LOG_PATH.write_text(json.dumps(list(merged_log.values()), indent=2), encoding="utf-8")
    print(f"\n\U0001f389 Done. Alignment summary written to {LOG_PATH}")

    if quit_index is not None:
        print(f"Alignment was skipped for {len(outcomes) - quit_index} learning "
              "outcome(s) after you quit - all outcomes were still created in Canvas.")

    banked = [entry for entry in log if entry["aligned_quizzes"]]
    if banked:
        print("\n⚠️  Canvas's API can't align outcomes to quizzes or quiz item/question "
              "banks directly.")
        print("   Use this list to tag them by hand in Canvas's UI:")
        for entry in banked:
            quiz_names = ", ".join(q["name"] for q in entry["aligned_quizzes"])
            print(f"     - {entry['learning_outcome']} -> {quiz_names}")


def main():
    parser = argparse.ArgumentParser(
        description="Create Canvas Learning Outcomes from a Markdown file and align them."
    )
    parser.add_argument(
        "--file", default=None,
        help="Path to the learning outcomes Markdown file. If omitted, you'll "
             f"be prompted to choose one from {CONTENT_DIR}."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually connect to Canvas and create/align (after confirmation). "
             "Without this flag, it's a preview only."
    )
    args = parser.parse_args()

    lo_path = Path(args.file) if args.file else choose_lo_file(CONTENT_DIR)
    if lo_path is None:
        print("Skipped. Nothing was done.")
        return

    group_title, outcomes = parse_learning_outcomes(lo_path)

    if not args.apply:
        run_preview(group_title, outcomes)
        return

    run_apply(group_title, outcomes)


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


