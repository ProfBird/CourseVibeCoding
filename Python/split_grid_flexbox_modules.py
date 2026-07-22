"""
Split Module 7 (CSS Grid and Flex Box) into two separate modules.

Module 7 of CIS 195 covers CSS Grid and CSS Flexbox together. This script
creates two new modules at the bottom of the Modules page, one per topic:

    CSS Grid
    CSS Flexbox

Both are created UNPUBLISHED, so they are hidden from students. Module 7 is
left completely untouched - nothing is moved, edited, or deleted.

The reading, lecture notes, and example content is fixed (copied/adapted from
Module 7, split by topic). The week number, the lab assignment, and an
optional quiz are NOT hardcoded - when you run with --apply you're prompted
for each, separately per module, so the same script can be reused for other
weeks without editing the code. The week number you enter is substituted into
whatever text was copied verbatim from Module 7 that references a week (the
lecture recording titles); it's not added anywhere else.

The assignment and quiz are real, new Canvas objects, not links to existing
ones: you pick an existing assignment/quiz as a template, type a new name,
and the script duplicates it via Canvas's own duplicate-assignment endpoint
(the same mechanism behind the "Duplicate" button in Canvas's UI - confirmed
to work on this course's New Quizzes, not just classic assignments) and
renames the copy. The copy's due date, if the template had one, is shifted by
(this module's week number - 7) weeks - 7 because Module 7, the module this
script splits, is itself "Week 7" per its own overview page - so the copy
lands on the same weekday/time in this module's week.

A few Canvas course-design conventions are applied on top of the content:
  - Items are indented under their SubHeader for visual grouping.
  - Each module requires viewing its overview page; if you pick an
    assignment, submitting it is also required to mark the module complete.
  - The Flexbox module is locked behind the Grid module's completion
    (prerequisite_module_ids), so students see them in order once published.
  - Each overview page states the estimated time to complete the module.

  Offline preview (default):
    Prints the structure that will be created - pages, links, sections - with
    placeholders for the week number, assignment, and quiz, since those are
    only known once you answer the prompts. Makes NO connection to Canvas and
    NO changes whatsoever.

  Apply mode (--apply):
    Connects to Canvas, asks you to confirm, then for each module prompts for
    its week number, then a template + new name for its assignment (numbered
    list, blank to skip), and the same for an optional quiz. This course's
    quizzes are Canvas New Quizzes, which the API exposes as regular
    assignments, so both prompts list the same assignments.

Usage:
    python split_grid_flexbox_modules.py           # offline structural preview
    python split_grid_flexbox_modules.py --apply   # connect, prompt per module, create
"""
import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv
from canvasapi import Canvas

# Windows consoles default to cp1252, which can't encode the emoji used in the
# progress output. Fall back gracefully rather than crashing mid-run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Load environment variables from .env file
# This keeps credentials out of your code
load_dotenv()

# Retrieve Canvas API credentials from environment variables
CANVAS_URL = os.getenv('CANVAS_URL')
CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
COURSE_ID_STR = os.getenv('COURSE_ID')

# Canvas content IDs carried over from Module 7. These are course-specific and
# were read from the course export in downloads/. The lab assignment and any
# quiz are NOT here - they're chosen interactively per module in --apply mode.
GRID_DEMOS_FILE_ID = 73039      # "CssGridDemos.zip"
GRID_IMAGE_FILE_ID = 73057      # grid-paper illustration used on the overview page

LAB6_INSTRUCTIONS_URL = "https://lcc-cit.github.io/CIS195-CourseMaterials/LabStarters/Lab06/Lab6Instructions_8wk.html"
SOUTH_INDIA_DEMO_URL = "https://lcc-cit.github.io/CIS195-Demos/Unit05_GridAndFlexbox/Finished/"

# Module 7 ("CSS Grid and Flex Box"), the module this script splits, is itself
# "Week 7" per its own overview page banner. Due dates on duplicated
# assignments/quizzes are shifted by (target module's week - 7) weeks, so a
# template's due date lands on the same weekday/time in the new week.
SOURCE_MODULE_WEEK = 7


