import fs from "node:fs";
import path from "node:path";

const [evalRoot, targetUrl, ...runArguments] = process.argv.slice(2);
if (!evalRoot || !targetUrl) {
  throw new Error("usage: check_sbek_config.mjs <eval-root> <target-url> [run flags...]");
}

let configName = "evalconfig.json";
for (let index = 0; index < runArguments.length; index += 1) {
  if (runArguments[index] === "--config") {
    configName = runArguments[index + 1] ?? "";
    break;
  }
}
if (!configName) throw new Error("--config requires a file path");

const configPath = path.resolve(evalRoot, configName);
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
const errors = [];
const startingPersonas = ["organizer", "speaker", "reviewer"];
const emailPersonas = [...startingPersonas, "speaker2"];

for (const persona of emailPersonas) {
  const email = String(config.personaEmails?.[persona] ?? "").trim();
  if (!email || /replace-me|example\.invalid/i.test(email)) {
    errors.push(`${persona} needs an inbox address you control in personaEmails`);
  }
}

for (const persona of startingPersonas) {
  const credential = config.credentials?.[persona];
  const email = String(credential?.email ?? "").trim();
  const password = String(credential?.password ?? "");
  if (!email || !password || /replace[-_ ]?(me|with)/i.test(password)) {
    errors.push(`${persona} needs both email and password credentials for identity switches`);
    continue;
  }
  if (email !== config.personaEmails?.[persona]) {
    errors.push(`${persona} credential email must match personaEmails`);
  }
}

if (config.url && new URL(config.url).origin !== new URL(targetUrl).origin) {
  errors.push("evalconfig.json URL does not match SBEK_TARGET_URL");
}

if (errors.length) {
  console.error("Eval configuration is not ready:");
  for (const error of errors) console.error(`- ${error}`);
  process.exitCode = 2;
} else {
  console.log("Eval configuration ready: organizer, speaker, reviewer, and speaker2 email data present.");
}
