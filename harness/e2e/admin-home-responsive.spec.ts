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
    proposal_count: 3, speaker_count: 10, pending_review_count: 2, cfp_status: "published",
    cfp_public_path: "/cfp/event1/spring-summit", schedule_status: "published",
  },
  {
    id: "event-2", organization_id: organizationId, name: "Private Planning Day",
    starts_at_ms: 1780272000000, ends_at_ms: 1780358400000, time_zone: "UTC",
    location: "", delivery_mode: "virtual", status: "draft",
    proposal_count: 0, speaker_count: 0, pending_review_count: 0, cfp_status: "published",
    cfp_public_path: "/cfp/event2/private-planning-day", schedule_status: "published",
  },
];
const secondOrganizationEvent = {
  ...events[0],
  id: "design-event-1",
  organization_id: secondOrganizationId,
  name: "Design Systems Assembly",
  location: "Studio Two",
  proposal_count: 7,
  speaker_count: 4,
};

async function serveHome(page: Page) {
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
      workspace_state: "ready", workspace_path: "/admin",
      usable_personas: [{ role: "organizer", workspace_state: "ready", workspace_path: "/admin" }],
      organization_access: [
          { organization_id: organizationId, organization_name: "Open Source Summit", permissions: ["owner"] },
          { organization_id: secondOrganizationId, organization_name: "Design Systems Guild", permissions: ["manage"] },
        ],
      event_access: [],
    }),
  }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [
      { id: organizationId, name: "Open Source Summit", status: "active", version: 1, event_count: 2, pending_review_count: 2 },
      { id: secondOrganizationId, name: "Design Systems Guild", status: "active", version: 1, event_count: 1, pending_review_count: 1 },
    ] }),
  }));
  await page.route("**/api/v1/admin/organizations/*/activities", (route) => {
    const isSecondOrganization = route.request().url().includes(secondOrganizationId);
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [
        { activity_id: "change", actor_name: isSecondOrganization ? "Priya" : "Alex", operation: "update", resource_type: "event", resource_id: "opaque", subject_name: isSecondOrganization ? "Design Systems Assembly" : "Spring Summit", occurred_at_ms: Date.now() - 60_000 },
      ] }),
    });
  });
}

