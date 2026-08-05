#!/usr/bin/env node
/**
 * Cloudflare Pages hosting-limit gate.
 *
 * Walks the EXACT directory that gets uploaded — `dist/` after `npm run build`,
 * which already contains everything Vite copied out of `public/` (the data
 * export and the map geometry) — and fails non-zero if the deployment would
 * breach a Pages free-tier limit:
 *
 *   - more than 20,000 files in one deployment
 *   - any single file larger than 25 MiB
 *
 * Usage:  node scripts/check-limits.mjs [deployDir]     (default: dist)
 *
 * It also prints a markdown block sized for `docs/baselines.md`, because the
 * numbers are a measured budget and a budget nobody can transcribe is not a
 * budget.
 *
 * Design notes, deliberate:
 *
 * - The measured directory is printed as an ABSOLUTE, symlink-resolved path,
 *   together with the raw argv it came from. A run pointed at the wrong tree
 *   (`public/` instead of `dist/`, a stale worktree) then reads as obviously
 *   wrong rather than as a reassuring pass.
 * - Symlinks are FOLLOWED and counted at their target's size. `dist/data/`
 *   may be a real copy today, but the export can legitimately be linked in;
 *   what matters is the bytes that reach the CDN, and `wrangler pages deploy`
 *   uploads file contents, not link structure. The count of links followed is
 *   reported so a surprise there is visible.
 * - An empty or missing directory is a hard failure. It is the single most
 *   likely operator error and the one a "0 files, well under 20,000" pass
 *   would hide.
 * - Everything else that looks off (no `index.html`, no data manifest) is a
 *   loud WARN, not a failure: the gate's contract is the two hosting limits,
 *   and widening it silently would make the gate mean something else.
 *
 * Plain Node ESM, no dependencies — same shape as `scripts/smoke.mjs`.
 */
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const dashboardDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Cloudflare Pages free tier: files per deployment. */
const MAX_FILES = 20_000;
/** Cloudflare Pages free tier: bytes in any single file (25 MiB). */
const MAX_FILE_BYTES = 25 * 1024 * 1024;

/**
 * Pages consumes these as deployment configuration and does not serve or count
 * them as assets. They are still walked and reported — the gate stays
 * conservative and counts them — but they are called out so the number can be
 * reconciled against what the Pages dashboard shows.
 */
const PAGES_METADATA = new Set(["_headers", "_redirects", "_routes.json", "_worker.js"]);

const log = (...parts) => console.log("[check-limits]", ...parts);

function fmtInt(n) {
  return n.toLocaleString("en-US");
}

function fmtBytes(n) {
  if (n < 1024) return `${fmtInt(n)} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${fmtInt(n)} B (${value.toFixed(2)} ${units[unit]})`;
}

/**
 * Nearest-rank percentile over an ascending array: the smallest value at or
 * below which at least `p` of the samples fall. Chosen over interpolation so
 * every reported number is a real file's size.
 */
function percentile(sortedAsc, p) {
  if (sortedAsc.length === 0) return 0;
  const rank = Math.max(1, Math.ceil((p / 100) * sortedAsc.length));
  return sortedAsc[rank - 1];
}

/**
 * Recursively collect every file under `dir`.
 *
 * `withFileTypes` reports symlinks as links; each is resolved with `stat` (not
 * `lstat`) so a linked-in export is measured by the bytes it really carries.
 */
async function walk(dir, root, acc) {
  const entries = await fs.readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    let isDir = entry.isDirectory();
    let isFile = entry.isFile();

    if (entry.isSymbolicLink()) {
      acc.symlinks += 1;
      let target;
      try {
        target = await fs.stat(full);
      } catch {
        acc.brokenSymlinks.push(path.relative(root, full));
        continue;
      }
      isDir = target.isDirectory();
      isFile = target.isFile();
    }

    if (isDir) {
      // Following symlinks means a link back to an ancestor would recurse
      // forever. Real paths are the cycle guard.
      const real = await fs.realpath(full);
      if (acc.seenDirs.has(real)) {
        acc.symlinkCycles.push(path.relative(root, full));
        continue;
      }
      acc.seenDirs.add(real);
      acc.dirs += 1;
      await walk(full, root, acc);
    } else if (isFile) {
      const stat = await fs.stat(full);
      acc.files.push({ rel: path.relative(root, full), bytes: stat.size });
    }
  }
  return acc;
}

