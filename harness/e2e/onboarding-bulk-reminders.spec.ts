import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(root, "admin_onboarding.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "admin_onboarding.js"), "utf8")}</script></body>`);

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
