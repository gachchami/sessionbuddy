import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// The invitation dialog must never surface the one-time acceptance link.
// That link signs the invitee in, so showing it to the inviting
// administrator would let them accept the invitation as the invitee. The
// link travels only in the invitee's email; the dialog reports delivery.

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const source = (name: string) => readFileSync(resolve(staticRoot, name), "utf8");
const sessionBody = JSON.stringify({
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  event_id: eventId,
  csrf_token: "browser-test-csrf",
  email: "admin@example.com",
  display_name: "Admin User",
  profile_complete: true,
  account_roles: ["organizer"],
  active_role: "organizer",
  default_role: "organizer",
  organization_access: [{
    organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    organization_name: "Example Organization",
    permissions: ["owner"],
  }],
  event_access: [{
    organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    event_id: eventId,
    event_name: "Example Event",
    permissions: ["owner"],
    assignments: [],
  }],
});

test.describe("invitation dialog", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test.beforeEach(async ({ page }) => {
    await page.route(new RegExp(`/admin/events/${eventId}/reviewers(?:\\?.*)?$`), (route) =>
      route.fulfill({ contentType: "text/html", body: source("access_admin.html") }));
    for (const [pattern, file, contentType] of [
      ["**/product/assets/product.css*", "product.css", "text/css"],
      ["**/app-shell/assets/app-shell.css*", "app_shell.css", "text/css"],
      ["**/app-shell/assets/api-client.js*", "api_client.js", "text/javascript"],
      ["**/app-shell/assets/app-shell.js*", "app_shell.js", "text/javascript"],
      ["**/admin/access/assets/access.js*", "access_admin.js", "text/javascript"],
    ] as const) {
      await page.route(pattern, (route) =>
        route.fulfill({ contentType, body: source(file) }));
    }
  });

  test("no acceptance link is rendered, even if a server returns one", async ({ page }) => {
    let invitations: Array<Record<string, unknown>> = [];
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: sessionBody }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: eventId,
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          name: "Linkless Conf",
          status: "active",
          version: 1,
        }),
      }));
    await page.route(`**/api/v1/admin/events/${eventId}/members`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/access-grants`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/invitations`, async (route) => {
      if (route.request().method() === "POST") {
        invitations = [{
          id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          event_id: eventId,
          email: "reviewer@example.com",
          role: "evaluator",
          status: "pending",
        }];
        // A hostile/stale server response containing accept_url must still
        // never reach the page: the client has no code path to render it.
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            ...invitations[0],
            accept_url: "https://example.test/auth/verify?token=SHOULD-NEVER-RENDER",
          }),
        });
        return;
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: invitations }) });
    });

    await page.goto(`/admin/events/${eventId}/reviewers`);
    await page.getByRole("button", { name: "Invite reviewer" }).click();
    await page.getByRole("dialog", { name: "Invite reviewer" }).getByRole("textbox", { name: "Email address" }).fill("reviewer@example.com");
    await page.getByRole("button", { name: "Send invitation" }).click();

    const invitationRow = page.locator("#reviewer-list [role=row]").filter({
      has: page.getByText("reviewer@example.com", { exact: true }),
    });
    await expect(invitationRow).toContainText("Reviewer");
    await expect(invitationRow).toContainText("pending");
    // A successful send closes the dialog; the sender lands on the updated list.
    await expect(page.locator("#invite-dialog")).not.toHaveAttribute("open", "");
    // The dialog offers no link surface at all.
    await expect(page.getByText("Acceptance link")).toHaveCount(0);
    await expect(page.getByText("Invitation ready")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Copy link" })).toHaveCount(0);
    await expect(page.locator("#invite-result")).toHaveCount(0);
    await expect(page.locator("#invite-url")).toHaveCount(0);
    const pageContent = await page.content();
    expect(pageContent).not.toContain("SHOULD-NEVER-RENDER");

    // Reopening remains reviewer-only; no legacy role selector is exposed.
    await page.getByRole("button", { name: "Invite reviewer" }).click();
    await expect(page.getByRole("combobox", { name: "Access" })).toHaveCount(0);
    await page.getByRole("button", { name: "Cancel" }).click();
  });

  test("resending an invitation reports delivery without exposing a link", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: sessionBody }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: eventId,
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          name: "Linkless Conf",
          status: "active",
          version: 1,
        }),
      }));
    await page.route(`**/api/v1/admin/events/${eventId}/members`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/access-grants`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/invitations/*/resend`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          event_id: eventId,
          email: "reviewer@example.com",
          role: "evaluator",
          status: "pending",
          accept_url: "https://example.test/auth/verify?token=SHOULD-NEVER-RENDER",
        }),
      }));
    await page.route(`**/api/v1/admin/events/${eventId}/invitations`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          data: [{
            id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            event_id: eventId,
            email: "reviewer@example.com",
            role: "evaluator",
            status: "pending",
          }],
        }),
      }));

    await page.goto(`/admin/events/${eventId}/reviewers`);
    await page.getByRole("button", { name: "Send again" }).click();

    await expect(page.locator("#status")).toHaveText("A new invitation was sent to reviewer@example.com.");
    await expect(page.getByText("Acceptance link")).toHaveCount(0);
    expect(await page.content()).not.toContain("SHOULD-NEVER-RENDER");
  });

  test("reviewer page does not expose legacy event access grants", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: sessionBody }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Linkless Conf", status: "active", version: 1 }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/invitations`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.goto(`/admin/events/${eventId}/reviewers`);
    await expect(page.getByRole("heading", { name: "Reviewers", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Grant access" })).toHaveCount(0);
    await expect(page.locator("#grant-list")).toHaveCount(0);
    await expect(page.getByText("Can view", { exact: true })).toHaveCount(0);
  });
});
