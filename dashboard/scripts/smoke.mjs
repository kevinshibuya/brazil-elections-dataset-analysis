#!/usr/bin/env node
/**
 * Self-orchestrating headless browser smoke.
 *
 * Builds the app, boots `vite preview`, drives Playwright chromium against it,
 * and asserts the shell actually rendered with zero console errors.
 * Exits non-zero on any failure. Requires only `npm install` beforehand
 * (plus the chromium browser binary: `npx playwright install chromium`).
 */
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "@playwright/test";

const dashboardDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PREVIEW_PORT = Number(process.env.SMOKE_PORT ?? 4173);
const PREVIEW_URL = `http://localhost:${PREVIEW_PORT}/`;
const STARTUP_TIMEOUT_MS = 60_000;

/** Every detector page, by route id. Mirrors `src/pages/Detector/registry.ts`. */
const DETECTOR_IDS = [
  "composite",
  "extreme_dominance",
  "split_ticket",
  "turnout",
  "t1_arithmetic",
  "t1_presence",
  "fingerprint",
  "digits",
  "urna_model",
];

/**
 * The routes the smoke drives, and the marker each one must render.
 *
 * Every detector page is smoked, not one of them: they are one component over
 * a registry, so a broken block or a missing payload fails for a single id
 * while the other eight stay green — exactly the failure a one-route smoke is
 * blind to.
 */
const ROUTES = [
  { path: "/", expect: "[data-testid='choropleth']" },
  { path: "/detectors", expect: null },
  // The methodology page parses its markdown at import time and THROWS if a
  // locale file's section markers break the contract — so this marker is the
  // one route check that proves the shipped prose still parses.
  { path: "/methodology", expect: "[data-testid='methodology-toc']" },
  ...DETECTOR_IDS.map((id) => ({
    path: `/detectors/${id}`,
    expect: "[data-testid='detector-results']",
  })),
  // A typo'd id is a route a reader can reach: it must render the "no such
  // indicator" page, and specifically NOT a results region.
  { path: "/detectors/not-a-detector", expect: null },
];

function run(command, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: dashboardDir,
      stdio: "inherit",
      shell: false,
      ...opts,
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} ${args.join(" ")} exited with code ${code}`));
    });
  });
}

async function waitForServer(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(2000) });
      if (res.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`Preview server did not become ready at ${url} within ${timeoutMs}ms`);
}

async function stopPreview(preview) {
  if (!preview || preview.exitCode !== null) return;
  const exited = new Promise((resolve) => preview.once("exit", resolve));
  preview.kill("SIGTERM");
  const timer = setTimeout(() => preview.kill("SIGKILL"), 5000);
  await exited;
  clearTimeout(timer);
}

async function main() {
  console.log("[smoke] building…");
  await run("npm", ["run", "build"]);

  console.log(`[smoke] starting vite preview on :${PREVIEW_PORT}…`);
  const preview = spawn(
    "npx",
    ["vite", "preview", "--port", String(PREVIEW_PORT), "--strictPort"],
    { cwd: dashboardDir, stdio: ["ignore", "inherit", "inherit"], shell: false },
  );
  preview.on("error", (err) => {
    console.error("[smoke] failed to start preview:", err);
    process.exitCode = 1;
  });

  let browser;
  try {
    await waitForServer(PREVIEW_URL, STARTUP_TIMEOUT_MS);

    browser = await chromium.launch();
    const page = await browser.newPage();

    /** @type {string[]} */
    const consoleErrors = [];
    let currentRoute = "/";
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(`${currentRoute}: ${msg.text()}`);
    });
    page.on("pageerror", (err) => consoleErrors.push(`${currentRoute}: pageerror: ${err.message}`));
    page.on("requestfailed", (req) => {
      consoleErrors.push(
        `${currentRoute}: requestfailed: ${req.url()} (${req.failure()?.errorText})`,
      );
    });

    for (const route of ROUTES) {
      const url = new URL(route.path.replace(/^\//, ""), PREVIEW_URL).href;
      const before = consoleErrors.length;
      currentRoute = route.path;

      const response = await page.goto(url, { waitUntil: "networkidle" });
      if (!response || !response.ok()) {
        throw new Error(
          `${route.path}: navigation failed — HTTP ${response ? response.status() : "no response"}`,
        );
      }

      await page.waitForSelector("#root > *", { timeout: 10_000 });
      const childCount = await page.evaluate(
        () => document.getElementById("root")?.childElementCount ?? 0,
      );
      if (childCount < 1) throw new Error(`${route.path}: #root rendered no children`);

      // A registry-driven page fails per id, so every route asserts the marker
      // that proves ITS id resolved — an unknown id renders no results region.
      if (route.expect) {
        await page.waitForSelector(route.expect, { timeout: 10_000 });
      }

      const added = consoleErrors.length - before;
      console.log(
        `[smoke]   ${added === 0 ? "OK  " : "FAIL"} ${route.path} — ${childCount} root child(ren), ${added} console error(s)`,
      );
    }

    if (consoleErrors.length > 0) {
      throw new Error(`Console errors detected:\n  - ${consoleErrors.join("\n  - ")}`);
    }

    console.log(`[smoke] OK — ${ROUTES.length} routes, 0 console errors`);
  } finally {
    if (browser) await browser.close();
    await stopPreview(preview);
  }
}

main().catch((err) => {
  console.error(`[smoke] FAILED: ${err.message}`);
  process.exit(1);
});