# ---------------------------------------------------------------------------
# Page bodies for the new modules
#
# The overview pages are adapted from the Module 7 overview page, minus its
# week/date banner - these modules aren't tied to a fixed week in the code.
# The reading pages split the combined "Selected Sections of The CSS Handbook"
# page into its Grid chapter and its Flexbox chapter.
# ---------------------------------------------------------------------------

GRID_OVERVIEW_BODY = f"""<div class="summary expanded">
  <div class="no-overflow">
    <figure style="float: right;">
      <img src="https://canvas.lanecc.edu/courses/1196/files/{GRID_IMAGE_FILE_ID}/preview" alt="Woman drawing squares on grid paper." class="img-fluid atto_image_button_text-bottom" width="200" height="200" loading="lazy" data-api-endpoint="https://canvas.lanecc.edu/api/v1/courses/1196/files/{GRID_IMAGE_FILE_ID}" data-api-returntype="File">
      <figcaption>Image generated by AI</figcaption>
    </figure>
    <h2>Overview</h2>
    <p dir="ltr" style="text-align: left;">In this module you will learn to control the layout of a page with <strong>CSS Grid</strong>, a two-dimensional layout system that arranges content into rows and columns.</p>
    <p dir="ltr" style="text-align: left;">By the end of this module you should be able to:</p>
    <ul dir="ltr">
      <li style="text-align: left;">Define a grid container and set up grid rows and columns</li>
      <li style="text-align: left;">Place elements into specific grid areas</li>
      <li style="text-align: left;">Use the <code>gap</code> property to space grid items</li>
      <li style="text-align: left;">Choose CSS Grid over other layout techniques when it is the better fit</li>
    </ul>
    <p dir="ltr" style="text-align: left;"><strong>Estimated time:</strong> 2-3 hours (reading, lecture notes, and this module's assignment).</p>
    <p dir="ltr" style="text-align: left;"><strong>To complete this module:</strong> view this page, then check the Activities section below for this module's assignment (and quiz, if one is listed).</p>
  </div>
</div>"""

FLEXBOX_OVERVIEW_BODY = """<div class="summary expanded">
  <div class="no-overflow">
    <h2>Overview</h2>
    <p dir="ltr" style="text-align: left;">In this module you will learn to control the layout of a page with <strong>CSS Flexbox</strong>, a one-dimensional layout system that distributes content along a single row or column.</p>
    <p dir="ltr" style="text-align: left;">By the end of this module you should be able to:</p>
    <ul dir="ltr">
      <li style="text-align: left;">Define a flex container and set its main axis with <code>flex-direction</code></li>
      <li style="text-align: left;">Align and distribute flex items with <code>justify-content</code> and <code>align-items</code></li>
      <li style="text-align: left;">Control how flex items grow, shrink, and wrap</li>
      <li style="text-align: left;">Choose CSS Flexbox over other layout techniques when it is the better fit</li>
    </ul>
    <p dir="ltr" style="text-align: left;"><strong>Estimated time:</strong> 2-3 hours (reading, lecture notes, and this module's assignment).</p>
    <p dir="ltr" style="text-align: left;"><strong>To complete this module:</strong> view this page, then check the Activities section below for this module's assignment (and quiz, if one is listed).</p>
  </div>
</div>"""

GRID_READING_BODY = """<p>Selected Sections of <a href="https://flaviocopes.com/book/css/" target="_blank"><em>The CSS Handbook</em></a> by Flavio Copes</p>
<ul>
<li>
<a href="https://flaviocopes.com/book/css/#31-css-grid" target="_blank">31. CSS Grid</a>
<ul><li>Note: this chapter refers to the CSS2 property <code>grid-gap</code>, which is now deprecated and replaced by the CSS3 property <code>gap</code>.</li></ul>
</li>
</ul>"""

