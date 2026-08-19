import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(root, "admin_onboarding.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "admin_onboarding.js"), "utf8")}</script></body>`);

test("general tasks stay unassigned while linked proposals and pending invitations remain truthful", async ({ page }, testInfo) => {
  await page.route("**/admin/events/event-a/onboarding", (route) => route.fulfill({ contentType: "text/html", body: pageHtml }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ json: { csrf_token: "csrf" } }));
  const base = { event_speaker_id: "speaker-sasha", person_id: "person-sasha", display_name: "Sasha Speaker", due_at_ms: Date.now(), last_activity_at_ms: Date.now(), state: "due_soon" };
  await page.route("**/api/v1/admin/events/event-a/onboarding*", (route) => route.fulfill({ json: {
    event_name: "DevFlow", time_zone: "America/Los_Angeles", generated_at_ms: Date.now(), next_cursor: null,
    summary: { complete: 0, incomplete: 2, overdue: 0, due_soon: 2, awaiting_acceptance: 1 },
    data: [
      { ...base, task_id: "general", task_title: "Upload a headshot", proposal_title: "" },
      { ...base, task_id: "linked", task_title: "Upload session slides", proposal_title: "Reliable CI" },
      { ...base, task_id: "pending", task_title: "Confirm participation", proposal_title: "", event_speaker_id: null, person_id: null, display_name: "Marcus Chen", state: "awaiting_acceptance" },
    ],
  } }));
  await page.goto("/admin/events/event-a/onboarding");
  for (const selector of ["#onboarding-rows > tr", "#onboarding-cards > li"]) {
    const general = page.locator(selector).filter({ hasText: "Upload a headshot" });
    const linked = page.locator(selector).filter({ hasText: "Upload session slides" });
    const pending = page.locator(selector).filter({ hasText: "Marcus Chen" });
    await expect(general).toContainText("General speaker task");
    await expect(general).not.toContainText("Reliable CI");
    await expect(linked).toContainText("Reliable CI");
    await expect(pending).toContainText("General speaker task");
    await expect(pending).toContainText("Awaiting acceptance");
    await expect(pending.getByRole("button", { name: "Send reminder", exact: true })).toHaveCount(0);
  }
  const screenshotPath = testInfo.outputPath("task-association-content.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach("Task association content (unstyled fixture)", { path: screenshotPath, contentType: "image/png" });
  await page.getByRole("button", { name: "Remind loaded outstanding" }).click();
  await expect(page.getByRole("dialog", { name: "Send task reminders?" })).toContainText("email 2 loaded outstanding tasks");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
});

test("bulk reminders confirm the loaded count and retry with stable task keys", async ({ page }) => {
  const keys: Record<string, string[]> = { "task-a": [], "task-b": [] };
  let failTaskB = true;
  await page.route("**/admin/events/event-a/onboarding", (route) => route.fulfill({ contentType: "text/html", body: pageHtml }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ csrf_token: "csrf" }) }));
  await page.route("**/api/v1/admin/events/event-a/onboarding*", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      event_name: "DevFlow", time_zone: "Asia/Kolkata", generated_at_ms: Date.now(), next_cursor: null,
      summary: { complete: 0, incomplete: 2, overdue: 0, due_soon: 2, awaiting_acceptance: 0 },
      data: ["a", "b"].map((suffix) => ({
        task_id: `task-${suffix}`, event_speaker_id: `speaker-${suffix}`, person_id: `person-${suffix}`,
        display_name: suffix === "a" ? "Priya Raman" : "Sam Whitfield", proposal_title: "Talk",
        task_title: "Upload slides", due_at_ms: Date.now(), last_activity_at_ms: Date.now(), state: "due_soon",
      })),
    }),
  }));
  await page.route("**/api/v1/admin/events/event-a/speaker-tasks/*/reminders", async (route) => {
    const taskId = route.request().url().match(/speaker-tasks\/(task-[ab])\//)?.[1] || "";
    keys[taskId].push(route.request().headers()["idempotency-key"]);
    if (taskId === "task-b" && failTaskB) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { message: "Unavailable" } }) });
    return route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ message_id: taskId }) });
  });

  await page.goto("/admin/events/event-a/onboarding");
  await page.getByRole("button", { name: "Remind loaded outstanding" }).click();
  const dialog = page.getByRole("dialog", { name: "Send task reminders?" });
  await expect(dialog).toContainText("email 2 loaded outstanding tasks");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  expect(keys["task-a"]).toEqual([]);

  await page.getByRole("button", { name: "Remind loaded outstanding" }).click();
  await dialog.getByRole("button", { name: "Send reminders" }).click();
  await expect(page.locator("#status")).toContainText("Not queued: Sam Whitfield");
  await expect(page.locator("#onboarding-action-toast")).toContainText("Not queued: Sam Whitfield");

  failTaskB = false;
  await page.getByRole("button", { name: "Remind loaded outstanding" }).click();
  await dialog.getByRole("button", { name: "Send reminders" }).click();
  await expect(page.locator("#status")).toHaveText("2 reminders queued.");
  await expect(page.locator("#onboarding-action-toast")).toContainText("2 reminders queued.");
  expect(keys["task-a"][1]).toBe(keys["task-a"][0]);
  expect(keys["task-b"][1]).toBe(keys["task-b"][0]);
});
