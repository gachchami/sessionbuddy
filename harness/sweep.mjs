import { chromium } from "@playwright/test";
const b = process.env.SESSIONBUDDY_BASE_URL;
const E = "8efd5ffd-dbfe-4c0d-8170-7b97c39763fe";
const br = await chromium.launch();
const p = await br.newPage({ viewport: { width: 1400, height: 1000 } });
await p.goto(`${b}/sign-in`); await p.waitForTimeout(1200);
const o = p.locator("#replace-session");
if (await o.count() && await o.isVisible()) { await o.click(); await p.waitForTimeout(1800); }
await p.getByRole("button", { name: "Sign in as demo organizer" }).click();
await p.waitForURL("**/admin"); await p.waitForTimeout(2500);
const shots = [
  ["home", "/admin"],
  ["event", `/admin/events/${E}`],
  ["proposals", `/admin/events/${E}/submissions`],
  ["cfp", `/admin/events/${E}/cfp`],
  ["agenda", `/admin/events/${E}/agenda`],
  ["settings", `/admin/events/${E}/settings`],
  ["people", "/admin/people"],
  ["org", "/admin/organization"],
];
for (const [name, path] of shots) {
  await p.goto(b + path); await p.waitForTimeout(3000);
  await p.screenshot({ path: `/workspace/harness/.local/w-${name}.png` });
}
await br.close();