FLEXBOX_READING_BODY = """<p>Selected Sections of <a href="https://flaviocopes.com/book/css/" target="_blank"><em>The CSS Handbook</em></a> by Flavio Copes</p>
<ul>
<li><a href="https://flaviocopes.com/book/css/#32-flexbox" target="_blank">32. Flexbox</a></li>
</ul>"""


# ---------------------------------------------------------------------------
# The two modules, described as templates.
#
# Everything here is fixed content copied/adapted from Module 7. The week
# number, assignment, and quiz are filled in at runtime by resolve_module_items
# - "{week_label}" in recording_title_template is the only place the week
# number is substituted, since that's the only text copied verbatim from
# Module 7 that referenced a week ("Week 7, Tuesday/Thursday lecture recording").
# ---------------------------------------------------------------------------

MODULE_TEMPLATES = [
    {
        "name": "CSS Grid",
        "overview_title": "CSS Grid",
        "overview_body": GRID_OVERVIEW_BODY,
        "reading_title": "Reading: CSS Grid",
        "reading_body": GRID_READING_BODY,
        "lecture_title": "Positioning with CSS Grid",
        "lecture_url": "https://lcc-cit.github.io/CIS195-CourseMaterials/LectureNotes/CIS195-LN-W06-D2A-CssGrid.html",
        "recording_title_template": "{week_label}, Tuesday lecture recording",
        "recording_url": "https://lanecc.zoom.us/rec/share/Uc7MV58IKxT0WjJJJGeNLpXIVXDDkA-gCDXvJcvC1Ja-ZCdvSgl_cnOx9q8mzx3k.NoCD0e_5dhbhO6T_",
        "extra_examples": [
            {"type": "ExternalUrl", "title": "South India Site with Grid and Flexbox",
             "external_url": SOUTH_INDIA_DEMO_URL},
            {"type": "ExternalUrl", "title": "CSS Grid Examples",
             "external_url": "https://lcc-cit.github.io/CIS195-CourseMaterials/Examples/LayoutDemos/cssGridExample.html"},
            {"type": "File", "title": "In-class grid demos—summer 2023",
             "content_id": GRID_DEMOS_FILE_ID},
        ],
        "lab_instructions_title": "Lab 6 Instructions",
        "lab_instructions_url": LAB6_INSTRUCTIONS_URL,
    },
    {
        "name": "CSS Flexbox",
        "prerequisite": "CSS Grid",
        "overview_title": "CSS Flexbox",
        "overview_body": FLEXBOX_OVERVIEW_BODY,
        "reading_title": "Reading: CSS Flexbox",
        "reading_body": FLEXBOX_READING_BODY,
        "lecture_title": "Positioning with CSS Flexbox",
        "lecture_url": "https://lcc-cit.github.io/CIS195-CourseMaterials/LectureNotes/CIS195-LN-W06-D2B-CssFlexBox.html",
        "recording_title_template": "{week_label}, Thursday lecture recording",
        "recording_url": "https://lanecc.zoom.us/rec/share/av6CumGQEahnrqY1jrP-Yr708ZTv7QrsKL9b12LoxgsMIAObMeaCIVKp47VLSB0n.URz-Xhqi7m8h2cqx",
        "extra_examples": [
            {"type": "ExternalUrl", "title": "South India Site with Grid and Flexbox",
             "external_url": SOUTH_INDIA_DEMO_URL},
        ],
        "lab_instructions_title": "Lab 6 Instructions",
        "lab_instructions_url": LAB6_INSTRUCTIONS_URL,
    },
]


def compute_indents(items):
    """Indent level for each item: 0 for SubHeaders (and anything before the
    first one, i.e. the overview page), 1 for everything grouped under the
    most recently seen SubHeader."""
    indents = []
    indent = 0
    for item in items:
        if item["type"] == "SubHeader":
            indent = 0
            indents.append(indent)
            indent = 1
        else:
            indents.append(indent)
    return indents


