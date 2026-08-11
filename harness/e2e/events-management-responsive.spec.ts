import { expect, test } from "@playwright/test";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

test("Events table and save failures stay usable on mobile", async ({ page }) => {
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
      default_email_sender_name: "SessionBuddy",
      default_email_address: "events@example.test",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_id: organizationId,
      organization_name: "AIEngineer",
      organization_access: [{ organization_id: organizationId, roles: ["organization_admin"] }],
      event_access: [],
    }),
  }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [{ id: organizationId, name: "AIEngineer", status: "active", version: 1 }] }),
  }));
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events?*`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: Array.from({ length: 12 }, (_, index) => ({
      id: `event-${index}`,
      organization_id: organizationId,
      name: `AIEngineer Event ${index + 1}`,
      status: "active",
      version: 1,
      starts_at_ms: Date.UTC(2027, index % 12, 12, 3, 30),
      ends_at_ms: Date.UTC(2027, index % 12, 13, 11, 30),
      time_zone: "Asia/Kolkata",
      delivery_mode: "hybrid",
      location: "Bengaluru",
      description: "Event description",
      proposal_count: index + 1,
      pending_review_count: index % 3,
      schedule_status: index ? "draft" : "published",
    })), next_cursor: "more" }),
  }));
  await page.route("**/api/v1/admin/events/*/duplicate", async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({
      name: "AIEngineer Event 1 copy",
      source_version: 1,
      status: "draft",
      retain_source_logo: false,
      retain_source_cover: false,
    });
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ name: "AIEngineer Event 1 copy" }) });
  });
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events`, async (route) => {
    expect(route.request().postDataJSON()).toMatchObject({ status: "active" });
    await new Promise((resolve) => setTimeout(resolve, 1_200));
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({
      error: { code: "service_unavailable", message: "Event service is temporarily unavailable." },
      request_id: "request-1",
    }) });
  });

  await page.goto("/admin/events");
  await expect(page.getByRole("heading", { name: "Events", exact: true })).toBeVisible();
  await expect(page.locator(".event-table-row")).toHaveCount(12);
  const firstEvent = page.locator(".event-table-row").first();
  await expect(firstEvent.locator(".event-table-cfp")).toContainText("Manage CFP");
  await expect(firstEvent.locator(".event-table-cfp")).toContainText("1 proposal");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await firstEvent.locator('summary[aria-label="More actions for AIEngineer Event 1"]').click();
  await page.getByRole("button", { name: "Duplicate AIEngineer Event 1 as a draft" }).click();
  let dialog = page.getByRole("dialog", { name: "Duplicate AIEngineer Event 1" });
  await expect(dialog.getByLabel(/Event name/)).toHaveValue("AIEngineer Event 1 copy");
  await expect(dialog.getByLabel("Attendance format")).toHaveValue("hybrid");
  await expect(dialog.getByLabel("Location")).toHaveValue("Bengaluru");
  await dialog.getByRole("button", { name: "Save draft" }).click();
  await expect(page.locator("#status")).toHaveText("Draft saved.");

  await page.getByRole("button", { name: "Create event", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Create an event" });
  const dialogGeometry = await dialog.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right, width: rect.width, scrollWidth: element.scrollWidth };
  });
  expect(dialogGeometry.left).toBeGreaterThanOrEqual(0);
  expect(dialogGeometry.right).toBeLessThanOrEqual(390);
  expect(dialogGeometry.scrollWidth).toBeLessThanOrEqual(Math.ceil(dialogGeometry.width));
  await expect(dialog.locator(".dialog-actions")).toBeVisible();
  await dialog.getByLabel(/Event name/).fill("AIEngineer Summit 2027");
  await dialog.getByLabel(/Time zone/).fill("Asia/Kolkata");
  await dialog.getByLabel("Attendance format").selectOption("hybrid");
  await dialog.getByLabel("Start date").fill("2027-10-12");
  await dialog.getByLabel("End date").fill("2027-10-13");
  await dialog.getByLabel("Location").fill("Bengaluru");
  await dialog.getByLabel(/Description/).fill("A production AI engineering conference.");
  await dialog.locator(".advanced-settings > summary").click();
  await dialog.getByLabel(/Event website/).fill("https://aiengineer.example/summit-2027");
  await dialog.getByLabel(/Accent color/).fill("#ffff00");
  await expect(dialog.locator("#public-brand-preview-title")).toHaveText("AIEngineer Summit 2027");
  await expect(dialog.locator("#public-brand-preview-date")).toContainText("Oct 12, 2027");
  await expect(dialog.locator("#public-brand-preview-location")).toContainText("Bengaluru");
  await expect(dialog.locator("#public-brand-preview-website")).toHaveText("aiengineer.example");
  const previewColor = await dialog.locator("#public-brand-preview-card").evaluate((element) => {
    const style = getComputedStyle(element);
    return style.borderTopColor;
  });
  expect(previewColor).toBe("rgb(255, 255, 0)");
  const save = dialog.getByRole("button", { name: "Create event", exact: true });
  await save.click();
  await expect(dialog.locator("#event-dialog-status")).toContainText("Reference: request-1");
  await expect(save).toBeEnabled();
  await expect(dialog).toBeVisible();
});

test("newer event searches cannot be replaced by a slower older response", async ({ page }) => {
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      authenticated: true, user_id: "user", email: "organizer@example.com", display_name: "User Zero",
      profile_complete: true, csrf_token: "csrf", account_roles: ["organizer"], active_role: "organizer",
      default_role: "organizer", organization_id: organizationId, organization_name: "AIEngineer",
      organization_access: [{ organization_id: organizationId, roles: ["organization_admin"] }], event_access: [],
    }),
  }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [{ id: organizationId, name: "AIEngineer", status: "active", version: 1 }] }),
  }));
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events?*`, async (route) => {
    const query = new URL(route.request().url()).searchParams.get("q") || "";
    if (query === "older") await new Promise((resolve) => setTimeout(resolve, 650));
    const name = query === "newer" ? "Newer result" : query === "older" ? "Older result" : "Initial event";
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{
      id: query || "initial", organization_id: organizationId, name, status: "active", version: 1,
      starts_at_ms: Date.UTC(2027, 1, 1), ends_at_ms: Date.UTC(2027, 1, 2), time_zone: "Asia/Kolkata",
      delivery_mode: "hybrid", location: "Bengaluru", description: "Event description",
    }], next_cursor: null }) });
  });

  await page.goto("/admin/events");
  const search = page.getByRole("searchbox", { name: "Search events" });
  await search.fill("older");
  await page.waitForTimeout(350);
  await search.fill("newer");
  await expect(page.getByRole("heading", { name: "Newer result" })).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page.getByRole("heading", { name: "Older result" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Newer result" })).toBeVisible();
});
