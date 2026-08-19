import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const html = readFileSync(resolve(root, "schedule.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "public_event_masthead.js"), "utf8")}</script><script>${readFileSync(resolve(root, "schedule.js"), "utf8")}</script></body>`);

test("session descriptions expand inline and survive itinerary updates", async ({ page }) => {
  const description = "Build reliable CI with incremental compilation. ".repeat(12);
  await page.route("**/events/event-a/schedule", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/public/events/event-a/schedule", (route) => route.fulfill({
    json: { event: { id: "event-a", name: "Test conference", time_zone: "UTC" }, revision: { id: "published" }, items: [{
      id: "session-a", title: "Reliable CI", description, start_at_ms: 1791972000000,
      end_at_ms: 1791973800000, room_name: "Main stage", track_name: "Platform",
      format_name: "Talk", speaker_names: "Sasha Speaker", speaker_details: "Engineer at Example",
    }] },
  }));
  await page.goto("/events/event-a/schedule");
  const card = page.locator(".schedule-item");
  await expect(card.locator(".session-description")).toContainText("Build reliable CI");
  await expect(card.locator(".session-description")).not.toHaveText(description);
  await card.getByRole("button", { name: "Show more", exact: true }).click();
  await expect(card.locator(".session-description")).toHaveText(description);
  await expect(card.getByRole("button", { name: "Show less", exact: true })).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#session-detail")).not.toBeVisible();
  await card.getByRole("button", { name: "Add Reliable CI to my itinerary", exact: true }).click();
  await expect(card.locator(".session-description")).toHaveText(description);
  await card.getByRole("button", { name: "Show less", exact: true }).click();
  await expect(card.locator(".session-description")).not.toHaveText(description);
  await card.getByRole("button", { name: "Show details", exact: true }).click();
  await expect(page.locator("#session-detail-description")).toHaveText(description);
});
