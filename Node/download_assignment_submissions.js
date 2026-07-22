#!/usr/bin/env node
/**
 * Canvas Assignment Submission Downloader (proof of concept) - Node.js port of
 * ../Python/download_assignment_submissions.py. Download the actual work
 * students submitted for ONE assignment, matched by Canvas ID.
 *
 * This is different from download_course_json.py, which exports assignment
 * METADATA (name, description, due date, points) for every assignment in the
 * course but explicitly skips file contents. This script pulls the submitted
 * work itself for a single assignment:
 *
 *   - online_upload      -> downloads every attached file
 *   - online_text_entry  -> saves the submitted text as an .html file
 *   - online_url         -> recorded in the manifest (nothing to download)
 *   - anything else (media_recording, online_quiz, discussion_topic, or no
 *     submission yet) -> recorded in the manifest only, not downloaded
 *
 * Output: Node/downloads/assignment_<assignment_id>/
 *   - one subfolder per submitter: user_<user_id>/
 *   - manifest.json summarizing what was found for every submitter
 *
 * Read-only: this never writes to Canvas, so there's no --apply flag. But the
 * files it downloads are real student work (a FERPA-protected education
 * record once run against real enrollments, not just the Test Student) -
 * downloads/ is gitignored, and nothing here prints, commits, or transmits
 * that content anywhere beyond your local disk.
 *
 * Unlike the Python version this has no third-party dependencies: it uses
 * Node's built-in global fetch (Node >= 18) and reads .env itself, so it runs
 * with just `node download_assignment_submissions.js` - no install step.
 *
 * Usage:
 *     node download_assignment_submissions.js
 *         # lists the course's assignments and asks you to pick one, then
 *         # downloads every submitter's work for it
 *
 *     node download_assignment_submissions.js --assignment-id 8846
 *         # skips the picker, downloads every submitter's work for that
 *         # assignment directly
 *
 *     node download_assignment_submissions.js --assignment-id 8846 --user-id 1494
 *         # downloads just one submitter's work (e.g. the Test Student)
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
const UNSAFE_FILENAME_RE = /[^A-Za-z0-9_.\-]+/g;


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

/** Parse the rel="next" URL out of a Canvas Link header, or null. */
function parseNextLink(linkHeader) {
  if (!linkHeader) return null;
  for (const part of linkHeader.split(',')) {
    const m = part.match(/<([^>]+)>\s*;\s*rel="next"/);
    if (m) return m[1];
  }
  return null;
}

/**
 * GET every page of a paginated Canvas list endpoint, following the Link
 * header's rel="next" - the bookkeeping canvasapi's PaginatedList did for free.
 */
async function apiGetAll(pathAndQuery) {
  const sep = pathAndQuery.includes('?') ? '&' : '?';
  let url = `${API_BASE}${pathAndQuery}${sep}per_page=100`;
  const results = [];
  while (url) {
    const res = await fetch(url, { headers: authHeaders });
    if (!res.ok) throw await canvasError(res);
    const page = await res.json();
    results.push(...page);
    url = parseNextLink(res.headers.get('link'));
  }
  return results;
}

/**
 * True if this assignment shell is actually a quiz (classic or New Quizzes) or
 * a graded discussion/forum, per CLAUDE.md's note that both get exposed through
 * the assignments endpoint alongside plain assignments.
 *
 * submission_types alone can't tell New Quizzes apart from some other
 * external-tool assignment, so that case also checks the LTI launch URL for
 * Instructure's quiz-lti domain (verified against this course's real quizzes).
 */
function isQuizOrForum(assignment) {
  const submissionTypes = assignment.submission_types || [];
  if (submissionTypes.includes('discussion_topic')) return true;
  if (assignment.quiz_id != null) return true;
  if (assignment.is_quiz_assignment) return true;
  if (submissionTypes.includes('external_tool')) {
    const tool = assignment.external_tool_tag_attributes || {};
    if ((tool.url || '').includes('quiz-lti')) return true;
  }
  return false;
}

