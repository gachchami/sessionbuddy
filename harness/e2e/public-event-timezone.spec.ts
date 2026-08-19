import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test.use({ timezoneId: "Asia/Kolkata", locale: "en-US" });

test("public dates follow event timezone and degrade without inventing dates", async ({ page }) => {
  const root = resolve(__dirname, "../../src/sessionbuddy/static");
  const html = `<html><body><section data-public-events></section>${["api_client.js", "app_shell.js"].map((file) => `<script>${readFileSync(resolve(root, file), "utf8")}</script>`).join("")}</body></html>`;
  const boundary = Date.parse("2026-09-30T06:59:00Z"); // Sep 29 23:59 in Los Angeles.
  const base = { id: "event-a", starts_at_ms: boundary, ends_at_ms: boundary + 3600000, time_zone: "America/Los_Angeles", cfp_slug: "call", cfp_state: "open", cfp_boundary_kind: "closes", cfp_boundary_at_ms: boundary, schedule_published: false, speaker_count: 0 };
  await page.route("https://test/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ status: 401, json: {} }));
  await page.route("**/api/v1/public/events", (route) => route.fulfill({ json: { data: [
    { ...base, name: "Open LA" },
    { ...base, name: "Scheduled LA", cfp_state: "scheduled", cfp_boundary_kind: "opens" },
    { ...base, name: "Closed LA", cfp_state: "closed" },
    { ...base, name: "Missing zone", time_zone: undefined },
    { ...base, name: "Invalid zone", time_zone: "not/a-zone", cfp_state: "scheduled" },
    { ...base, name: "Invalid date", starts_at_ms: "bad", cfp_boundary_at_ms: "bad" },
  ] } }));
  await page.goto("https://test/");
  const card = (name: string) => page.locator(".public-event-card").filter({ has: page.getByRole("heading", { name, exact: true }) });
  await expect(card("Open LA").locator(".public-event-state")).toHaveText("Closes Sep 29, 2026");
  await expect(card("Scheduled LA").locator(".public-event-state")).toHaveText("Opens Sep 29, 2026");
  await expect(card("Closed LA").locator(".public-event-state")).toHaveText("Closed Sep 29, 2026");
  await expect(card("Open LA").locator("time")).toHaveText("Sep 29, 2026");
  await expect(card("Missing zone").locator(".public-event-state")).toHaveText("Open");
  await expect(card("Invalid zone").locator(".public-event-state")).toHaveText("Scheduled");
  await expect(card("Missing zone").locator("time")).toHaveText("Date unavailable");
  await expect(card("Invalid zone").locator("time")).toHaveText("Date unavailable");
  await expect(card("Invalid date").locator(".public-event-state")).toHaveText("Open");
  await expect(page.locator(".public-event-card")).toHaveCount(6);
});
