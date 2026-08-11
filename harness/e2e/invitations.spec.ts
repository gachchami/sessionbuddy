import { expect, test } from "@playwright/test";

// The invitation dialog must never surface the one-time acceptance link.
// That link signs the invitee in, so showing it to the inviting
// administrator would let them accept the invitation as the invitee. The
// link travels only in the invitee's email; the dialog reports delivery.

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
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
          email: "speaker@example.com",
          role: "speaker",
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

    await page.goto(`/admin/events/${eventId}/access`);
    await page.getByRole("button", { name: "Invite someone" }).click();
    await page.getByRole("dialog", { name: "Invite someone" }).getByRole("textbox", { name: "Email address" }).fill("speaker@example.com");
    await page.getByRole("textbox", { name: "Name", exact: true }).fill("Example Speaker");
    await page.getByRole("button", { name: "Send invitation" }).click();

    await expect(page.getByText("speaker@example.com · Speaker assignment · pending")).toBeVisible();
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

    // Reopening presents a consistent form: role back to Speaker with the
    // speaker-details fields visible (they must track the role through
    // reset/close/reopen, not just explicit role changes).
    await page.getByRole("button", { name: "Invite someone" }).click();
    await expect(page.getByRole("combobox", { name: "Access" })).toHaveValue("speaker");
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Access" }).selectOption("evaluator");
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toBeHidden();
    await page.getByRole("button", { name: "Cancel" }).click();
    await page.getByRole("button", { name: "Invite someone" }).click();
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toBeHidden();
    await expect(page.getByRole("combobox", { name: "Access" })).toHaveValue("evaluator");
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
          email: "speaker@example.com",
          role: "speaker",
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
            email: "speaker@example.com",
            role: "speaker",
            status: "pending",
          }],
        }),
      }));

    await page.goto(`/admin/events/${eventId}/access`);
    await page.getByRole("button", { name: "Send again" }).click();

    await expect(page.getByRole("status")).toHaveText("A new link was sent to speaker@example.com.");
    await expect(page.getByText("Acceptance link")).toHaveCount(0);
    expect(await page.content()).not.toContain("SHOULD-NEVER-RENDER");
  });

  test("exact event access can be granted, changed, and revoked", async ({ page }) => {
    let grants = [{ user_id: "owner-user", email: "admin@example.com", permission: "owner", status: "active" }];
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: sessionBody }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Linkless Conf", status: "active", version: 1 }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/members`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/invitations`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/access-grants`, async (route) => {
      if (route.request().method() === "POST") {
        grants.push({ user_id: "grantee-user", email: "grantee@example.com", permission: "view", status: "active" });
        await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(grants[1]) }); return;
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: grants }) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/access-grants/grantee-user`, async (route) => {
      if (route.request().method() === "PATCH") grants[1].permission = "edit";
      if (route.request().method() === "DELETE") grants = grants.slice(0, 1);
      await route.fulfill({ status: route.request().method() === "DELETE" ? 204 : 200, contentType: "application/json", body: route.request().method() === "DELETE" ? "" : JSON.stringify(grants[1]) });
    });

    await page.goto(`/admin/events/${eventId}/access`);
    await page.getByRole("textbox", { name: "Email address" }).fill("grantee@example.com");
    await page.getByRole("button", { name: "Grant access" }).click();
    await expect(page.getByText("grantee@example.com · Can view")).toBeVisible();
    await page.getByRole("combobox", { name: "Permission for grantee@example.com" }).selectOption("edit");
    await expect(page.getByText("grantee@example.com · Can edit")).toBeVisible();
    await page.getByRole("button", { name: "Revoke access" }).click();
    await page.getByRole("button", { name: "Select again to revoke" }).click();
    await expect(page.getByText(/grantee@example.com/)).toHaveCount(0);
  });
});
