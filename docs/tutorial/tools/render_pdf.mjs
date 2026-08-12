import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

const tutorialRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(tutorialRoot, "../..");
const pdf = path.resolve(process.argv[2] || path.join(repoRoot, "output/pdf/sessionbuddy-three-role-field-guide-design-draft.pdf"));
const output = path.resolve(process.argv[3] || path.join(repoRoot, "tmp/pdfs/sessionbuddy-tutorial"));
const prefix = path.join(output, "page");
fs.mkdirSync(output, { recursive: true });
for (const file of fs.readdirSync(output)) if (/^(page-\d+\.png|contact-sheet\.(html|png))$/.test(file)) fs.rmSync(path.join(output, file));

const renderer = process.env.SESSIONBUDDY_PDFTOPPM || "pdftoppm";
const rendered = spawnSync(renderer, ["-png", "-r", "110", pdf, prefix], { stdio: "inherit" });
if (rendered.status !== 0) {
  console.error(`pdftoppm failed; set SESSIONBUDDY_PDFTOPPM to a Poppler pdftoppm binary.`);
  process.exit(rendered.status ?? 1);
}
const pages = fs.readdirSync(output).filter((file) => /^page-\d+\.png$/.test(file)).sort((a, b) => Number(a.match(/\d+/)[0]) - Number(b.match(/\d+/)[0]));
if (!pages.length) throw new Error("No PDF pages were rendered");
const cards = pages.map((file, index) => `<figure><img src="${file}" alt="Rendered PDF page ${index + 1}"><figcaption>Page ${index + 1}</figcaption></figure>`).join("");
const html = `<!doctype html><meta charset="utf-8"><title>SessionBuddy tutorial contact sheet</title><style>body{margin:0;padding:24px;background:#e8ebf2;font:14px system-ui;color:#172033}.grid{display:grid;grid-template-columns:repeat(4,260px);gap:24px}figure{margin:0}img{display:block;width:260px;height:auto;background:white;box-shadow:0 2px 8px #0002}figcaption{padding-top:6px}</style><div class="grid">${cards}</div>`;
const contactHtml = path.join(output, "contact-sheet.html");
fs.writeFileSync(contactHtml, html);

const require = createRequire(path.join(repoRoot, "harness/package.json"));
const { chromium } = require(process.env.SESSIONBUDDY_PLAYWRIGHT_MODULE || "@playwright/test");
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1160, height: 900 }, deviceScaleFactor: 1 });
await page.goto(pathToFileURL(contactHtml).href, { waitUntil: "networkidle" });
await page.screenshot({ path: path.join(output, "contact-sheet.png"), fullPage: true });
await browser.close();
console.log(`Rendered ${pages.length} pages to ${output}`);
