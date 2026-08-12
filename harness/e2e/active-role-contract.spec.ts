import { expect, test, type Page } from "@playwright/test";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

async function mockAccount(
  page: Page,
  activeRole: string | null,
  organizerAccess = true,
) {
  const session = {
    authenticated: true,
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    email: "multi-role@example.test",
    display_name: "Multi Role",
    profile_complete: true,
    csrf_token: "browser-test-csrf",
    account_roles: ["organizer", "reviewer", "speaker"],
    active_role: activeRole,
    default_role: "organizer",
    organization_id: organizationId,
    organization_name: "Example Organization",
    event_id: null,
    organization_access: organizerAccess ? [{
      organization_id: organizationId,
      organization_name: "Example Organization",
      permissions: ["owner"],
    }] : [],
    event_access: [],
  };
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
  await page.route("**/api/v1/account/profile", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      email: session.email,
      first_name: "Multi",
      last_name: "Role",
      display_name: session.display_name,
      roles: session.account_roles,
      has_password: true,
      time_zone: "UTC",
      version: 1,
    }),
  }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
}

test.describe("active role session contract", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  for (const activeRole of [null, "event_admin"]) {
    test(`fails closed when active_role is ${activeRole ?? "null"}`, async ({ page }) => {
      await mockAccount(page, activeRole);

      await page.goto("/account");

      const shell = page.locator("header[data-auth-shell]");
      await expect(page).toHaveURL(/\/account$/);
      await expect(shell.getByRole("alert")).toContainText(
        "this session has no valid active role",
      );
      await expect(shell.locator("a")).toHaveCount(0);
      await expect(shell.locator(".sb-account")).toHaveCount(0);
      await expect(shell.locator('[href="/admin"], [href="/reviews"], [href="/speaker"], [href="/account"]')).toHaveCount(0);
    });
  }

  for (const [activeRole, destination] of [
    ["organizer", "/admin"],
    ["reviewer", "/reviews"],
    ["speaker", "/speaker"],
  ] as const) {
    test(`keeps the ${activeRole} shell destination`, async ({ page }) => {
      await mockAccount(page, activeRole);

      await page.goto("/account");

      const shell = page.locator("header[data-auth-shell]");
      await expect(shell.getByRole("alert")).toHaveCount(0);
      if (activeRole !== "speaker") {
        await expect(shell.locator(`a[href="${destination}"]`)).not.toHaveCount(0);
      }
      await expect(shell.locator(".sb-account__identity")).toContainText(
        activeRole[0].toUpperCase() + activeRole.slice(1),
      );
    });
  }

  test("fails closed for an Organizer without a manageable resource", async ({ page }) => {
    await mockAccount(page, "organizer", false);

    await page.goto("/account");

    const shell = page.locator("header[data-auth-shell]");
    await expect(page).toHaveURL(/\/account$/);
    await expect(shell.getByRole("alert")).toContainText(
      "this session has no manageable organization or event",
    );
    await expect(shell.locator("a")).toHaveCount(0);
    await expect(shell.locator(".sb-account")).toHaveCount(0);
  });
});
