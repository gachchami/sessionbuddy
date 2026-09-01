// Save a SessionBuddy evaluator persona session using credentials from the
// ignored evalconfig.json. Credentials are read inside the browser container
// and are never passed through argv or printed.
import fs from "node:fs";
import { chromium } from "/eval/node_modules/playwright/index.mjs";

const [persona, hostName, targetUrl, mode] = process.argv.slice(2);
if (!persona || !hostName || !targetUrl) {
  throw new Error("usage: sbek_password_auth.mjs <persona> <host> <target-url>");
}

const config = JSON.parse(fs.readFileSync("/eval/evalconfig.json", "utf8"));
if (config.credentialsFile) {
  const source = JSON.parse(fs.readFileSync(`/eval/${config.credentialsFile}`, "utf8"));
  const accounts = Array.isArray(source.accounts)
    ? source.accounts
    : Object.values(source.accounts ?? {});
  const account = accounts.find(
    (candidate) => String(candidate.email ?? "").toLowerCase()
      === String(config.personaEmails?.[persona] ?? "").toLowerCase(),
  );
  if (account) {
    config.credentials = { ...(config.credentials ?? {}), [persona]: account };
  }
}
const credential = config.credentials?.[persona];
if (!credential?.email || !credential?.password) {
  throw new Error(`no password credential configured for ${persona}`);
}

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(new URL("/sign-in", targetUrl).toString(), {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await page.locator('input[name="email"]').fill(credential.email);
  await page.locator('input[name="password"]').fill(credential.password);
  await Promise.all([
    page.waitForURL((url) => url.pathname !== "/sign-in", { timeout: 30000 }),
    page.locator("#password-sign-in").click(),
  ]);

  const session = await page.request.get(new URL("/api/v1/auth/session", targetUrl).toString(), {
    headers: { accept: "application/json" },
    timeout: 15000,
  });
  if (session.status() !== 200) {
    throw new Error(`sign-in did not complete: /api/v1/auth/session returned ${session.status()}`);
  }
  if (mode === "--verify-only") {
    console.log(`${persona}: password login verified; no storage state saved`);
  } else {
    const sessionPath = `/eval/.auth/${hostName}.${persona}.json`;
    await context.storageState({ path: sessionPath });
    console.log(`${persona}: authenticated, storage state saved to ${sessionPath}`);
  }
} finally {
  await browser.close();
}
