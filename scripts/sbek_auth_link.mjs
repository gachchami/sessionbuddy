// Complete a pasted SessionBuddy magic link for an eval persona, including the
// POST-confirm interstitial (/auth/verify now renders a "Continue" page on GET
// so mail scanners cannot burn the single-use token), then save the Playwright
// storage state where the SessionBoard Eval Kit expects it.
//
// Usage (inside the eval-kit Playwright container, repo mounted at /eval):
//   node sbek_auth_link.mjs <persona> <host> <pasted-magic-link-url>
import { chromium } from "/eval/node_modules/playwright/index.mjs";

const [persona, hostName, pastedLink] = process.argv.slice(2);
if (!persona || !hostName || !pastedLink) {
  throw new Error("usage: sbek_auth_link.mjs <persona> <host> <magic-link-url>");
}

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(pastedLink, { waitUntil: "domcontentloaded", timeout: 30000 });

  // The magic-link GET no longer consumes the token; it shows a confirmation
  // page whose single form posts back to /auth/verify. Detect and click it.
  const continueButton = page.locator('form[action^="/auth/verify"] button[type="submit"]');
  if (await continueButton.count()) {
    await Promise.all([
      page.waitForURL((url) => !url.pathname.startsWith("/auth/verify"), { timeout: 30000 }),
      continueButton.first().click(),
    ]);
  }

  const session = await page.request.get(new URL("/api/v1/auth/session", pastedLink).toString(), {
    headers: { accept: "application/json" },
    timeout: 15000,
  });
  if (session.status() !== 200) {
    throw new Error(`sign-in did not complete: /api/v1/auth/session returned ${session.status()}`);
  }
  const sessionPath = `/eval/.auth/${hostName}.${persona}.json`;
  await context.storageState({ path: sessionPath });
  console.log(`${persona}: authenticated, storage state saved to ${sessionPath}`);
} finally {
  await browser.close();
}