def confirm(prompt):
    """Ask a yes/no question on the console. Only an explicit 'y'/'yes' counts as approval."""
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def choose_one(items, label_fn, question):
    """Show a numbered list and ask for exactly one choice, or blank to skip.
    Returns the chosen item, or None if skipped."""
    if not items:
        print("   (Nothing available to choose from.)")
        return None
    for i, item in enumerate(items, start=1):
        print(f"     {i}. {label_fn(item)}")
    while True:
        raw = input(f"   {question} (blank to skip): ").strip()
        if raw == "":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print("   Please enter a valid number from the list, or leave blank to skip.")


def choose_week_number(module_name, used_weeks):
    """Ask for a whole number not already used by another module in this run."""
    while True:
        raw = input(f"   Week number for '{module_name}' (e.g. 7): ").strip()
        if not raw.isdigit():
            print("   Please enter a whole number.")
            continue
        week = int(raw)
        if week in used_weeks:
            print(f"   Week {week} was already used for another module in this run - "
                  "please enter a different week.")
            continue
        used_weeks.add(week)
        return week


def choose_name(prompt_text, used_names):
    """Ask for a non-empty name that doesn't collide (case-insensitively) with
    an existing Canvas assignment/quiz or a name already chosen in this run."""
    while True:
        raw = input(f"   {prompt_text}: ").strip()
        if not raw:
            print("   Please enter a name.")
            continue
        if raw.lower() in used_names:
            print(f"   '{raw}' is already in use (an existing assignment/quiz, or a name "
                  "you already chose in this run) - please enter a different name.")
            continue
        used_names.add(raw.lower())
        return raw


def shifted_due_at(original_due_at, target_week):
    """Shift an ISO8601 due date by (target_week - SOURCE_MODULE_WEEK) whole
    weeks, preserving the weekday and time of day. None if there's nothing to
    shift (the template had no due date)."""
    if not original_due_at:
        return None
    dt = datetime.strptime(original_due_at, "%Y-%m-%dT%H:%M:%SZ")
    shifted = dt + timedelta(weeks=(target_week - SOURCE_MODULE_WEEK))
    return shifted.strftime("%Y-%m-%dT%H:%M:%SZ")


def duplicate_and_rename(course, template_obj, new_name, target_week):
    """Duplicate an existing assignment/quiz via Canvas's duplicate-assignment
    endpoint (not wrapped by canvasapi, so called directly), wait for the copy
    to finish - Canvas does this asynchronously for New Quizzes - then rename
    it and shift its due date. Returns the resulting Assignment."""
    resp = course._requester.request(
        "POST", f"courses/{course.id}/assignments/{template_obj.id}/duplicate"
    )
    new_id = resp.json()["id"]

    duplicate = course.get_assignment(new_id)
    for _ in range(15):
        if duplicate.workflow_state != "duplicating":
            break
        time.sleep(2)
        duplicate = course.get_assignment(new_id)
    else:
        print(f"      ⚠️  Still copying in Canvas after 30s - it will finish shortly in the background.")

    edits = {"name": new_name}
    new_due = shifted_due_at(getattr(template_obj, "due_at", None), target_week)
    if new_due:
        edits["due_at"] = new_due

    return duplicate.edit(assignment=edits)


