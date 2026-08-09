import { expect, test, type Page } from "@playwright/test";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const session = {
  authenticated: true,
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "admin@example.com",
  display_name: "Admin User",
  csrf_token: "browser-test-csrf",
  organization_id: organizationId,
  event_id: eventId,
  organization_access: [{ organization_id: organizationId, roles: ["organization_admin"] }],
  event_access: [{ organization_id: organizationId, event_id: eventId, roles: ["event_admin"] }],
};

async function mockSession(page: Page) {
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
  await page.route("**/api/v1/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
}

test.describe("safe API and page failures", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("plain-text event API failures become a safe recoverable message", async ({ page }) => {
    await mockSession(page);
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ id: organizationId, name: "Example Events", status: "active", version: 1 }],
      }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}/events`, (route) => route.fulfill({
      status: 500,
      contentType: "text/plain",
      headers: { "x-request-id": "event-load-reference" },
      body: "Internal Server Error: private database details",
    }));

    await page.goto("/admin/events");
    const status = page.getByRole("status").first();
    await expect(status).toContainText("Something went wrong on our side. Try again.");
    await expect(status).toContainText("event-load-reference");
    await expect(status).not.toContainText("Unexpected token");
    await expect(status).not.toContainText("Internal Server Error");
    await expect(status).not.toContainText("private database details");
  });

  test("successful non-JSON responses are treated as invalid without leaking markup", async ({ page }) => {
    await page.route("**/api/v1/engine-room/status", (route) => route.fulfill({
      status: 200,
      contentType: "text/html",
      headers: { "x-request-id": "invalid-json-reference" },
      body: "<html><body>proxy implementation details</body></html>",
    }));

    await page.goto("/engine-room");
    const notice = page.locator("#notice");
    await expect(notice).toContainText("The server returned an unexpected response. Try again.");
    await expect(notice).toContainText("invalid-json-reference");
    await expect(notice).not.toContainText("proxy implementation details");
  });

  test("network failures show a connection message on a public page", async ({ page }) => {
    await page.route(`**/api/v1/public/events/${eventId}/speakers`, (route) => route.abort("failed"));

    await page.goto(`/events/${eventId}/speakers`);
    await expect(page.getByRole("status")).toContainText(
      "We could not connect. Check your connection and try again.",
    );
  });

  test("React review pages also normalize text server failures", async ({ page }) => {
    await mockSession(page);
    await page.route("**/api/v1/evaluator/assignments", (route) => route.fulfill({
      status: 502,
      contentType: "text/plain",
      headers: { "x-request-id": "review-load-reference" },
      body: "Internal Server Error",
    }));

    await page.goto("/reviews");
    const status = page.getByRole("status").first();
    await expect(status).toContainText("Something went wrong on our side. Try again.");
    await expect(status).toContainText("review-load-reference");
    await expect(status).not.toContainText("Unexpected token");
  });

  test("unknown browser routes render a useful 404 page", async ({ page }) => {
    const response = await page.goto("/this-page-does-not-exist");
    expect(response?.status()).toBe(404);
    await expect(page).toHaveTitle(/Page not found/);
    await expect(page.getByRole("heading", { name: "We could not find that page." })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open SessionBuddy" })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("resource_not_found");
  });
});