for (const viewport of [
  { name: "desktop", width: 1320, height: 800 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`Organizer Home renders the semantic ledger and activity at ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await serveHome(page);
    let eventRequests = 0;
    await page.route("**/api/v1/admin/organizations/*/events**", (route) => {
      eventRequests += 1;
      const url = new URL(route.request().url());
      const isSecondOrganization = url.pathname.includes(secondOrganizationId);
      const secondPage = url.searchParams.has("cursor");
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(isSecondOrganization
          ? { data: [secondOrganizationEvent], next_cursor: null }
          : secondPage
          ? { data: [{ ...events[0], id: "event-3", name: "Autumn Forum" }], next_cursor: null }
          : { data: events, next_cursor: "page-2" }),
      });
    });

    await page.goto("/admin?view=active&order=recent&q=summit");
    await expect(page.getByRole("heading", { name: "Organizer workspace", exact: true })).toBeVisible();
    if (viewport.name === "desktop") {
      await expect(page.getByRole("navigation", { name: "Organizations" })).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Organizations" }).getByRole("button").filter({ hasText: "Open Source Summit" })).toHaveAttribute("aria-current", "page");
      await expect(page.getByLabel("Switch organization")).toBeHidden();
    } else {
      await expect(page.getByRole("navigation", { name: "Organizations" })).toBeHidden();
      await expect(page.getByLabel("Switch organization")).toBeVisible();
      await expect(page.getByLabel("Switch organization")).toHaveValue(organizationId);
    }
    await expect(page.getByRole("button", { name: "Active" })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("searchbox", { name: "Search" })).toHaveValue("summit");
    if (viewport.name === "mobile") await page.getByText("Sort options", { exact: true }).click();
    await expect(page.getByLabel("Sort")).toHaveValue("recent");
    await expect(page.getByRole("link", { name: "Create event" })).toHaveAttribute("href", `/admin/events/new?organization_id=${organizationId}`);

    const row = page.getByRole("row").filter({ hasText: "Spring Summit" });
    await expect(row.getByRole("link", { name: "Spring Summit", exact: true })).toHaveAttribute("href", "/admin/events/event-1");
    await expect(row.getByText("CFP open · 3 proposals", { exact: true })).toBeVisible();
    await expect(row.getByText("Agenda live · 10 speakers", { exact: true })).toBeVisible();
    await expect(row.getByRole("link", { name: "CFP open · 3 proposals — view public CFP for Spring Summit" })).toHaveAttribute("href", "/cfp/event1/spring-summit");
    await expect(row.getByRole("link", { name: "Agenda live · 10 speakers — view public agenda for Spring Summit" })).toHaveAttribute("href", "/events/event-1/schedule");
    await expect(row.getByRole("link", { name: "Manage CFP", exact: true })).toBeHidden();
    await row.locator("summary").click();
    await expect(row.getByRole("link", { name: "Manage CFP", exact: true })).toBeVisible();
    await row.locator(".organizer-home-event-tools-menu").click({ position: { x: 2, y: 2 } });
    await expect(row.locator("details")).toHaveAttribute("open", "");
    await page.getByRole("heading", { name: "Organizer workspace", exact: true }).click();
    await expect(row.locator("details")).not.toHaveAttribute("open", "");
    await expect(row.getByRole("link", { name: "Manage CFP", exact: true })).toBeHidden();
    await row.locator("summary").click();
    await expect(row.getByRole("link", { name: "Manage CFP", exact: true })).toHaveAttribute("href", "/admin/events/event-1/cfp");
    await expect(row.getByRole("link", { name: "Manage agenda", exact: true })).toHaveAttribute("href", "/admin/events/event-1/agenda");
    await expect(row.getByRole("link", { name: "Speakers", exact: true })).toHaveAttribute("href", "/admin/events/event-1/speakers");
    await expect(row.getByRole("link", { name: "Reviewers", exact: true })).toHaveAttribute("href", "/admin/events/event-1/reviewers");
    await row.locator("summary").click();
    await expect(row.getByRole("link", { name: "Review 2 proposals", exact: true })).toHaveAttribute("href", "/admin/events/event-1/submissions");
    await expect(row.locator(".organizer-home-event-calendar")).toHaveCount(0);
    await expect(row.locator(".organizer-home-event-name")).toHaveCSS("font-size", "18px");
    await row.locator("summary").click();
    await expect(row.getByRole("link", { name: "Manage", exact: true })).toHaveAttribute("href", "/admin/events/event-1/settings");
    await expect(row.getByRole("link", { name: "Clone" })).toHaveAttribute("href", "/admin/events/new?source=event-1");
    await row.getByRole("link", { name: "Clone" }).focus();
    await page.keyboard.press("Escape");
    await expect(row.locator("details")).not.toHaveAttribute("open", "");
    await expect(row.locator("summary")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(row.getByRole("link", { name: "Manage", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open", exact: true })).toHaveCount(0);
    await row.locator("summary").click();
    const draftRow = page.getByRole("row").filter({ hasText: "Private Planning Day" });
    await expect(draftRow.getByText("CFP not configured", { exact: true })).toBeVisible();
    await expect(draftRow.getByText("Agenda not built", { exact: true })).toBeVisible();
    await draftRow.locator("summary").click();
    await expect(draftRow.getByRole("link", { name: "Manage CFP", exact: true })).toHaveAttribute("href", "/admin/events/event-2/cfp");
    await expect(draftRow.getByRole("link", { name: "Manage agenda", exact: true })).toHaveAttribute("href", "/admin/events/event-2/agenda");
    await draftRow.locator("summary").click();
    if (viewport.name === "mobile") {
      const sortBox = await page.getByLabel("Sort").boundingBox();
      expect(sortBox?.width).toBeGreaterThan(240);
      await page.getByText("Sort options", { exact: true }).click();
      await row.locator("summary").click();
      const titleBox = await row.getByRole("link", { name: "Spring Summit", exact: true }).boundingBox();
      const settingsBox = await row.getByRole("link", { name: "Manage", exact: true }).boundingBox();
      expect(settingsBox!.y).toBeGreaterThan(titleBox!.y);
      expect(settingsBox!.height).toBeGreaterThanOrEqual(44);
      const dateBox = await row.locator(".organizer-home-event-date").boundingBox();
      const locationBox = await row.locator(".organizer-home-event-state").boundingBox();
      expect(dateBox!.y).toBeGreaterThan(locationBox!.y);
      await page.getByLabel("Switch organization").selectOption(secondOrganizationId);
      await expect(page.locator("#events-title")).toHaveText("Design Systems Guild");
      await expect(page).toHaveURL(new RegExp(`organization_id=${secondOrganizationId}`));
      await expect(page.getByRole("link", { name: "Create event in Design Systems Guild" })).toHaveAttribute("href", `/admin/events/new?organization_id=${secondOrganizationId}`);
      await expect(page.getByRole("link", { name: "Design Systems Assembly", exact: true })).toHaveAttribute("href", "/admin/events/design-event-1");
      await expect(page.getByRole("link", { name: "Spring Summit", exact: true })).toHaveCount(0);
      await page.getByRole("row").filter({ hasText: "Design Systems Assembly" }).locator("summary").click();
      await expect(page.getByRole("row").filter({ hasText: "Design Systems Assembly" }).getByRole("link", { name: "Manage", exact: true })).toHaveAttribute("href", "/admin/events/design-event-1/settings");
      await expect(page.getByRole("row").filter({ hasText: "Design Systems Assembly" }).getByRole("link", { name: "Clone" })).toHaveAttribute("href", "/admin/events/new?source=design-event-1");
      await expect(page.getByRole("complementary", { name: "Recent changes" })).toContainText("Priya updated event Design Systems Assembly");
      await expect(page.getByRole("complementary", { name: "Recent changes" })).not.toContainText("Spring Summit");
      await page.getByLabel("Switch organization").selectOption(organizationId);
      await expect(page.getByRole("link", { name: "Spring Summit", exact: true })).toBeVisible();
      await expect(page.getByRole("link", { name: "Design Systems Assembly", exact: true })).toHaveCount(0);
    }

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
    if (viewport.name === "desktop") {
      const tableWidths = await page.locator("#event-table").evaluate((node) => ({ client: node.clientWidth, scroll: node.scrollWidth }));
      expect(tableWidths.scroll).toBeLessThanOrEqual(tableWidths.client);
    }

    const requestsBeforeLoadMore = eventRequests;
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(page.getByRole("link", { name: "Autumn Forum", exact: true })).toBeVisible();
    expect(eventRequests).toBe(requestsBeforeLoadMore + 1);
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

test("Organizer Home keeps a concise Create label and organization context across viewports", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  await serveHome(page);
  await page.route("**/api/v1/admin/organizations/*/events**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: events, next_cursor: null }),
  }));
  await page.goto("/admin");
  const create = page.locator("#new-event");
  await expect(create).toHaveText("Create event");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(create).toHaveText("Create event");
  await expect(create).toHaveAttribute("aria-label", "Create event in Open Source Summit");
});

for (const width of [1280, 390]) {
  test(`Organizer Home puts work before redundant context at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await serveHome(page);
    await page.route("**/api/v1/admin/organizations", route => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{ id: organizationId, name: "Open Source Summit", event_count: 2, pending_review_count: 2 }] }),
    }));
    await page.route("**/api/v1/admin/organizations/*/events**", route => route.fulfill({
      contentType: "application/json", body: JSON.stringify({ data: events, next_cursor: null }),
    }));
    await page.goto("/admin");
    const firstEvent = page.getByRole("link", { name: "Spring Summit", exact: true });
    await expect(firstEvent).toBeVisible();
    await expect(page.locator(".sb-topbar #workspace-summary")).toContainText("2 review items need attention");
    await expect(page.locator("#workspace-summary")).toBeVisible();
    await expect(page.locator(".sb-sidebar #organization-rail")).toHaveCount(1);
    if (width > 800) {
      await expect(page.getByLabel("Find organization")).toBeVisible();
      await page.getByLabel("Find organization").fill("no matching organization");
      await expect(page.locator("#organization-list button")).toHaveCount(0);
      await page.getByLabel("Find organization").fill("Open");
      await expect(page.locator("#organization-list button")).toHaveCount(1);
    } else {
      await expect(page.getByLabel("Switch organization")).toBeVisible();
    }
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    const box = await firstEvent.boundingBox();
    // The first task must fit in the upper half of the desktop viewport and
    // leave a usable event card in the first mobile viewport.
    expect(box!.y).toBeLessThan(width > 800 ? 400 : 620);
    expect(await firstEvent.evaluate(node => parseFloat(getComputedStyle(node).fontSize))).toBeGreaterThanOrEqual(16);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test("Organizer Home keeps 10 organizations and 50 events operable with disclosed event tools", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await serveHome(page);
  const organizations = Array.from({ length: 10 }, (_, index) => ({
    id: index === 0 ? organizationId : `organization-${index}`,
    name: index === 0 ? "Open Source Summit" : `Organization ${index + 1}`,
    status: "active",
    version: 1,
    event_count: 5,
    pending_review_count: index % 3,
  }));
  const manyEvents = Array.from({ length: 50 }, (_, index) => ({
    ...events[index % events.length],
    id: `event-${index + 1}`,
    name: `Event ${String(index + 1).padStart(2, "0")}`,
  }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: organizations }),
  }));
  await page.route("**/api/v1/admin/organizations/*/events**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: manyEvents, next_cursor: null }),
  }));

  await page.goto("/admin");
  await expect(page.getByRole("navigation", { name: "Organizations" }).getByRole("button")).toHaveCount(10);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("Switch organization")).toBeVisible();
  await expect(page.getByLabel("Switch organization").locator("option")).toHaveCount(10);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("row").filter({ hasText: "Event 50" }).locator("summary").click();
  await expect(page.getByRole("row").filter({ hasText: "Event 50" }).getByRole("link", { name: "Manage", exact: true })).toBeVisible();
  // Header visibility follows the available content width beside the sidebar;
  // all fifty data rows must remain reachable in both table and card layouts.
  await expect(page.locator("#event-list").getByRole("row")).toHaveCount(50);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440);
  const tableWidths = await page.locator("#event-table").evaluate((node) => ({ client: node.clientWidth, scroll: node.scrollWidth }));
  expect(tableWidths.scroll).toBeLessThanOrEqual(tableWidths.client);
});
