import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

function imageSize(file) {
  const data = fs.readFileSync(file);
  if (data.subarray(1, 4).toString() === "PNG") return { width: data.readUInt32BE(16), height: data.readUInt32BE(20) };
  if (data[0] === 0xff && data[1] === 0xd8) {
    let offset = 2;
    while (offset < data.length) {
      if (data[offset] !== 0xff) { offset += 1; continue; }
      const marker = data[offset + 1];
      const length = data.readUInt16BE(offset + 2);
      if ([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker)) {
        return { height: data.readUInt16BE(offset + 5), width: data.readUInt16BE(offset + 7) };
      }
      offset += 2 + length;
    }
  }
  throw new Error(`Unsupported image: ${file}`);
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const shots = JSON.parse(fs.readFileSync(path.join(root, "screenshots.json"), "utf8"));
const coverage = JSON.parse(fs.readFileSync(path.join(root, "coverage.json"), "utf8"));
const errors = [];

for (const required of ["Goal", "You need", "Start", "Finish", "Expected result"]) {
  if (!html.includes(required)) errors.push(`Missing task schema label: ${required}`);
}
for (const role of ["Organizer", "Speaker", "Reviewer"]) {
  if (!html.includes(role)) errors.push(`Missing role: ${role}`);
}
for (const fig of shots.figures) {
  const asset = path.join(root, fig.file);
  if (!fs.existsSync(asset)) errors.push(`Missing screenshot: ${fig.file}`);
  if (!html.includes(fig.file)) errors.push(`Screenshot not used by guide: ${fig.file}`);
  for (const key of ["figure_number", "role", "route", "purpose", "captured_at", "revision", "fixture_version", "width_px", "height_px", "print_width_in", "callouts", "recapture_required"]) {
    if (!Object.hasOwn(fig, key) || fig[key] === null || fig[key] === undefined || fig[key] === "") errors.push(`${fig.id} missing ${key}`);
  }
  if (typeof fig.recapture_required !== "boolean") errors.push(`${fig.id} recapture_required must be boolean`);
  const dimensions = imageSize(asset);
  if (dimensions.width !== fig.width_px || dimensions.height !== fig.height_px) errors.push(`${fig.id} dimensions do not match manifest`);
  if (fig.width_px < shots.capture_contract.minimum_width_px || fig.height_px < shots.capture_contract.minimum_height_px) errors.push(`${fig.id} below minimum dimensions`);
  if ((fig.width_px / fig.print_width_in) < shots.capture_contract.minimum_effective_dpi_at_print_width) errors.push(`${fig.id} below effective print DPI`);
  if (!Array.isArray(fig.callouts) || fig.callouts.length < 2) errors.push(`${fig.id} needs numbered callouts`);
  if (!html.includes(`data-figure-id="${fig.id}"`)) errors.push(`${fig.id} missing annotated figure wrapper`);
  fig.callouts?.forEach((callout, index) => {
    if (callout.number !== index + 1 || !callout.label) errors.push(`${fig.id} invalid callout ${index + 1}`);
  });
}
for (const capability of coverage.capabilities) {
  const id = capability.guide.replace(/^#/, "");
  if (!html.includes(`id="${id}"`)) errors.push(`Coverage anchor missing: ${capability.guide}`);
  for (const authority of capability.authority) {
    if (!fs.existsSync(path.resolve(root, "../..", authority))) errors.push(`Authority file missing: ${authority}`);
  }
  for (const key of ["category", "roles", "disposition", "guide", "authority", "evidence", "verification"]) {
    if (!capability[key] || (Array.isArray(capability[key]) && !capability[key].length)) errors.push(`${capability.id} missing ${key}`);
  }
  if (!coverage.dispositions[capability.disposition]) errors.push(`${capability.id} invalid disposition`);
}
const staleFigures = shots.figures.filter((figure) => figure.recapture_required || figure.revision !== shots.capture_contract.required_revision || figure.fixture_version !== shots.capture_contract.fixture_version);
if (coverage.artifact_status === "RELEASE" && staleFigures.length) errors.push(`Release artifact has ${staleFigures.length} stale figures`);
if (coverage.artifact_status !== "RELEASE" && !html.includes("Design draft")) errors.push("Draft artifact is not visibly labelled");
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
for (const match of html.matchAll(/href="#([^"]+)"/g)) {
  if (!ids.has(match[1])) errors.push(`Broken internal link: #${match[1]}`);
}
for (const match of html.matchAll(/<img\s+[^>]*>/g)) {
  if (!/\balt="[^"]+"/.test(match[0])) errors.push(`Image missing non-empty alt: ${match[0]}`);
}
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`Tutorial verified: ${shots.figures.length} figures, ${coverage.capabilities.length} capabilities, ${ids.size} anchors.`);
