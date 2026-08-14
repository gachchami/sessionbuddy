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
    const body = route.request().postDataJSON();
    if (body.subject.includes("Your invitation")) {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ recipients: [{
        recipient_user_id: null,
        recipient_target_id: "invite-marcus",
        recipient_state: "invited",
        selection_status: "invited",
        display_name: "Marcus Okafor",
        email: "marcus@example.test",
        subject: "Your invitation to DevFlow",
        html_body: "<p>Your invitation is waiting.</p>",
      }] }) });
    }
    return route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ error: {
        code: "validation_failed",
        message: "1 invited recipient cannot receive this template because it uses portal.link and submission.title: Marcus Okafor. Choose an invitation-safe template or remove this recipient.",
      } }),
    });
  });

  await page.goto("/admin/events/event-a/messages");
  const composeError = page.locator("#message-compose-error");

  // The sibling no-op path uses the same local, focusable feedback without
  // sending a request or relying on the off-screen page status.
  await page.locator("#preview-message").click();
  await expect(composeError).toHaveText("Select at least one recipient.");
  await expect(composeError).toBeFocused();
  expect(previewRequests).toBe(0);

  await page.getByText("Marcus Okafor").click();
  await expect(composeError).toBeEmpty();

  // Required fields keep native browser validation and focus rather than
  // being replaced by the Compose alert.
  await page.locator('input[name="subject"]').fill("");
  await page.locator("#preview-message").click();
  await expect(page.locator('input[name="subject"]')).toBeFocused();
  await expect(composeError).toBeEmpty();
  expect(previewRequests).toBe(0);

  await page.locator("#message-template").selectOption("welcome");
  await page.locator("#preview-message").click();

  await expect(composeError).toContainText("1 invited recipient cannot receive this template");
  await expect(composeError).toContainText("portal.link and submission.title");
  await expect(composeError).toContainText("Marcus Okafor");
  await expect(composeError).toBeFocused();
  await expect(composeError).toBeInViewport();
  expect(previewRequests).toBe(1);

  // A second failure replaces the same message instead of appending another.
  await page.locator("#preview-message").click();
  await expect(composeError).toHaveText(/^[^\n]*1 invited recipient[^\n]*$/);
  expect(previewRequests).toBe(2);

  // Changing the template clears the focused error without dropping focus to
  // body, and the invitation-safe request can then preview normally.
  await page.locator("#message-template").selectOption("invitation");
  await expect(composeError).toBeEmpty();
  await expect(page.locator("#preview-message")).toBeFocused();
  await page.locator("#preview-message").click();
  await expect(page.locator("#message-preview")).toBeVisible();
  await expect(page.locator("#status")).toContainText("Preview ready for 1 recipient");
  expect(previewRequests).toBe(3);
});
