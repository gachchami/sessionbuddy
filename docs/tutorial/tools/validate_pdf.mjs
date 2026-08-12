import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const pdf = path.resolve(process.argv[2] || "output/pdf/sessionbuddy-three-role-field-guide-design-draft.pdf");
const errors = [];
if (!fs.existsSync(pdf)) {
  console.error(`PDF not found: ${pdf}`);
  process.exit(1);
}

const bytes = fs.readFileSync(pdf);
const source = bytes.toString("latin1");
for (const token of ["/MarkInfo", "/StructTreeRoot", "/Outlines"]) {
  if (!source.includes(token)) errors.push(`PDF missing ${token}`);
}
const linkCount = (source.match(/\/Subtype\s*\/Link\b/g) || []).length;
if (linkCount < 1) errors.push("PDF contains no link annotations");

const pdfinfo = process.env.SESSIONBUDDY_PDFINFO || "pdfinfo";
const info = spawnSync(pdfinfo, [pdf], { encoding: "utf8" });
if (info.status !== 0) errors.push(`pdfinfo failed; set SESSIONBUDDY_PDFINFO (${info.error?.message || info.stderr?.trim() || "unknown error"})`);
else {
  if (!/^Tagged:\s+yes$/mi.test(info.stdout)) errors.push("pdfinfo does not report Tagged: yes");
  if (!/^Pages:\s+[1-9]\d*$/mi.test(info.stdout)) errors.push("pdfinfo did not report a positive page count");
}

const pdftotext = process.env.SESSIONBUDDY_PDFTOTEXT || "pdftotext";
const text = spawnSync(pdftotext, ["-layout", pdf, "-"], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
if (text.status !== 0) errors.push(`pdftotext failed; set SESSIONBUDDY_PDFTOTEXT (${text.error?.message || text.stderr?.trim() || "unknown error"})`);
else {
  const pages = text.stdout.split("\f").filter((page) => page.trim());
  if (!pages.length || pages.some((page) => page.trim().length < 20)) errors.push("PDF text extraction produced an empty or near-empty page");
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`PDF validated: structure, outlines, ${linkCount} links, and extractable text.`);