async function main() {
  const argvDir = process.argv[2] ?? "dist";
  // Resolved against the dashboard dir, not cwd, so `node dashboard/scripts/...`
  // from the repo root measures the same tree as a run from `dashboard/`.
  const resolved = path.resolve(dashboardDir, argvDir);

  let realDir;
  try {
    realDir = await fs.realpath(resolved);
    const stat = await fs.stat(realDir);
    if (!stat.isDirectory()) throw new Error("not a directory");
  } catch {
    log(`FAIL — deploy directory does not exist or is not a directory: ${resolved}`);
    log(`       (argv was "${argvDir}"; run \`npm run build\` first)`);
    process.exit(1);
  }

  log(`measuring: ${realDir}`);
  log(`argv: "${argvDir}"  →  resolved from ${dashboardDir}`);

  const acc = await walk(realDir, realDir, {
    files: [],
    dirs: 0,
    symlinks: 0,
    brokenSymlinks: [],
    symlinkCycles: [],
    seenDirs: new Set([realDir]),
  });

  const files = acc.files;
  if (files.length === 0) {
    log(`FAIL — measured 0 files under ${realDir}.`);
    log("       An empty deploy directory is an operator error, not a pass.");
    process.exit(1);
  }

  const sizes = files.map((f) => f.bytes).sort((a, b) => a - b);
  const totalBytes = sizes.reduce((a, b) => a + b, 0);
  const largest = files.reduce((a, b) => (b.bytes > a.bytes ? b : a));
  const p50 = percentile(sizes, 50);
  const p95 = percentile(sizes, 95);
  const max = sizes[sizes.length - 1];
  const metadata = files.filter((f) => PAGES_METADATA.has(f.rel));
  const oversized = files.filter((f) => f.bytes > MAX_FILE_BYTES);

  log(
    `walked ${fmtInt(files.length)} files in ${fmtInt(acc.dirs)} directories, ` +
      `${fmtInt(acc.symlinks)} symlink(s) followed`,
  );
  log(`total bytes seen: ${fmtBytes(totalBytes)}`);
  log(`limits: ${fmtInt(MAX_FILES)} files/deployment, ${fmtBytes(MAX_FILE_BYTES)} per file`);

  for (const broken of acc.brokenSymlinks) {
    log(`WARN broken symlink skipped: ${broken}`);
  }
  for (const cycle of acc.symlinkCycles) {
    log(`WARN symlink cycle — directory already walked, not counted twice: ${cycle}`);
  }
  if (!files.some((f) => f.rel === "index.html")) {
    log("WARN no index.html at the root of the measured directory — is this really the build output?");
  }
  if (!files.some((f) => f.rel === path.join("data", "data-manifest.json"))) {
    log("WARN no data/data-manifest.json — the data export is not in this directory.");
  }
  if (metadata.length > 0) {
    log(
      `note: ${metadata.length} Pages configuration file(s) counted here but not ` +
        `served as assets: ${metadata.map((f) => f.rel).join(", ")}`,
    );
  }

  log("");
  log("top 5 files by size:");
  for (const f of [...files].sort((a, b) => b.bytes - a.bytes).slice(0, 5)) {
    console.log(`  ${String(f.bytes).padStart(12)}  ${f.rel}`);
  }

  log("");
  log("--- paste into docs/baselines.md ---");
  // Repo-relative when the directory is inside the repo (that is the readable
  // form for the ledger); absolute otherwise, so an out-of-tree run is obvious.
  const repoRelative = path.relative(path.dirname(dashboardDir), realDir);
  const displayDir = repoRelative.startsWith("..") ? realDir : repoRelative;

  const rows = [
    ["Measured directory", `\`${displayDir}\``],
    ["Files", fmtInt(files.length)],
    ["Total bytes", fmtBytes(totalBytes)],
    ["Largest file", `\`${largest.rel}\` — ${fmtBytes(largest.bytes)}`],
    ["p50 file size", fmtBytes(p50)],
    ["p95 file size", fmtBytes(p95)],
    ["max file size", fmtBytes(max)],
    [
      "File-count budget",
      `${fmtInt(files.length)} / ${fmtInt(MAX_FILES)} (${((files.length / MAX_FILES) * 100).toFixed(2)} %)`,
    ],
    [
      "Per-file budget",
      `${fmtInt(max)} / ${fmtInt(MAX_FILE_BYTES)} B (${((max / MAX_FILE_BYTES) * 100).toFixed(2)} %)`,
    ],
  ];
  console.log("| Metric | Value |");
  console.log("| --- | --- |");
  for (const [k, v] of rows) console.log(`| ${k} | ${v} |`);
  log("--- end ---");
  log("");

  if (oversized.length > 0) {
    log(`FAIL — ${oversized.length} file(s) exceed ${fmtBytes(MAX_FILE_BYTES)}:`);
    for (const f of oversized) console.log(`  ${String(f.bytes).padStart(12)}  ${f.rel}`);
    process.exit(1);
  }
  if (files.length > MAX_FILES) {
    log(`FAIL — ${fmtInt(files.length)} files exceeds the ${fmtInt(MAX_FILES)}-file limit.`);
    process.exit(1);
  }

  log(
    `PASS — ${fmtInt(files.length)} files ≤ ${fmtInt(MAX_FILES)}, ` +
      `largest ${fmtInt(max)} B ≤ ${fmtInt(MAX_FILE_BYTES)} B, ` +
      `${fmtInt(totalBytes)} B total.`,
  );
}

main().catch((err) => {
  console.error(`[check-limits] FAILED: ${err.message}`);
  process.exit(1);
});
