import { chromium } from "/eval/node_modules/playwright/index.mjs";

const [targetUrl, hostName, ...personas] = process.argv.slice(2);
if (!targetUrl || !hostName || personas.length === 0) {
  throw new Error("usage: check_sbek_sessions.mjs <url> <host> <persona>...");
}

const browser = await chromium.launch({ headless: true });
let failed = false;
try {
  for (const persona of personas) {
    const sessionPath = `/eval/.auth/${hostName}.${persona}.json`;
    const context = await browser.newContext({ storageState: sessionPath });
    const page = await context.newPage();
    let status = 0;
    let elapsedMs = 0;
    let body = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      const startedAt = Date.now();
      try {
        const response = await page.request.get(`${targetUrl}/api/v1/auth/session`, {
          timeout: 15000,
          headers: { accept: "application/json" },
        });
        elapsedMs = Date.now() - startedAt;
        status = response.status();
        body = status === 200 ? await response.json() : null;
        if (status < 500) break;
      } catch (_) {
        elapsedMs = Date.now() - startedAt;
        status = 0;
      }
    }
    await context.close();
    const organizationRoles = new Set(
      (body?.organization_access ?? []).flatMap((item) => item.roles ?? []),
    );
    const eventRoles = new Set((body?.event_access ?? []).flatMap((item) => item.roles ?? []));
    const correctBoundary = persona === "organizer"
      ? organizationRoles.has("organization_admin")
      : persona === "speaker"
        ? eventRoles.has("speaker") && !organizationRoles.has("organization_admin")
        : persona === "reviewer"
          ? eventRoles.has("evaluator")
            && !eventRoles.has("event_admin")
            && !organizationRoles.has("organization_admin")
          : true;
    const valid = status === 200 && correctBoundary;
    const reason = status !== 200
      ? `status ${status || "timeout"}`
      : correctBoundary
        ? "role boundary confirmed"
        : "authenticated with the wrong role boundary";
    console.log(`${persona}: ${valid ? "valid" : "invalid"} (${reason}, ${elapsedMs} ms)`);
    failed ||= !valid;
  }
} finally {
  await browser.close();
}

if (failed) process.exitCode = 3;