/** Collapse a Canvas-provided filename to a safe, flat basename. */
function safeFilename(name) {
  name = path.basename(name || 'file');
  name = name.replace(UNSAFE_FILENAME_RE, '_');
  return name || 'file';
}

// A single shared stdin prompt, opened lazily and closed once we're done
// asking (so it doesn't hold the event loop open during downloads).
let rl = null;
function ask(question) {
  if (!rl) rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => rl.question(question, resolve));
}
function closePrompt() {
  if (rl) {
    rl.close();
    rl = null;
  }
}

/**
 * List the course's assignments and ask the user to pick one, or cancel.
 * Returns null if the user cancels.
 */
async function chooseAssignment(course) {
  const assignments = (await apiGetAll(`/courses/${course.id}/assignments`)).filter(
    (a) => !isQuizOrForum(a)
  );
  if (assignments.length === 0) {
    throw new Error(`No plain assignments found in course ${course.id} (only quizzes/forums?).`);
  }

  console.log(`\nAssignments in "${course.name}":`);
  assignments.forEach((a, i) => {
    const due = a.due_at || 'no due date';
    const flag = a.published === false ? '  [unpublished]' : '';
    console.log(`  ${i + 1}. ${a.name}  (id ${a.id}, due ${due})${flag}`);
  });
  console.log('  0. Cancel');

  while (true) {
    const raw = (await ask(`Choose an assignment # (1-${assignments.length}, or 0 to cancel): `)).trim();
    if (raw === '0') return null;
    const n = Number(raw);
    if (Number.isInteger(n) && n >= 1 && n <= assignments.length) return assignments[n - 1];
    console.log('Please enter a valid number from the list, or 0 to cancel.');
  }
}

/** Download a single URL to dest, returning the byte count written. */
async function downloadFile(url, dest) {
  const res = await fetch(url);
  if (!res.ok) throw new CanvasError(res.status, `download failed: ${res.status} ${res.statusText}`);
  const buf = Buffer.from(await res.arrayBuffer());
  await fsp.writeFile(dest, buf);
  return buf.length;
}

/** Download whatever's downloadable for one submission; return a manifest entry. */
async function processSubmission(submission, outDir) {
  const userId = submission.user_id;
  const submissionType = submission.submission_type ?? null;
  const workflowState = submission.workflow_state ?? 'unknown';

  const entry = {
    user_id: userId,
    submission_type: submissionType,
    workflow_state: workflowState,
    submitted_at: submission.submitted_at ?? null,
    files: [],
    text_saved: false,
    url: null,
    note: null,
  };

  if (workflowState === 'unsubmitted' || submissionType === null) {
    entry.note = 'No submission yet.';
    console.log(`  ➖ user ${userId}: no submission yet`);
    return entry;
  }

  const userDir = path.join(outDir, `user_${userId}`);

  if (submissionType === 'online_upload') {
    const attachments = submission.attachments || [];
    if (attachments.length === 0) {
      entry.note = 'online_upload with no attachments.';
      console.log(`  ⚠️  user ${userId}: online_upload but no attachments found`);
      return entry;
    }
    await fsp.mkdir(userDir, { recursive: true });
    for (const att of attachments) {
      const filename = safeFilename(att.filename || att.display_name);
      const dest = path.join(userDir, filename);
      const size = await downloadFile(att.url, dest);
      entry.files.push({ filename, size });
      console.log(`  ✅ user ${userId}: downloaded ${filename} (${size} bytes)`);
    }
  } else if (submissionType === 'online_text_entry') {
    const body = submission.body || '';
    await fsp.mkdir(userDir, { recursive: true });
    const dest = path.join(userDir, 'submission.html');
    await fsp.writeFile(dest, body, 'utf-8');
    entry.text_saved = true;
    console.log(`  ✅ user ${userId}: saved text submission to ${path.basename(dest)}`);
  } else if (submissionType === 'online_url') {
    entry.url = submission.url ?? null;
    console.log(`  🔗 user ${userId}: submitted URL recorded in manifest (${entry.url})`);
  } else {
    entry.note = `submission_type '${submissionType}' not downloaded by this POC.`;
    console.log(`  ℹ️  user ${userId}: submission_type '${submissionType}' recorded, not downloaded`);
  }

  return entry;
}

