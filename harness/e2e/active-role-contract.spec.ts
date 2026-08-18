import { expect, test, type Page } from "@playwright/test";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

async function mockAccount(
  page: Page,
  activeRole: string | null,
  organizerAccess = true,
) {
  const supported = ["organizer", "reviewer", "speaker"].includes(activeRole || "");
  const workspaceState = activeRole === null
    ? "roleless"
    : !supported
      ? "active_role_invalid"
      : activeRole === "organizer" && !organizerAccess
        ? "organizer_authority_missing"
        : "ready";
  const workspacePath = workspaceState === "roleless"
    ? "/calls"
    : workspaceState !== "ready"
      ? null
      : { organizer: "/admin", reviewer: "/reviews", speaker: "/speaker" }[activeRole!];
  const usablePersonas = [
    ...(organizerAccess ? [{ role: "organizer", workspace_state: "ready", workspace_path: "/admin" }] : []),
    { role: "reviewer", workspace_state: "ready", workspace_path: "/reviews" },
    { role: "speaker", workspace_state: "ready", workspace_path: "/speaker" },
  ];
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
    workspace_state: workspaceState,
    workspace_path: workspacePath,
    usable_personas: usablePersonas,
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

  test("keeps the account page usable while an active role is being restored", async ({ page }) => {
    await mockAccount(page, null);

    await page.goto("/account");

    const shell = page.locator("header[data-auth-shell]");
    await expect(page).toHaveURL(/\/account$/);
    await expect(shell.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Roles and Access" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Organizer/ })).toBeVisible();
  });

  test("keeps account recovery usable when active_role is unsupported", async ({ page }) => {
    await mockAccount(page, "event_admin");

    await page.goto("/account?workspace=recovery");

    const shell = page.locator("header[data-auth-shell]");
    await expect(page).toHaveURL(/\/account\?workspace=recovery$/);
    await expect(shell.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Choose an available workspace" })).toBeVisible();
    await expect(page.locator("#workspace-recovery")).toContainText("current role is no longer available");
    await expect(page.locator("#workspace-recovery")).toHaveAttribute("data-destination", "server-recovery");
    await expect(page.getByRole("heading", { name: "Profile", exact: true })).toBeVisible();
    await shell.locator(".sb-account summary").click();
    await expect(shell.getByRole("button", { name: /Speaker/ })).toBeVisible();
    await expect(shell.getByRole("button", { name: "Sign out" })).toBeVisible();
    await expect(shell.getByRole("link", { name: "Account settings" })).toHaveCount(0);
    await expect(shell.locator(".sb-global-brand:not(a)")).toBeVisible();
    await expect(shell.locator('[href="/admin"], [href="/reviews"], [href="/speaker"]')).toHaveCount(0);
  });

  test("keeps an unsupported active role out of protected workspaces", async ({ page }) => {
    await mockAccount(page, "event_admin");

    await page.goto("/admin");

    const shell = page.locator("header[data-auth-shell]");
    await expect(shell.getByRole("alert")).toContainText("does not have a valid active role");
    await expect(shell.locator('[href="/admin"], [href="/reviews"], [href="/speaker"]')).toHaveCount(0);
  });

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

  test("keeps account recovery usable for an Organizer without a manageable resource", async ({ page }) => {
    await mockAccount(page, "organizer", false);

    await page.goto("/account?workspace=recovery");

    const shell = page.locator("header[data-auth-shell]");
    await expect(page).toHaveURL(/\/account\?workspace=recovery$/);
    await expect(shell.getByRole("alert")).toHaveCount(0);
    await expect(page.locator("#workspace-recovery")).toContainText("organizer role no longer manages an organization");
    await expect(page.getByRole("heading", { name: "Roles and Access" })).toBeVisible();
    await shell.locator(".sb-account summary").click();
    await expect(shell.getByRole("button", { name: /Reviewer/ })).toBeVisible();
    await expect(shell.getByRole("button", { name: /Speaker/ })).toBeVisible();
    await expect(shell.getByRole("link", { name: "Account settings" })).toHaveCount(0);
  });

  test("recovery switches with the server-returned workspace instead of a client map", async ({ page }) => {
    await mockAccount(page, "organizer", false);
    await page.route(/^https?:\/\/[^/]+\/speaker$/, (route) => route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Speaker workspace</title>",
    }));
    let requestedRole = "";
    await page.route("**/api/v1/session/active-role", async (route) => {
      requestedRole = route.request().postDataJSON().role;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          active_role: "speaker",
          workspace_state: "ready",
          workspace_path: "/speaker",
        }),
      });
    });

    await page.goto("/account?workspace=recovery");
    await page.locator(".sb-account summary").click();
    await page.getByRole("button", { name: /Speaker/ }).click();

    await expect.poll(() => requestedRole).toBe("speaker");
    await expect(page).toHaveURL(/\/speaker$/);
  });

  test("a stale reviewer cache cannot deny an organizer document", async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem("sessionbuddy:shell-session", JSON.stringify({
        stored_at: Date.now(),
        session: {
          email: "multi-role@example.test",
          display_name: "Multi Role",
          organization_name: "Example Organization",
          active_role: "reviewer",
          account_roles: ["organizer", "reviewer"],
          profile_complete: true,
        },
      }));
    });
    await mockAccount(page, "organizer");

    await page.goto("/admin");

    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.locator("header[data-auth-shell]").getByRole("alert")).toHaveCount(0);
    await expect(page.locator("header[data-auth-shell]").getByRole("link", { name: "SessionBuddy" }))
      .toHaveAttribute("href", "/admin");
    await expect(page.locator(".sb-account__identity")).toContainText("Organizer");
  });
});
