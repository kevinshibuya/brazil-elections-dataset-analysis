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
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });
    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
    page.on("requestfailed", (req) => {
      consoleErrors.push(`requestfailed: ${req.url()} (${req.failure()?.errorText})`);
    });

    const response = await page.goto(PREVIEW_URL, { waitUntil: "networkidle" });
    if (!response || !response.ok()) {
      throw new Error(`Navigation failed: HTTP ${response ? response.status() : "no response"}`);
    }

    await page.waitForSelector("#root > *", { timeout: 10_000 });
    const childCount = await page.evaluate(
      () => document.getElementById("root")?.childElementCount ?? 0,
    );
    if (childCount < 1) {
      throw new Error("#root rendered no children");
    }

    if (consoleErrors.length > 0) {
      throw new Error(`Console errors detected:\n  - ${consoleErrors.join("\n  - ")}`);
    }

    console.log(`[smoke] OK — #root has ${childCount} child element(s), 0 console errors`);
  } finally {
    if (browser) await browser.close();
    await stopPreview(preview);
  }
}

main().catch((err) => {
  console.error(`[smoke] FAILED: ${err.message}`);
  process.exit(1);
});
