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
  let resendRequests = 0;
  let lastPreviewBody: Record<string, unknown> | null = null;
  let previewUnavailable = false;
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
    lastPreviewBody = body;
    if (previewUnavailable) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "temporarily_unavailable", message: "Try again later." } }) });
    if (body.subject.includes("Your invitation") || !body.event_speaker_ids.includes("invite-marcus")) {
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ recipients: [{
        recipient_user_id: body.event_speaker_ids.includes("invite-marcus") ? null : "user-priya",
        recipient_target_id: body.event_speaker_ids[0],
        recipient_state: body.event_speaker_ids.includes("invite-marcus") ? "invited" : "active",
        selection_status: body.event_speaker_ids.includes("invite-marcus") ? "invited" : "accepted",
        display_name: body.event_speaker_ids.includes("invite-marcus") ? "Marcus Okafor" : "Priya Raman",
        email: body.event_speaker_ids.includes("invite-marcus") ? "marcus@example.test" : "priya@example.test",
        subject: body.subject,
        html_body: "<p>Message preview.</p>",
      }] }) });
    }
    return route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ error: {
        code: "validation_failed",
        message: "This message is not ready for 1 selected recipient: Marcus Okafor.",
        metadata: { details: [{
          recipient_target_id: "invite-marcus",
          display_name: "Marcus Okafor",
          recipient_state: "invited",
          reason_codes: ["membership_pending"],
          fields: [
            { name: "portal.link", label: "speaker portal link" },
            { name: "submission.title", label: "proposal title" },
          ],
        }] },
      } }),
    });
  });
  await page.route("**/api/v1/admin/events/event-a/invitations/invite-marcus/resend", (route) => {
    resendRequests += 1;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "invite-marcus", access_url: "https://example.test/invite" }) });
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
  const compatibility = page.locator("#message-compatibility");
  await expect(compatibility).toContainText("1 selected recipient cannot receive this message yet");
  await expect(compatibility).toContainText("Invitation pending");
  await expect(compatibility).toContainText("Portal and proposal details become available after acceptance");
  await compatibility.getByText("Technical details").click();
  await expect(compatibility).toContainText("Unavailable fields: portal.link, submission.title");
  await expect(compatibility).toContainText("Marcus Okafor");
  await expect(compatibility.getByRole("button", { name: "Send to active speakers only" })).toBeVisible();
  await expect(compatibility.getByRole("button", { name: "Use invitation reminder" })).toBeVisible();
  await expect(compatibility.getByRole("button", { name: "Resend 1 invitation" })).toBeVisible();
  await expect(page.locator("#recipient-count")).toHaveText("1 selected · 1 invitation pending");
  expect(previewRequests).toBeGreaterThanOrEqual(1);

  await page.locator("#preview-message").click();
  await expect(compatibility).toBeFocused();
  await expect(compatibility).toBeInViewport();

  await compatibility.getByRole("button", { name: "Resend 1 invitation" }).click();
  await expect(page.locator("#status")).toContainText("1 invitation sent again");
  expect(resendRequests).toBe(1);

  await compatibility.getByRole("button", { name: "Use invitation reminder" }).click();
  await expect(compatibility).toBeHidden();
  await page.locator("#message-template").selectOption("welcome");
  await expect(compatibility).toBeVisible();

  await page.getByText("Priya Raman").click();
  await expect(page.locator("#recipient-count")).toHaveText("2 selected · 1 invitation pending");
  await compatibility.getByRole("button", { name: "Send to active speakers only" }).click();
  await expect(page.locator("#recipient-count")).toHaveText("1 selected");
  await expect(compatibility).toBeHidden();
  expect(lastPreviewBody).toMatchObject({
    event_speaker_ids: ["speaker-priya"],
    excluded_recipient_ids: ["invite-marcus"],
    exclusion_reason: "membership_pending",
  });

  // The invitation-safe template can still preview normally after resolving
  // the audience incompatibility.
  await page.locator("#message-template").selectOption("invitation");
  await expect(composeError).toBeEmpty();
  await page.locator("#preview-message").click();
  await expect(page.locator("#message-preview")).toBeVisible();
  await expect(page.locator("#status")).toContainText("Preview ready for 1 recipient");
  expect(previewRequests).toBeGreaterThanOrEqual(3);

  previewUnavailable = true;
  await page.locator("#message-template").selectOption("deadline");
  await expect(compatibility).toContainText("Automatic recipient check is unavailable");
});
