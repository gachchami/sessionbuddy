import { expect, test } from "@playwright/test";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

test.describe("persona and resource boundary", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("role switcher contains personas while access cards contain resource facts", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        user_id: "user",
        email: "owner@example.test",
        display_name: "Owner Speaker",
        profile_complete: true,
        csrf_token: "csrf",
        account_roles: ["organizer", "speaker"],
        active_role: "organizer",
        default_role: "organizer",
        organization_id: organizationId,
        organization_name: "AI Engineer",
        event_id: eventId,
        organization_access: [{
          organization_id: organizationId,
          organization_name: "AI Engineer",
          permissions: ["owner"],
        }],
        event_access: [{
          organization_id: organizationId,
          event_id: eventId,
          event_name: "World's Fair Test",
          assignments: ["speaker"],
        }],
      }),
    }));
    await page.route("**/api/v1/account/profile", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        email: "owner@example.test",
        first_name: "Owner",
        last_name: "Speaker",
        display_name: "Owner Speaker",
        roles: ["organizer", "speaker"],
        has_password: true,
        version: 1,
      }),
    }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ id: organizationId, name: "AI Engineer", status: "active", version: 1 }],
      }),
    }));

    await page.goto("/account");
    await expect(page.getByText("World's Fair Test", { exact: true })).toBeVisible();
    await expect(page.getByText("Owner", { exact: true }).last()).toBeVisible();
    await expect(page.getByText("Speaker", { exact: true }).last()).toBeVisible();
    await expect(page.getByText("Event administrator", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Organization administrator", { exact: true })).toHaveCount(0);

    await page.locator(".sb-account summary").click();
    const switcher = page.locator(".sb-role-switcher");
    await expect(switcher.getByText("Organizer", { exact: true })).toBeVisible();
    await expect(switcher.getByText("Speaker", { exact: true })).toBeVisible();
    await expect(switcher.getByText("Owner", { exact: true })).toHaveCount(0);
    await expect(switcher.getByText("Can manage", { exact: true })).toHaveCount(0);
  });

  test("a retired event grant cannot establish an Organizer workspace", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        user_id: "event-only-user",
        email: "event-only@example.test",
        display_name: "Event Only",
        profile_complete: true,
        csrf_token: "csrf",
        account_roles: ["organizer", "speaker"],
        active_role: "organizer",
        default_role: "organizer",
        organization_id: null,
        organization_name: null,
        event_id: eventId,
        organization_access: [],
        event_access: [{
          organization_id: organizationId,
          event_id: eventId,
          event_name: "World's Fair Test",
          assignments: ["speaker"],
        }],
      }),
    }));
    await page.route("**/api/v1/account/profile", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        email: "event-only@example.test",
        first_name: "Event",
        last_name: "Only",
        display_name: "Event Only",
        roles: ["organizer", "speaker"],
        has_password: true,
        version: 1,
      }),
    }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify({ data: [] }),
    }));
    await page.goto("/account");
    await expect(page).toHaveURL("/account");
    await expect(page.getByText(/no manageable organization/i)).toBeVisible();
    await expect(page.locator(".sb-account summary")).toHaveCount(0);
  });
});
