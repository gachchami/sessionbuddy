import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const secondOrganizationId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const source = (name: string) => readFileSync(resolve(staticRoot, name), "utf8");

const events = [
  {
    id: "event-1", organization_id: organizationId, name: "Spring Summit",
    starts_at_ms: 1772323200000, ends_at_ms: 1772409600000, time_zone: "UTC",
    location: "Convention Center", delivery_mode: "hybrid", status: "active",
    proposal_count: 3, pending_review_count: 2, cfp_status: "published", schedule_status: "ready",
  },
  {
    id: "event-2", organization_id: organizationId, name: "Private Planning Day",
    starts_at_ms: 1780272000000, ends_at_ms: 1780358400000, time_zone: "UTC",
    location: "", delivery_mode: "virtual", status: "draft",
    proposal_count: 0, pending_review_count: 0, cfp_status: "published", schedule_status: "published",
  },
];

async function serveHome(page: Page, manager = true) {
  await page.route(/\/admin(?:\?.*)?$/, (route) => route.fulfill({ contentType: "text/html", body: source("admin_home.html") }));
  for (const [pattern, file, contentType] of [
    ["**/product/assets/product.css*", "product.css", "text/css"],
    ["**/app-shell/assets/app-shell.css*", "app_shell.css", "text/css"],
    ["**/admin/home/assets/home.css*", "admin_home.css", "text/css"],
    ["**/app-shell/assets/api-client.js*", "api_client.js", "text/javascript"],
    ["**/app-shell/assets/app-shell.js*", "app_shell.js", "text/javascript"],
    ["**/app-shell/assets/activity-format.js*", "activity_format.js", "text/javascript"],
    ["**/admin/home/assets/home.js*", "admin_home.js", "text/javascript"],
  ] as const) {
    await page.route(pattern, (route) => route.fulfill({ contentType, body: source(file) }));
  }
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      authenticated: true, user_id: "user", email: "organizer@example.com", display_name: "User Zero",
      profile_complete: true, csrf_token: "csrf", account_roles: ["organizer"], active_role: "organizer",
      default_role: "organizer", organization_id: organizationId, organization_name: "Open Source Summit",
      organization_access: manager
        ? [{ organization_id: organizationId, organization_name: "Open Source Summit", permissions: ["owner"] }]
        : [],
      event_access: manager ? [] : [{ organization_id: organizationId, event_id: "event-1", event_name: "Spring Summit", permissions: ["edit"], assignments: [] }],
    }),
  }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [
      { id: organizationId, name: "Open Source Summit", status: "active", version: 1 },
      ...(manager ? [{ id: secondOrganizationId, name: "Design Systems Guild", status: "active", version: 1 }] : []),
    ] }),
  }));
  await page.route("**/api/v1/admin/organizations/*/activities", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [
      { activity_id: "read", actor_name: "Alex", operation: "read", resource_type: "event", resource_id: "opaque", subject_name: "Hidden read", occurred_at_ms: Date.now() - 1_000 },
      { activity_id: "change", actor_name: "Alex", operation: "update", resource_type: "event", resource_id: "opaque", subject_name: "Spring Summit", occurred_at_ms: Date.now() - 60_000 },
    ] }),
  }));
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`Organizer Home renders the semantic ledger and activity at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await serveHome(page);
    let eventRequests = 0;
    await page.route("**/api/v1/admin/organizations/*/events**", (route) => {
      eventRequests += 1;
      const url = new URL(route.request().url());
      const secondPage = url.searchParams.has("cursor");
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(secondPage
          ? { data: [{ ...events[0], id: "event-3", name: "Autumn Forum" }], next_cursor: null }
          : { data: events, next_cursor: "page-2" }),
      });
    });

    await page.goto("/admin?view=active&order=recent&q=summit");
    await expect(page.getByRole("heading", { name: "Open Source Summit", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Active" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("searchbox", { name: "Search" })).toHaveValue("summit");
    await expect(page.getByLabel("Sort")).toHaveValue("recent");
    await expect(page.getByRole("link", { name: "Create event" })).toHaveAttribute("href", `/admin/events/new?organization_id=${organizationId}`);

    const row = page.getByRole("row").filter({ hasText: "Spring Summit" });
    await expect(row.getByRole("link", { name: "Spring Summit" })).toHaveAttribute("href", "/admin/events/event-1");
    await expect(row.getByRole("link", { name: "3 proposals" })).toHaveAttribute("href", "/admin/events/event-1/submissions");
    await expect(row.getByRole("link", { name: "2 awaiting reviews" })).toHaveAttribute("href", "/admin/events/event-1/submissions#rounds-title");
    await expect(row.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/admin/events/event-1/settings");
    await expect(row.getByRole("link", { name: "Clone" })).toHaveAttribute("href", "/admin/events/new?source=event-1");
    await expect(page.getByRole("link", { name: "Open", exact: true })).toHaveCount(0);
    await expect(page.getByText("CFP published · offline", { exact: true })).toBeVisible();
    await expect(page.getByText("Schedule published · offline", { exact: true })).toBeVisible();

    const order = await page.locator("#events, #recent-changes, #event-pagination").evaluateAll((nodes) => nodes.map((node) => node.id));
    expect(order).toEqual(["events", "recent-changes", "event-pagination"]);
    await expect(page.getByRole("complementary", { name: "Recent changes" })).toContainText("Alex updated event Spring Summit");
    await expect(page.getByRole("complementary", { name: "Recent changes" })).not.toContainText("Hidden read");
    await page.getByRole("link", { name: "Skip to recent changes" }).focus();
    await page.getByRole("link", { name: "Skip to recent changes" }).press("Enter");
    await expect(page.getByRole("complementary", { name: "Recent changes" })).toBeFocused();

    const cells = row.getByRole("cell");
    await expect(cells).toHaveCount(5);
    for (let index = 0; index < 5; index += 1) {
      await expect(cells.nth(index)).toHaveAttribute("aria-labelledby", /^event-column-/);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);

    await page.getByRole("button", { name: "Load more" }).click();
    await expect(page.getByRole("link", { name: "Autumn Forum" })).toBeVisible();
    expect(eventRequests).toBe(2);
  });
}

test("Organizer Home keeps the single-column DOM at 200% zoom width", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 800 });
  await serveHome(page);
  await page.route("**/api/v1/admin/organizations/*/events**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: events, next_cursor: null }) }));
  await page.goto("/admin");
  const areas = await page.locator(".organizer-home-grid").evaluate((node) => getComputedStyle(node).gridTemplateAreas);
  expect(areas).toContain('"events"');
  expect(areas).toContain('"changes"');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(640);
});

test("Organizer Home omits manager actions and activity for an event editor", async ({ page }) => {
  await serveHome(page, false);
  await page.route("**/api/v1/admin/organizations/*/events**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [events[0]], next_cursor: null }) }));
  await page.goto("/admin");
  await expect(page.locator("#recent-changes")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Skip to recent changes" })).toBeHidden();
  await expect(page.getByRole("link", { name: "Create event" })).toBeHidden();
  await expect(page.getByRole("link", { name: "Organization settings" })).toBeHidden();
  await expect(page.getByRole("link", { name: "Clone" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
});
