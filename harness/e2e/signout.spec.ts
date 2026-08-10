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
  organization_access: [{
    organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    roles: ["organization_admin"],
  }],
  event_access: [],
});

async function serveSignedInEventsPage(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ contentType: "application/json", body: sessionBody }));
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
    await serveSignedInEventsPage(page);
    let logoutMethod = "";
    let logoutCsrf: string | undefined;
    let logoutContentType: string | undefined;
    await page.route("**/api/v1/session/logout", async (route) => {
      logoutMethod = route.request().method();
      logoutCsrf = route.request().headers()["x-csrf-token"];
      logoutContentType = route.request().headers()["content-type"];
      await route.fulfill({ status: 204 });
    });

    const response = await page.goto("/admin/events");
    expect(response?.ok()).toBeTruthy();

    await page.locator("summary[aria-label='Profile and account for admin@example.com']").click();
    const signOut = page.getByRole("button", { name: "Sign out" });
    await expect(signOut).toBeVisible();
    await signOut.click();

    await expect(page).toHaveURL(/\/$/);
    expect(logoutMethod).toBe("POST");
    expect(logoutCsrf).toBe("browser-test-csrf");
    expect(logoutContentType).toBe("application/json");
  });

  test("a failed sign-out recovers instead of stranding the user", async ({ page }) => {
    await serveSignedInEventsPage(page);
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
      await route.fulfill({ status: 204 });
    });

    await page.goto("/admin/events");
    await page.locator("summary[aria-label='Profile and account for admin@example.com']").click();
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