/** Minimal --flag value / --flag=value parser for the two int options. */
function parseArgs(argv) {
  const opts = { assignmentId: null, userId: null };
  const map = { '--assignment-id': 'assignmentId', '--user-id': 'userId' };
  for (let i = 0; i < argv.length; i++) {
    let arg = argv[i];
    let value = null;
    const eq = arg.indexOf('=');
    if (arg.startsWith('--') && eq !== -1) {
      value = arg.slice(eq + 1);
      arg = arg.slice(0, eq);
    }
    if (arg in map) {
      if (value === null) value = argv[++i];
      const n = Number(value);
      if (!Number.isInteger(n)) {
        throw new Error(`${arg} expects an integer Canvas ID, got: ${value}`);
      }
      opts[map[arg]] = n;
    } else if (arg === '-h' || arg === '--help') {
      opts.help = true;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  return opts;
}

const USAGE = `Download submitted work for one Canvas assignment.

Usage:
  node download_assignment_submissions.js [--assignment-id N] [--user-id N]

  --assignment-id N   Canvas assignment ID. If omitted, lists the course's
                      assignments to choose from.
  --user-id N         Limit to one submitter's Canvas user ID (e.g. the Test
                      Student). Default: all submitters.`;


async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(USAGE);
    return;
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
  if (args.assignmentId !== null) {
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
  } else {
    assignment = await chooseAssignment(course);
    if (assignment === null) {
      console.log('Cancelled. Nothing was downloaded.');
      return;
    }
  }
  console.log(`📝 Assignment: "${assignment.name}" (id ${assignment.id})`);

  let submissions;
  if (args.userId !== null) {
    try {
      submissions = [
        await apiGetJson(`/courses/${course.id}/assignments/${assignment.id}/submissions/${args.userId}`),
      ];
    } catch (e) {
      if (e instanceof CanvasError && e.status === 404) {
        console.log(
          `\n❌ No submission record for user ${args.userId} on assignment ` +
            `${assignment.id}. Double-check --user-id and that they're enrolled here.`
        );
        process.exit(1);
      }
      throw e;
    }
  } else {
    submissions = await apiGetAll(`/courses/${course.id}/assignments/${assignment.id}/submissions`);
  }

  // No more prompts past this point - free stdin before the download phase.
  closePrompt();

  const outDir = path.join(DOWNLOAD_ROOT, `assignment_${assignment.id}`);
  await fsp.mkdir(outDir, { recursive: true });

  console.log(`\nProcessing ${submissions.length} submission(s) -> ${outDir}`);
  const manifestEntries = [];
  for (const submission of submissions) {
    try {
      manifestEntries.push(await processSubmission(submission, outDir));
    } catch (e) {
      const uid = submission?.user_id ?? '?';
      console.log(`  ❌ user ${uid}: download failed - ${sanitize(e.message ?? e)}`);
      manifestEntries.push({ user_id: submission?.user_id ?? null, error: sanitize(e.message ?? e) });
    }
  }

  const manifest = {
    downloaded_at: new Date().toISOString(),
    course_id: course.id,
    course_name: course.name,
    assignment_id: assignment.id,
    assignment_name: assignment.name,
    submissions: manifestEntries,
  };
  const manifestPath = path.join(outDir, 'manifest.json');
  await fsp.writeFile(manifestPath, JSON.stringify(manifest, null, 2), 'utf-8');

  const downloadedFiles = manifestEntries.reduce((sum, e) => sum + (e.files?.length || 0), 0);
  console.log(
    `\n🎉 Done. ${manifestEntries.length} submission(s) processed, ${downloadedFiles} file(s) downloaded.`
  );
  console.log(`📝 Manifest: ${manifestPath}`);
}


main()
  .catch((e) => {
    if (e instanceof CanvasError) {
      console.error(`\n❌ Canvas API error:\n\n${sanitize(e.message)}`);
    } else {
      console.error(`\n❌ Error:\n\n${sanitize(e?.message ?? e)}`);
    }
    process.exitCode = 1;
  })
  .finally(closePrompt);