def resolve_module_items(course, template, assignments, used_weeks, used_names):
    """Prompt for this module's week number, assignment, and optional quiz,
    then build the final list of module items from the template. Called once
    per module, so Grid and Flexbox can get different answers.

    used_weeks and used_names are shared across all modules processed in this
    run, so a week number or a new assignment/quiz name can't be entered
    twice - and used_names starts pre-loaded with every existing Canvas
    assignment/quiz name, so a new one can't collide with those either.
    """
    print(f"\n--- Configuring '{template['name']}' ---")
    week = choose_week_number(template["name"], used_weeks)
    week_label = f"Week {week}"

    print("   Assignment (pick an existing one to duplicate as the template for this module's lab):")
    assignment_template = choose_one(assignments, lambda a: a.name, "Choose a template assignment #")
    assignment = None
    if assignment_template is not None:
        new_name = choose_name("New name for the duplicated assignment", used_names)
        assignment = duplicate_and_rename(course, assignment_template, new_name, week)
        print(f"      ✅ Duplicated '{assignment_template.name}' -> '{assignment.name}' (id {assignment.id})")

    print("   Quiz (optional - duplicated the same way; this course's quizzes are New Quizzes, "
          "listed among the assignments above):")
    quiz_template = choose_one(assignments, lambda a: a.name, "Choose a template quiz #")
    quiz = None
    if quiz_template is not None:
        new_name = choose_name("New name for the duplicated quiz", used_names)
        quiz = duplicate_and_rename(course, quiz_template, new_name, week)
        print(f"      ✅ Duplicated '{quiz_template.name}' -> '{quiz.name}' (id {quiz.id})")

    items = [
        {"type": "Page", "title": template["overview_title"], "body": template["overview_body"],
         "require": "must_view"},
        {"type": "SubHeader", "title": "Reading"},
        {"type": "Page", "title": template["reading_title"], "body": template["reading_body"]},
        {"type": "SubHeader", "title": "Lecture Notes"},
        {"type": "ExternalUrl", "title": template["lecture_title"], "external_url": template["lecture_url"]},
        {"type": "SubHeader", "title": "Recordings"},
        {"type": "ExternalUrl",
         "title": template["recording_title_template"].format(week_label=week_label),
         "external_url": template["recording_url"]},
        {"type": "SubHeader", "title": "Examples"},
        *template["extra_examples"],
    ]

    if quiz is not None:
        items.append({"type": "SubHeader", "title": "Quiz"})
        items.append({"type": "Assignment", "title": quiz.name, "content_id": quiz.id})

    items.append({"type": "SubHeader", "title": "Activities"})
    items.append({"type": "ExternalUrl", "title": template["lab_instructions_title"],
                  "external_url": template["lab_instructions_url"]})
    if assignment is not None:
        items.append({"type": "Assignment", "title": assignment.name, "content_id": assignment.id,
                      "require": "must_submit"})
    else:
        print("   ⚠️  No assignment selected - Activities will only include the instructions link.")

    return items


def print_template_plan():
    print("\n" + "=" * 60)
    print("OFFLINE PREVIEW - no connection to Canvas, no changes will be made")
    print("=" * 60)
    for template in MODULE_TEMPLATES:
        print(f"\n📦 New module: {template['name']}  (unpublished - hidden from students)")
        print("   Position: bottom of the Modules page")
        if template.get("prerequisite"):
            print(f"   🔒 Will be locked until '{template['prerequisite']}' is completed")
        print("   New pages to create: 2 (also unpublished)")
        print("   Structure (week number, and a template + new name for the assignment/quiz, are")
        print("   chosen when you run --apply; assignment and quiz are NEW Canvas objects, duplicated")
        print("   from whatever existing ones you pick as templates):")
        print(f"     [Page] {template['overview_title']}  (requirement: must_view)")
        print("     [SubHeader] Reading")
        print(f"       [Page] {template['reading_title']}")
        print("     [SubHeader] Lecture Notes")
        print(f"       [ExternalUrl] {template['lecture_title']}")
        print("     [SubHeader] Recordings")
        recording_suffix = template["recording_title_template"].split("}, ", 1)[1]
        print(f"       [ExternalUrl] <Week N>, {recording_suffix}")
        print("     [SubHeader] Examples")
        for extra in template["extra_examples"]:
            print(f"       [{extra['type']}] {extra['title']}")
        print("     [SubHeader] Quiz  (only created if you pick a template quiz to duplicate)")
        print("       [Assignment] <new quiz - duplicated from your template, renamed>")
        print("     [SubHeader] Activities")
        print(f"       [ExternalUrl] {template['lab_instructions_title']}")
        print("       [Assignment] <new assignment - duplicated from your template, renamed>  "
              "(requirement: must_submit, if created)")
    print("\n" + "=" * 60)
    print("Module 7 is NOT modified - nothing is moved, edited, or deleted.")
    print("=" * 60 + "\n")


