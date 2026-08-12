import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const source = (name: string) => readFileSync(resolve(staticRoot, name), "utf8");

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`Organizer Home presents a minimal paginated event index on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    let eventDataRequests = 0;
    await page.route(/\/admin(?:\?.*)?$/, (route) => route.fulfill({
      contentType: "text/html", body: source("admin_home.html"),
    }));
    for (const [pattern, file, contentType] of [
      ["**/product/assets/product.css*", "product.css", "text/css"],
      ["**/app-shell/assets/app-shell.css*", "app_shell.css", "text/css"],
      ["**/app-shell/assets/api-client.js*", "api_client.js", "text/javascript"],
      ["**/app-shell/assets/app-shell.js*", "app_shell.js", "text/javascript"],
      ["**/admin/home/assets/home.js*", "admin_home.js", "text/javascript"],
    ] as const) {
      await page.route(pattern, (route) => route.fulfill({
        contentType, body: source(file),
      }));
    }
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
    await page.route("**/api/v1/admin/organizations/*/events**", (route) => {
      eventDataRequests += 1;
      const secondPage = new URL(route.request().url()).searchParams.has("cursor");
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(secondPage ? {
          data: [{ id: "event-3", name: "Autumn Forum", starts_at_ms: 1798761600000, time_zone: "UTC", delivery_mode: "in_person", status: "draft" }],
          next_cursor: null,
        } : {
          data: [
            { id: "event-1", name: "Spring Summit", starts_at_ms: 1772323200000, time_zone: "UTC", delivery_mode: "hybrid", status: "active" },
            { id: "event-2", name: "Summer School", starts_at_ms: 1780272000000, time_zone: "UTC", delivery_mode: "online", status: "active" },
            { id: "event-old", name: "Winter Archive", starts_at_ms: 1735689600000, time_zone: "UTC", delivery_mode: "in_person", status: "archived" },
          ],
          next_cursor: "page-2",
        }),
      });
    });
    await page.route("**/api/v1/admin/organizations/*/metrics", (route) => {
      eventDataRequests += 1;
      return route.abort();
    });

    await page.goto("/admin");
    await expect(page.getByRole("heading", { name: "Open Source Summit", exact: true })).toBeVisible();
    const account = page.locator("details.sb-account");
    const accountTrigger = account.locator("summary");
    await accountTrigger.click();
    await expect(account).toHaveAttribute("open", "");
    await page.locator("main").click({ position: { x: 4, y: 4 }, force: true });
    await expect(account).not.toHaveAttribute("open", "");
    await accountTrigger.click();
    await page.keyboard.press("Escape");
    await expect(account).not.toHaveAttribute("open", "");
    await expect(accountTrigger).toBeFocused();
    await expect(page.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/admin/organization");
    await expect(page.getByRole("link", { name: "Create event" })).toHaveAttribute("href", "/admin/events#event-form");
    await expect(page.getByRole("link", { name: /Spring Summit/ })).toHaveAttribute("href", "/admin/events/event-1");
    await expect(page.getByRole("link", { name: /Summer School/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Current events" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Winter Archive/ })).toBeHidden();
    await page.getByText("Archived events (1)", { exact: true }).click();
    await expect(page.getByRole("link", { name: /Winter Archive/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Winter Archive/ })).toContainText("In person");
    await expect(page.getByText("Organization workspace")).toHaveCount(0);
    await expect(page.getByText("Your program at a glance")).toHaveCount(0);
    await expect(page.getByText("Recent activity")).toHaveCount(0);
    const identityLayout = await page.locator(".organizer-home-identity").evaluate((element) => {
      const section = element.getBoundingClientRect();
      const heading = element.querySelector("h1")!.getBoundingClientRect();
      return { sectionWidth: section.width, sectionHeight: section.height, headingWidth: heading.width, headingHeight: heading.height };
    });
    expect(identityLayout.headingWidth).toBeGreaterThan(identityLayout.sectionWidth * .4);
    expect(identityLayout.headingHeight).toBeLessThan(100);
    expect(identityLayout.sectionHeight).toBeLessThan(viewport.width <= 480 ? 240 : 180);
    expect(eventDataRequests).toBe(1);
    await page.getByRole("button", { name: "Load more" }).click();
    await expect(page.getByRole("link", { name: /Autumn Forum/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Load more" })).toBeHidden();
    expect(eventDataRequests).toBe(2);

    const bounds = await page.locator(".organizer-home-identity, .organizer-home-event-index, .organizer-home-event-row")
      .evaluateAll((elements) => elements.map((element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right, width: rect.width };
      }));
    expect(bounds.length).toBe(6);
    for (const bound of bounds) {
      expect(bound.left).toBeGreaterThanOrEqual(0);
      expect(bound.right).toBeLessThanOrEqual(viewport.width);
      expect(bound.width).toBeGreaterThan(0);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
  });
}
