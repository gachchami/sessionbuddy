import path from "node:path";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const tutorialRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(tutorialRoot, "../..");
const require = createRequire(path.join(repoRoot, "harness/package.json"));
const moduleName = process.env.SESSIONBUDDY_PLAYWRIGHT_MODULE || "@playwright/test";
const { chromium } = require(moduleName);
const output = path.resolve(
  process.env.SESSIONBUDDY_TUTORIAL_OUTPUT ||
  path.join(repoRoot, "output/pdf/sessionbuddy-three-role-field-guide-design-draft.pdf")
);
fs.mkdirSync(path.dirname(output), { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(path.join(tutorialRoot, "index.html")).href, { waitUntil: "networkidle" });
await page.emulateMedia({ media: "print" });
await page.pdf({
  path: output,
  format: "A4",
  printBackground: true,
  preferCSSPageSize: true,
  displayHeaderFooter: true,
  headerTemplate: '<div style="width:100%;font:8px Arial;color:#6c7588;padding:0 14mm"><span>SESSIONBUDDY · THREE-ROLE FIELD GUIDE</span></div>',
  footerTemplate: '<div style="width:100%;font:8px Arial;color:#6c7588;padding:0 14mm;display:flex;justify-content:space-between"><span>Revision 859bfbc · 18 August 2026</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
  margin: { top: "18mm", right: "13mm", bottom: "18mm", left: "13mm" }
  ,tagged: true
  ,outline: true
});
await browser.close();
const validation = spawnSync(process.execPath, [path.join(tutorialRoot, "tools/validate_pdf.mjs"), output], {
  stdio: "inherit",
  env: process.env,
});
if (validation.status !== 0) process.exit(validation.status ?? 1);
console.log(output);
