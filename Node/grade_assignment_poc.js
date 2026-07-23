#!/usr/bin/env node
/**
 * Canvas Grading Proof-of-Concept - Node.js port of
 * ../Python/grade_assignment_poc.py. Check whether this CANVAS_TOKEN can
 * submit a grade for one assignment submission via the API.
 *
 * This is a minimal probe, not a bulk grading tool: it targets exactly one
 * assignment + one user (matched by Canvas ID, never by name) and shows the
 * current submission state before touching anything.
 *
 *   Dry run (default):
 *     Connects, fetches the assignment and the target user's current
 *     submission, and prints what WOULD change. No write call is made.
 *
 *   Apply mode (--apply):
 *     Same preview, then asks for confirmation, then does one
 *     PUT .../submissions/:user_id call and prints Canvas's response
 *     (score, grade, graded_at, grader_id) so you can see the write actually
 *     landed - or see exactly why it didn't (e.g. a 401/403 means this token
 *     can't grade in this course).
 *
 * Unlike the Python version this has no third-party dependencies: it uses
 * Node's built-in global fetch (Node >= 18) and reads .env itself, so it runs
 * with just `node grade_assignment_poc.js` - no install step.
 *
 * Usage:
 *     node grade_assignment_poc.js --assignment-id 22146 --user-id 987654 --grade 10
 *         # dry run - shows the assignment, the user, and the current submission
 *
 *     node grade_assignment_poc.js --assignment-id 22146 --user-id 987654 --grade 10 --apply
 *         # after confirming, actually submits the grade
 *
 * Find --user-id from the Test Student's submission in SpeedGrader (or People
 * page for a real enrollment) - this script does not look anyone up by name.
 */
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import readline from 'node:readline';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DOWNLOAD_ROOT = path.join(__dirname, 'downloads');


// ---------------------------------------------------------------------------
// Environment: mirror python-dotenv's load_dotenv() by walking up from this
// file to find a .env (the shared one lives at the repo root, a level up from
// Node/), parsing KEY=VALUE lines, and NOT overriding vars already in the
// real environment (e.g. Codespaces secrets).
// ---------------------------------------------------------------------------

function parseEnvFile(filePath) {
  const text = fs.readFileSync(filePath, 'utf-8');
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (value[0] === '"' || value[0] === "'") {
      // Quoted value: take what's inside the quotes literally; ignore any
      // trailing inline comment after the closing quote.
      const quote = value[0];
      const end = value.indexOf(quote, 1);
      value = end === -1 ? value.slice(1) : value.slice(1, end);
    } else {
      // Unquoted value: strip an inline comment (a '#' with whitespace before
      // it), matching python-dotenv - e.g. `COURSE_ID=680 # sandbox` -> `680`.
      const comment = value.match(/\s#/);
      if (comment) value = value.slice(0, comment.index);
      value = value.trimEnd();
    }
    if (!(key in process.env)) process.env[key] = value;
  }
}

