import { expect, test } from "@playwright/test";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const eventId = "22222222-2222-4222-8222-222222222222";

test("Organizer Home stays readable at a real phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      authenticated: true,
      user_id: "user",
      email: "organizer@example.com",
      display_name: "User Zero",
      profile_complete: true,
      csrf_token: "csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_id: organizationId,
      organization_name: "Open Source Summit",
      organization_access: [{ organization_id: organizationId, organization_name: "Open Source Summit", permissions: ["owner"] }],
      event_access: [],
    }),
  }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [{ id: organizationId, name: "Open Source Summit", status: "active", version: 1 }] }),
  }));
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events**`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [{
      id: eventId,
      organization_id: organizationId,
      name: "Open Source Summit 2026",
      status: "active",
      starts_at_ms: Date.UTC(2026, 10, 12, 3, 30),
      ends_at_ms: Date.UTC(2026, 10, 14, 11, 30),
      time_zone: "Asia/Kolkata",
      delivery_mode: "hybrid",
      location: "Bengaluru",
    }] }),
  }));
  await page.route(`**/api/v1/admin/organizations/${organizationId}/metrics`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ event_count: 1, speaker_count: 0, session_count: 4, proposal_count: 7, recent_speakers: [] }),
  }));

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Open Source Summit", exact: true })).toBeVisible();
  await expect(page.locator("#dashboard-greeting")).toHaveText("Organizer dashboard");
  await expect(page.getByRole("heading", { name: "Open Source Summit 2026" })).toBeVisible();
  await expect(page.locator("#metric-sessions")).toHaveText("4");
  await expect(page.locator("#metric-proposals")).toHaveText("7");
  await expect(page.locator("#all-events-link")).toHaveText("View all events →");
  await expect(page.getByRole("heading", { name: "Recent activity" })).toBeVisible();

  const bounds = await page.locator(".organizer-home-identity, .organizer-home-metrics, .organizer-home-panel")
    .evaluateAll((elements) => elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: rect.width };
    }));
  expect(bounds.length).toBe(4);
  for (const bound of bounds) {
    expect(bound.left).toBeGreaterThanOrEqual(0);
    expect(bound.right).toBeLessThanOrEqual(390);
    expect(bound.width).toBeGreaterThan(300);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
