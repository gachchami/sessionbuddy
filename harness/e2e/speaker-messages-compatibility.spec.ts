import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(root, "speaker_messages.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "speaker_messages.js"), "utf8")}</script></body>`);

test("blocks an active-speaker template for a pending invitee with actionable detail", async ({ page }) => {
  let previewRequests = 0;
  await page.route("**/admin/events/event-a/messages", (route) => route.fulfill({ contentType: "text/html", body: pageHtml }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ csrf_token: "csrf" }) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", time_zone: "UTC" }) }));
  await page.route("**/api/v1/admin/events/event-a/speaker-targets", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [
    { event_speaker_id: "invite-marcus", display_name: "Marcus Okafor", email: "marcus@example.test", company: "", proposal_title: "Invitation pending", selection_status: "invited" },
    { event_speaker_id: "speaker-priya", display_name: "Priya Raman", email: "priya@example.test", company: "", proposal_title: "DevFlow", selection_status: "accepted" },
  ] }) }));
  await page.route("**/api/v1/admin/events/event-a/communications?*", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], next_cursor: null }) }));
  await page.route("**/api/v1/admin/events/event-a/communications/speakers/preview", (route) => {
    previewRequests += 1;
    return route.fulfill({ status: 500 });
  });

  await page.goto("/admin/events/event-a/messages");
  await page.getByText("Marcus Okafor").click();
  await page.locator("#message-template").selectOption("welcome");
  await page.locator("#preview-message").click();

  await expect(page.locator("#status")).toContainText("Marcus Okafor is awaiting acceptance");
  await expect(page.locator("#status")).toContainText("submission.title or portal.link");
  await expect(page.locator("#status")).toContainText("Invitation reminder");
  expect(previewRequests).toBe(0);
});
