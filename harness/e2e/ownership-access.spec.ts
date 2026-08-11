import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const accessHtml = readFileSync(resolve(staticRoot, "access_admin.html"), "utf8");
const accessJavaScript = readFileSync(resolve(staticRoot, "access_admin.js"), "utf8");
const apiClientJavaScript = readFileSync(resolve(staticRoot, "api_client.js"), "utf8");
const appShellCss = readFileSync(resolve(staticRoot, "app_shell.css"), "utf8");
const appShellJavaScript = readFileSync(resolve(staticRoot, "app_shell.js"), "utf8");
const productCss = readFileSync(resolve(staticRoot, "product.css"), "utf8");

async function serveAccessPage(page: import("@playwright/test").Page, eventId: string) {
  await page.route(new RegExp(`/admin/events/${eventId}/access(?:\\?.*)?$`), (route) => route.fulfill({
    contentType: "text/html",
    body: accessHtml,
  }));
  await page.route("**/product/assets/product.css*", (route) => route.fulfill({ contentType: "text/css", body: productCss }));
  await page.route("**/app-shell/assets/app-shell.css*", (route) => route.fulfill({ contentType: "text/css", body: appShellCss }));
  await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({ contentType: "text/javascript", body: apiClientJavaScript }));
  await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({ contentType: "text/javascript", body: appShellJavaScript }));
  await page.route("**/admin/access/assets/access.js*", (route) => route.fulfill({ contentType: "text/javascript", body: accessJavaScript }));
}

function sessionFor(
  eventId: string,
  organizationId: string,
  organizationPermission: "owner" | "manage",
) {
  return {
    authenticated: true,
    user_id: "current-user",
    email: "current@example.test",
    display_name: "Current User",
    profile_complete: true,
    csrf_token: "browser-test-csrf",
    account_roles: ["organizer"],
    active_role: "organizer",
    default_role: "organizer",
    organization_access: [{
      organization_id: organizationId,
      organization_name: "Example Events",
      permissions: [organizationPermission],
    }],
    event_access: [{
      organization_id: organizationId,
      event_id: eventId,
      event_name: "Example Conference",
      permissions: ["manage"],
      assignments: [],
    }],
  };
}

async function mockAccessData(
  page: import("@playwright/test").Page,
  eventId: string,
  organizationId: string,
  session: ReturnType<typeof sessionFor>,
  ownerUserId = "previous-owner",
) {
  let owner = { user_id: ownerUserId, email: "previous-owner@example.test", permission: "owner", status: "active" };
  let transferBody: Record<string, unknown> | null = null;
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
  await page.route(new RegExp(`/api/v1/admin/events/${eventId}$`), (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ id: eventId, organization_id: organizationId, name: "Example Conference" }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/invitations`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/members`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/access-grants`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [owner] }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/ownership-transfers`, (route) => {
    transferBody = route.request().postDataJSON() as Record<string, unknown>;
    owner = {
      user_id: "new-owner",
      email: String(transferBody.email),
      permission: "owner",
      status: "active",
    };
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        transfer_id: "transfer-id",
        event_id: eventId,
        previous_owner_user_id: ownerUserId,
        new_owner_user_id: "new-owner",
        previous_owner_permission: transferBody.grant_previous_owner_manage ? "manage" : null,
        transferred_at_ms: 1,
      }),
    });
  });
  return { transferBody: () => transferBody };
}

test.describe("event ownership recovery", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("organization owner reviews scope before explicitly confirming transfer", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const session = sessionFor(eventId, organizationId, "owner");
    await serveAccessPage(page, eventId);
    const transfer = await mockAccessData(page, eventId, organizationId, session);

    await page.goto(`/admin/events/${eventId}/access`);

    await expect(page.getByRole("heading", { name: "Event ownership" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Event ownership" })
      .getByText("previous-owner@example.test", { exact: true })).toBeVisible();
    await expect(page.getByText(/does not delete, move, archive, or rewrite sessions/)).toBeVisible();
    await page.getByLabel("New owner’s account email").fill("new-owner@example.test");
    await page.getByLabel("Reason").fill("The former owner left the events team.");
    await page.getByLabel("Keep the previous owner as Can manage after transfer").check();
    await page.getByRole("button", { name: "Review ownership transfer" }).click();

    expect(transfer.transferBody()).toBeNull();
    const confirmation = page.getByRole("alert");
    await expect(confirmation).toBeFocused();
    await expect(confirmation).toContainText("new-owner@example.test will own this event");
    await expect(confirmation).toContainText("previous-owner@example.test will keep Can manage access");

    await page.getByRole("button", { name: "Confirm transfer" }).click();

    await expect(page.getByRole("status").first()).toHaveText(
      "Event ownership transferred to new-owner@example.test. Event content was not changed.",
    );
    expect(transfer.transferBody()).toEqual({
      email: "new-owner@example.test",
      reason: "The former owner left the events team.",
      grant_previous_owner_manage: true,
    });
  });

  test("organization manage grant alone cannot see ownership recovery", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const session = sessionFor(eventId, organizationId, "manage");
    await serveAccessPage(page, eventId);
    await mockAccessData(page, eventId, organizationId, session);

    await page.goto(`/admin/events/${eventId}/access`);

    await expect(page.getByRole("heading", { name: "Event ownership" })).toBeHidden();
    await expect(page.getByRole("button", { name: "Review ownership transfer" })).toBeHidden();
  });

  test("exact event owner can see transfer even without organization ownership", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const session = sessionFor(eventId, organizationId, "manage");
    await serveAccessPage(page, eventId);
    await mockAccessData(page, eventId, organizationId, session, session.user_id);

    await page.goto(`/admin/events/${eventId}/access`);

    await expect(page.getByRole("heading", { name: "Event ownership" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Event ownership" })
      .getByText("previous-owner@example.test", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Review ownership transfer" })).toBeVisible();
  });
});