function loadEnv() {
  let dir = __dirname;
  for (let i = 0; i < 6; i++) {
    const candidate = path.join(dir, '.env');
    if (fs.existsSync(candidate)) {
      parseEnvFile(candidate);
      return candidate;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

loadEnv();

const CANVAS_URL = process.env.CANVAS_URL;
const CANVAS_TOKEN = process.env.CANVAS_TOKEN;
const COURSE_ID_STR = process.env.COURSE_ID;

const API_BASE = (CANVAS_URL || '').replace(/\/+$/, '') + '/api/v1';


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Raised for a non-OK Canvas API response, carrying the HTTP status. */
class CanvasError extends Error {
  constructor(status, message) {
    super(message);
    this.name = 'CanvasError';
    this.status = status;
  }
}

/** Strip the Canvas token out of any string before it's printed or logged. */
function sanitize(text) {
  text = String(text);
  if (CANVAS_TOKEN) text = text.split(CANVAS_TOKEN).join('***REDACTED***');
  return text;
}

const authHeaders = { Authorization: `Bearer ${CANVAS_TOKEN}` };

async function canvasError(res) {
  const body = await res.text().catch(() => '');
  return new CanvasError(
    res.status,
    `${res.status} ${res.statusText} for ${sanitize(res.url)} ${sanitize(body).slice(0, 300)}`.trim()
  );
}

/** GET one JSON resource from the Canvas API (path relative to /api/v1). */
async function apiGetJson(pathAndQuery) {
  const res = await fetch(`${API_BASE}${pathAndQuery}`, { headers: authHeaders });
  if (!res.ok) throw await canvasError(res);
  return res.json();
}

/** PUT a JSON body to the Canvas API and return the parsed JSON response. */
async function apiPutJson(pathAndQuery, body) {
  const res = await fetch(`${API_BASE}${pathAndQuery}`, {
    method: 'PUT',
    headers: { ...authHeaders, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await canvasError(res);
  return res.json();
}

/** Ask a yes/no question on the console. Only an explicit y/yes counts as approval. */
function confirm(prompt) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(`${prompt} [y/N]: `, (answer) => {
      rl.close();
      resolve(['y', 'yes'].includes(answer.trim().toLowerCase()));
    });
  });
}

/** UTC timestamp matching Python's strftime("%Y%m%dT%H%M%SZ"), e.g. 20260722T231807Z. */
function utcStamp() {
  return new Date().toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
}

async function writeLog(courseId, assignmentId, userId, log) {
  await fsp.mkdir(DOWNLOAD_ROOT, { recursive: true });
  const filePath = path.join(DOWNLOAD_ROOT, `grade_poc_${courseId}_${assignmentId}_${userId}_${utcStamp()}.json`);
  await fsp.writeFile(filePath, JSON.stringify(log, null, 2), 'utf-8');
  console.log(`📝 Log written: ${filePath}`);
}

/** Minimal argv parser: --assignment-id/--user-id are required ints, --grade is a
 * required string (grades can be "100%" or a letter, not just a number),
 * --comment is an optional string, --apply is a boolean flag. */
function parseArgs(argv) {
  const opts = { assignmentId: null, userId: null, grade: null, comment: 'Thank you for the submission.', apply: false };
  const stringFlags = { '--grade': 'grade', '--comment': 'comment' };
  const intFlags = { '--assignment-id': 'assignmentId', '--user-id': 'userId' };

  for (let i = 0; i < argv.length; i++) {
    let arg = argv[i];
    let value = null;
    const eq = arg.indexOf('=');
    if (arg.startsWith('--') && eq !== -1) {
      value = arg.slice(eq + 1);
      arg = arg.slice(0, eq);
    }
    if (arg in intFlags) {
      if (value === null) value = argv[++i];
      const n = Number(value);
      if (!Number.isInteger(n)) throw new Error(`${arg} expects an integer Canvas ID, got: ${value}`);
      opts[intFlags[arg]] = n;
    } else if (arg in stringFlags) {
      if (value === null) value = argv[++i];
      opts[stringFlags[arg]] = value;
    } else if (arg === '--apply') {
      opts.apply = true;
    } else if (arg === '-h' || arg === '--help') {
      opts.help = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return opts;
}

const USAGE = `Proof of concept: submit one grade via the Canvas API.

Usage:
  node grade_assignment_poc.js --assignment-id N --user-id N --grade G [--comment TEXT] [--apply]

  --assignment-id N   Canvas assignment ID. (required)
  --user-id N         Canvas user ID to grade (e.g. the Test Student). (required)
  --grade G           Grade to submit, e.g. '10', '100%', or 'A'. (required)
  --comment TEXT      Grader comment to attach (default: 'Thank you for the
                      submission.'). Pass --comment='' for no comment.
  --apply             Actually submit the grade after confirmation. Without
                      this, only previews.`;


async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(USAGE);
    return;
  }
  if (args.assignmentId === null || args.userId === null || args.grade === null) {
    throw new Error('--assignment-id, --user-id, and --grade are all required. See --help.');
  }

  if (!CANVAS_URL || !CANVAS_TOKEN || !COURSE_ID_STR) {
    throw new Error(
      'Missing required environment variables!\n' +
        'Please ensure your .env file has CANVAS_URL, CANVAS_TOKEN, and COURSE_ID.'
    );
  }
  const courseId = Number(COURSE_ID_STR);

  console.log(`🔗 Connecting to Canvas: ${CANVAS_URL}`);
  let course;
  try {
    course = await apiGetJson(`/courses/${courseId}`);
  } catch (e) {
    if (e instanceof CanvasError && e.status === 404) {
      console.log(
        `\n❌ Course ${courseId} (from COURSE_ID in .env) does not exist, or this token can't see it.`
      );
      process.exit(1);
    }
    throw e;
  }
  console.log(`📚 Course: "${course.name}" (id ${course.id}, status: ${course.workflow_state})`);

  let assignment;
  try {
    assignment = await apiGetJson(`/courses/${course.id}/assignments/${args.assignmentId}`);
  } catch (e) {
    if (e instanceof CanvasError && e.status === 404) {
      console.log(
        `\n❌ Assignment ${args.assignmentId} does not exist in course ${course.id} ` +
          `("${course.name}").\n` +
          "   Double-check --assignment-id - it's the number in the assignment's Canvas URL."
      );
      process.exit(1);
    }
    throw e;
  }
  console.log(`📝 Assignment: "${assignment.name}" (id ${assignment.id}, points possible: ${assignment.points_possible})`);

  let submission;
  try {
    submission = await apiGetJson(
      `/courses/${course.id}/assignments/${assignment.id}/submissions/${args.userId}`
    );
  } catch (e) {
    if (e instanceof CanvasError && e.status === 404) {
      console.log(
        `\n❌ No submission found for user ${args.userId} on assignment ${assignment.id} ` +
          `("${assignment.name}") in course ${course.id}.\n` +
          `   Canvas returns this same 'Not Found' whether user ${args.userId} doesn't ` +
          "exist at all, or exists but isn't enrolled in this course - it can't tell you " +
          "which. Double-check --user-id (from the SpeedGrader URL or the course's People " +
          'page) and make sure that user is actually enrolled here.'
      );
      process.exit(1);
    }
    throw e;
  }
  const currentGrade = submission.grade ?? null;
  const currentScore = submission.score ?? null;
  const workflowState = submission.workflow_state ?? 'unknown';
  console.log(
    `👤 User ${args.userId} - current grade: ${JSON.stringify(currentGrade)} ` +
      `(score: ${JSON.stringify(currentScore)}, state: ${workflowState})`
  );
  console.log(`\nProposed grade: ${JSON.stringify(args.grade)}` + (args.comment ? `  comment: ${JSON.stringify(args.comment)}` : ''));

  const log = {
    timestamp: new Date().toISOString(),
    course_id: course.id,
    assignment_id: assignment.id,
    assignment_name: assignment.name,
    user_id: args.userId,
    before: { grade: currentGrade, score: currentScore, workflow_state: workflowState },
    proposed_grade: args.grade,
    comment: args.comment,
  };

  if (!args.apply) {
    log.mode = 'dry_run';
    await writeLog(course.id, assignment.id, args.userId, log);
    console.log('\nDry run complete. No changes were made. Re-run with --apply to submit this grade.');
    return;
  }

  if (!(await confirm(`Submit grade ${JSON.stringify(args.grade)} for user ${args.userId} on "${assignment.name}"?`))) {
    console.log('Aborted. Nothing was submitted.');
    log.mode = 'aborted';
    await writeLog(course.id, assignment.id, args.userId, log);
    return;
  }

  const body = { submission: { posted_grade: args.grade } };
  if (args.comment) body.comment = { text_comment: args.comment };

  try {
    const updated = await apiPutJson(
      `/courses/${course.id}/assignments/${assignment.id}/submissions/${args.userId}`,
      body
    );
    console.log('\n✅ Grade submitted. Canvas now reports:');
    console.log(`   grade: ${JSON.stringify(updated.grade)}  score: ${JSON.stringify(updated.score)}`);
    console.log(`   graded_at: ${updated.graded_at ?? null}  grader_id: ${updated.grader_id ?? null}`);
    log.mode = 'live';
    log.after = {
      grade: updated.grade ?? null,
      score: updated.score ?? null,
      graded_at: updated.graded_at ?? null,
      grader_id: updated.grader_id ?? null,
    };
    log.result = 'success';
  } catch (e) {
    const msg = sanitize(e.message ?? e);
    console.log(`\n❌ Grade submission failed: ${msg}`);
    log.mode = 'live';
    log.result = 'failed';
    log.error = msg;
  } finally {
    await writeLog(course.id, assignment.id, args.userId, log);
  }
}


main().catch((e) => {
  if (e instanceof CanvasError) {
    console.error(`\n❌ Canvas API error:\n\n${sanitize(e.message)}`);
  } else {
    console.error(`\n❌ Error:\n\n${sanitize(e?.message ?? e)}`);
  }
  process.exitCode = 1;
});