def get_course():
    """Validate configuration and connect. Only called when actually writing -
    the offline preview should work with no .env file at all."""
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
    return course


def find_existing_module(course, name):
    """Return an existing module with this exact name, or None.

    Lets the script be re-run safely without creating duplicate modules.
    """
    for module in course.get_modules():
        if getattr(module, "name", None) == name:
            return module
    return None


def create_page(course, title, body):
    """Create an unpublished wiki page and return it.

    Pages are created unpublished so they stay hidden from students even
    though they are reachable from the course Pages index.
    """
    return course.create_page(wiki_page={
        "title": title,
        "body": body,
        "published": False,
    })


def build_module(course, name, items, prerequisite_module_id=None):
    """Create one module, its pages, and its items. Returns the module's id
    (whether newly created or already existing), so callers can wire it up
    as another module's prerequisite."""
    existing = find_existing_module(course, name)
    if existing:
        print(f"↪️  Module '{name}' already exists (id {existing.id}) - skipping.")
        return existing.id

    module_payload = {"name": name, "published": False}
    if prerequisite_module_id:
        module_payload["prerequisite_module_ids"] = [prerequisite_module_id]

    # position is omitted so Canvas appends the module at the bottom.
    module = course.create_module(module=module_payload)
    lock_note = f", locked behind module {prerequisite_module_id}" if prerequisite_module_id else ""
    print(f"📦 Created module '{name}' (id {module.id}, unpublished{lock_note})")

    indents = compute_indents(items)
    for item, indent in zip(items, indents):
        payload = {"type": item["type"], "title": item["title"], "indent": indent}

        if item["type"] == "Page":
            page = create_page(course, item["title"], item["body"])
            payload["page_url"] = page.url
            print(f"      📄 Created page '{item['title']}' ({page.url}, unpublished)")
        elif item["type"] == "ExternalUrl":
            payload["external_url"] = item["external_url"]
            payload["new_tab"] = True
        elif "content_id" in item:
            payload["content_id"] = item["content_id"]

        if item.get("require"):
            payload["completion_requirement"] = {"type": item["require"]}

        module.create_module_item(module_item=payload)
        req_note = f" (requires {item['require']})" if item.get("require") else ""
        print(f"      ✅ [{item['type']}] {item['title']}{req_note}")

    print(f"🎉 Module '{name}' complete: {CANVAS_URL}/courses/{course.id}/modules\n")
    return module.id


def main():
    parser = argparse.ArgumentParser(
        description="Split Module 7 into separate CSS Grid and CSS Flexbox modules."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually connect to Canvas, prompt for each module's week number, "
             "assignment, and optional quiz, then create the modules (after "
             "confirmation). Without this flag, it's an offline structural preview."
    )
    args = parser.parse_args()

    if not args.apply:
        print_template_plan()
        print("This was an offline preview - no connection to Canvas was made and nothing was created.")
        print("Re-run with --apply to be prompted for each module's details and create them.")
        return

    course = get_course()

    if not confirm(
        f"Proceed with configuring and creating {len(MODULE_TEMPLATES)} "
        f"unpublished modules in '{course.name}'?"
    ):
        print("Aborted. Nothing was created.")
        return

    assignments = list(course.get_assignments(include=["rubric_association"]))

    used_weeks = set()
    used_names = {a.name.strip().lower() for a in assignments}

    module_ids = {}
    for template in MODULE_TEMPLATES:
        items = resolve_module_items(course, template, assignments, used_weeks, used_names)
        prereq_id = module_ids.get(template.get("prerequisite"))
        module_ids[template["name"]] = build_module(
            course, template["name"], items, prerequisite_module_id=prereq_id
        )


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
