import { expect, test } from "@playwright/test";

const reviewerSession = JSON.stringify({
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  csrf_token: "browser-test-csrf",
  email: "namohh.namaha+reviewer@example.com",
  display_name: "Rhea Reviewer",
  profile_complete: true,
  account_roles: ["reviewer"],
  active_role: "reviewer",
  default_role: "reviewer",
  organization_access: [],
  event_access: [{
    organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    event_name: "Example Event",
    permissions: [],
    assignments: ["reviewer"],
  }],
});

test.describe("reviewer workspace", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("an evaluator with no assignments gets a clean, useful empty state", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: [], total: 0, completed_count: 0, next_cursor: null }),
      }));

    await page.goto("/reviews");

    await expect(page.getByRole("heading", { name: "Reviews", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "No reviews assigned" })).toBeVisible();
    await expect(page.getByText("New assignments will appear here, and we’ll notify you by email.")).toBeVisible();
    await expect(page.getByText("Hide finalized")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();

    // Non-organizer portals do not render an empty Main navigation card.
    await expect(page.locator(".sb-sidebar__primary")).toHaveCount(0);
    await expect(page.locator(".sb-account__identity strong")).toHaveText("Rhea Reviewer · Reviewer");

    // The accessibility link is visually clipped until keyboard focus.
    const skipLink = page.getByRole("link", { name: "Skip to reviews" });
    expect((await skipLink.boundingBox())?.width).toBe(1);
    await skipLink.focus();
    expect((await skipLink.boundingBox())?.width).toBeGreaterThan(1);
  });
});
