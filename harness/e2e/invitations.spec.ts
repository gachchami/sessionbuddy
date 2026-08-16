import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Authorized invitation managers may copy the newly issued one-time bearer
// link. It appears only in the create/resend result, never in list responses,
// and resend rotates the previous link.

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

  test("shows the named reviewer and newly issued copyable access link", async ({ page }) => {
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
          display_name: "Sam Whitfield",
        }];
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            ...invitations[0],
            access_url: "https://example.test/auth/verify#token=NEW-REVIEWER-LINK",
          }),
        });
        return;
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: invitations }) });
    });

    await page.goto(`/admin/events/${eventId}/reviewers`);
    await page.getByRole("button", { name: "Invite reviewer" }).click();
    await page.getByRole("dialog", { name: "Invite reviewer" }).getByRole("textbox", { name: "Full name" }).fill("Sam Whitfield");
    await page.getByRole("dialog", { name: "Invite reviewer" }).getByRole("textbox", { name: "Email address" }).fill("reviewer@example.com");
    await page.getByRole("button", { name: "Send invitation" }).click();

    const invitationRow = page.locator("#reviewer-list [role=row]").filter({
      has: page.getByText("reviewer@example.com", { exact: true }),
    });
    await expect(invitationRow).toContainText("Sam Whitfield");
    await expect(invitationRow).toContainText("pending");
    // A successful send closes the dialog; the sender lands on the updated list.
    await expect(page.locator("#invite-dialog")).not.toHaveAttribute("open", "");
    await expect(page.getByText("Access link for reviewer@example.com")).toBeVisible();
    await expect(page.getByRole("button", { name: "Copy link" })).toBeVisible();
    await expect(page.locator("#invitation-access-url")).toHaveValue(
      "https://example.test/auth/verify#token=NEW-REVIEWER-LINK",
    );

    // Reopening remains reviewer-only; no legacy role selector is exposed.
    await page.getByRole("button", { name: "Invite reviewer" }).click();
    await expect(page.getByRole("combobox", { name: "Access" })).toHaveCount(0);
    await page.getByRole("button", { name: "Cancel" }).click();
  });

  test("shows an accepted-invitation conflict inside the open dialog", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: sessionBody }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
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
    await page.route(`**/api/v1/admin/events/${eventId}/invitations`, (route) => {
      if (route.request().method() === "POST") {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            error: {
              code: "conflict",
              message: "This invitation has already been accepted",
            },
            request_id: "accepted-invitation",
          }),
        });
      }
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: [] }),
      });
    });

    await page.goto(`/admin/events/${eventId}/reviewers`);
    await page.getByRole("button", { name: "Invite reviewer" }).click();
    const dialog = page.getByRole("dialog", { name: "Invite reviewer" });
    await dialog.getByRole("textbox", { name: "Full name" }).fill("Sam Whitfield");
    await dialog.getByRole("textbox", { name: "Email address" }).fill("reviewer@example.com");
    await dialog.getByRole("button", { name: "Send invitation" }).click();

    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("status")).toContainText(
      "This invitation has already been accepted",
    );
    await expect(dialog.getByRole("status")).toBeFocused();
    await expect(page.locator("#status")).not.toContainText("already been accepted");
  });

  test("resending an invitation rotates and exposes the new copyable link", async ({ page }) => {
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
          access_url: "https://example.test/auth/verify#token=ROTATED-REVIEWER-LINK",
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
    await expect(page.locator("#invitation-access-url")).toHaveValue(
      "https://example.test/auth/verify#token=ROTATED-REVIEWER-LINK",
    );
    await expect(page.getByRole("button", { name: "Copy link" })).toBeVisible();
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
