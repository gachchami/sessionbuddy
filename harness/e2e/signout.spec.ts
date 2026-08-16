import { expect, test } from "@playwright/test";

// Sign-out from the app-shell account menu. The page under test is the real
// /admin/events document served by the worker; every API interaction is
// mocked so the test pins the browser-side contract: a guarded POST to
// /api/v1/session/logout with the session's CSRF token, a redirect home on
// success, and a recoverable button on failure.

const sessionBody = JSON.stringify({
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  event_id: null,
  csrf_token: "browser-test-csrf",
  email: "admin@example.com",
  display_name: "Admin User",
  profile_complete: true,
  account_roles: ["organizer"],
  active_role: "organizer",
  default_role: "organizer",
  organization_access: [{
    organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    organization_name: "Sign Out Organization",
    permissions: ["owner"],
  }],
  event_access: [],
});

async function serveSignedInEventsPage(
  page: import("@playwright/test").Page,
  sessionIsActive: () => boolean = () => true,
) {
  await page.route("**/api/v1/setup/status", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: true }) }));
  await page.route("**/api/v1/auth/session", (route) => {
    if (!sessionIsActive()) {
      return route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "authentication_required", message: "Authentication required" } }),
      });
    }
    return route.fulfill({ contentType: "application/json", body: sessionBody });
  });
  await page.route("**/api/v1/admin/organizations", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{
          id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          name: "Sign Out Organization",
          status: "active",
          version: 1,
        }],
      }),
    }));
  // The events list is server-paginated, so the request carries a query
  // string (?view=...&q=...); match it explicitly.
  await page.route("**/api/v1/admin/organizations/*/events**", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], next_cursor: null }) }));
}

test.describe("account sign-out", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("signing out posts the guarded logout request and returns home", async ({ page }) => {
    let sessionIsActive = true;
    await serveSignedInEventsPage(page, () => sessionIsActive);
    let logoutMethod = "";
    let logoutCsrf: string | undefined;
    let logoutContentType: string | undefined;
    await page.route("**/api/v1/session/logout", async (route) => {
      logoutMethod = route.request().method();
      logoutCsrf = route.request().headers()["x-csrf-token"];
      logoutContentType = route.request().headers()["content-type"];
      sessionIsActive = false;
      await route.fulfill({ status: 204 });
    });

    const response = await page.goto("/admin/events");
    expect(response?.ok()).toBeTruthy();

    // Pin the shared layer contract against sticky workflow content. Before
    // the shell established its own higher layer, a workflow hero could win
    // hit testing over the open account menu and make Sign out unclickable.
    await page.evaluate(() => {
      const hero = document.createElement("div");
      hero.className = "page-heading workflow-hero";
      hero.dataset.testOverlay = "workflow-hero";
      Object.assign(hero.style, {
        position: "fixed",
        zIndex: "80",
        inset: "4rem 0 auto 0",
        height: "12rem",
      });
      document.body.append(hero);
    });

    await page.locator("summary[aria-label='Account menu for Admin User']").click();
    const signOut = page.getByRole("button", { name: "Sign out" });
    await expect(signOut).toBeVisible();
    expect(await signOut.evaluate((button) => {
      const bounds = button.getBoundingClientRect();
      const hit = document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2);
      return hit === button || button.contains(hit);
    })).toBe(true);
    await signOut.click();

    await expect(page).toHaveURL(/\/$/);
    expect(logoutMethod).toBe("POST");
    expect(logoutCsrf).toBe("browser-test-csrf");
    expect(logoutContentType).toBe("application/json");
  });

  test("an already-dead session still signs out cleanly", async ({ page }) => {
    // A 401 from logout means the session is already gone server-side.
    // Retrying can never succeed, so the shell must treat it as a completed
    // sign-out and go home instead of trapping the user in a retry loop.
    let sessionIsActive = true;
    await serveSignedInEventsPage(page, () => sessionIsActive);
    let logoutAttempts = 0;
    await page.route("**/api/v1/session/logout", async (route) => {
      logoutAttempts += 1;
      sessionIsActive = false;
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "authentication_required", message: "Authentication required" } }),
      });
    });

    await page.goto("/admin/events");
    await page.locator("summary[aria-label='Account menu for Admin User']").click();
    await page.getByRole("button", { name: "Sign out" }).click();

    await expect(page).toHaveURL(/\/$/);
    expect(logoutAttempts).toBe(1);
    await expect(page.getByRole("button", { name: "Try sign out again" })).toHaveCount(0);
  });

  test("a failed sign-out recovers instead of stranding the user", async ({ page }) => {
    let sessionIsActive = true;
    await serveSignedInEventsPage(page, () => sessionIsActive);
    let logoutAttempts = 0;
    await page.route("**/api/v1/session/logout", async (route) => {
      logoutAttempts += 1;
      if (logoutAttempts === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "dependency_unavailable", message: "Unavailable" } }),
        });
        return;
      }
      sessionIsActive = false;
      await route.fulfill({ status: 204 });
    });

    await page.goto("/admin/events");
    await page.locator("summary[aria-label='Account menu for Admin User']").click();
    await page.getByRole("button", { name: "Sign out" }).click();

    // The failure re-enables the control with an explicit retry affordance
    // and never navigates away from the page.
    const retry = page.getByRole("button", { name: "Try sign out again" });
    await expect(retry).toBeEnabled();
    await expect(page).toHaveURL(/\/admin\/events$/);

    await retry.click();
    await expect(page).toHaveURL(/\/$/);
    expect(logoutAttempts).toBe(2);
  });
});
