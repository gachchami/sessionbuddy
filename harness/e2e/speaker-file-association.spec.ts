import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("file library distinguishes stored proposal association from general speaker files", async ({ page }) => {
  const root = resolve(__dirname, "../../src/sessionbuddy/static");
  const html = readFileSync(resolve(root, "speaker_content.html"), "utf8")
    .replace(/<link[^>]+>/g, "")
    .replace(/<script[^>]+><\/script>/g, "")
    .replace("</body>", ["api_client.js", "speaker_content.js"].map((file) => `<script>${readFileSync(resolve(root, file), "utf8")}</script>`).join("") + "</body>");
  await page.route("**/admin/events/event-a/speaker-content", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { csrf_token: "csrf" } }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ json: { time_zone: "UTC" } }));
  await page.route("**/api/v1/admin/events/event-a/resources", (route) => route.fulfill({ json: { data: [] } }));
  await page.route("**/api/v1/admin/events/event-a/speaker-targets", (route) => route.fulfill({ json: { data: [] } }));
  const base = { event_speaker_id: "speaker-a", speaker_name: "Sasha", kind: "slides", content_type: "application/pdf", byte_size: 100, uploaded_at_ms: 1, uploaded_by: "Speaker", generation: 1, version_count: 1, versions: [] };
  await page.route("**/api/v1/admin/events/event-a/assets", (route) => route.fulfill({ json: { data: [
    { ...base, id: "linked", filename: "linked.pdf", submission_id: "proposal-a", proposal_title: "Reliable pipelines", task_id: "task-a", task_title: "Upload slides" },
    { ...base, id: "general", filename: "general.pdf", submission_id: null, proposal_title: null, task_id: null, task_title: null },
  ] } }));
  await page.goto("https://test/admin/events/event-a/speaker-content");
  const linked = page.locator(".speaker-file-card").filter({ hasText: "linked.pdf" });
  const general = page.locator(".speaker-file-card").filter({ hasText: "general.pdf" });
  await expect(linked).toContainText("Reliable pipelines");
  await expect(linked).toContainText("Upload slides");
  await expect(general).toContainText("General speaker file");
  await expect(general).not.toContainText("Reliable pipelines");
});
