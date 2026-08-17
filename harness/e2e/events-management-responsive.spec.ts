import { expect, test, type Page } from "@playwright/test";

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const session = {
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
  organization_access: [{ organization_id: organizationId, organization_name: "AIEngineer", permissions: ["owner"] }],
  event_access: [],
};

const organizations = { data: [{ id: organizationId, name: "AIEngineer", status: "active", version: 1 }] };

function eventFixture(index = 0) {
  return {
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
    cfp_status: index ? "draft" : "published",
    accent_color: "#3159d9",
    logo_url: null,
    cover_image_url: null,
    website_url: null,
    email_sender_name: null,
    email_reply_to: null,
  };
}

async function mockIdentity(page: Page) {
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(organizations) }));
  await page.route(`**/api/v1/admin/organizations/${organizationId}/activities`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
}

test("the mobile event ledger leads to a usable routed editor and preserves a failed save", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockIdentity(page);
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events?*`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: Array.from({ length: 12 }, (_, index) => eventFixture(index)), next_cursor: "more" }),
  }));
  await page.route(`**/api/v1/admin/organizations/${organizationId}/event-assets/logo`, async (route) => {
    expect(route.request().headers()["content-type"]).toBe("image/png");
    expect(route.request().headers()["x-csrf-token"]).toBe("csrf");
    expect(route.request().postDataBuffer()).toEqual(Buffer.from("event-logo"));
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ asset_url: "/event-branding/test-logo.png", kind: "logo" }),
    });
  });
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events`, async (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toMatchObject({
      name: "AIEngineer Summit 2027",
      status: "active",
      logo_url: "/event-branding/test-logo.png",
      time_zone: "Asia/Kolkata",
    });
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "service_unavailable", message: "Event service is temporarily unavailable." },
        request_id: "request-1",
      }),
    });
  });

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Events", exact: true })).toBeVisible();
  await expect(page.locator(".organizer-home-event-row")).toHaveCount(12);
  const firstEvent = page.locator('.organizer-home-event-row[data-event-id="event-0"]');
  await expect(firstEvent.getByRole("link", { name: "AIEngineer Event 1", exact: true })).toHaveAttribute("href", "/admin/events/event-0");
  await expect(firstEvent.getByRole("link", { name: "1 proposal" })).toHaveAttribute("href", "/admin/events/event-0/submissions");
  await expect(firstEvent.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/admin/events/event-0/settings");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

  await page.getByRole("link", { name: "Create event" }).click();
  await expect(page).toHaveURL(new RegExp(`/admin/events/new\\?organization_id=${organizationId}$`));
  await expect(page.getByRole("heading", { name: "Create event" })).toBeVisible();
  const editor = page.locator("#event-editor-form");
  const editorGeometry = await editor.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right, width: rect.width, scrollWidth: element.scrollWidth };
  });
  expect(editorGeometry.left).toBeGreaterThanOrEqual(0);
  expect(editorGeometry.right).toBeLessThanOrEqual(390);
  expect(editorGeometry.scrollWidth).toBeLessThanOrEqual(Math.ceil(editorGeometry.width));

  await page.getByLabel("Event name").fill("AIEngineer Summit 2027");
  await page.getByLabel("Time zone").fill("Asia/Kolkata");
  await page.getByLabel("Attendance format").selectOption("hybrid");
  await page.locator('input[name="start_date"]').fill("2027-10-12");
  await page.locator('input[name="end_date"]').fill("2027-10-13");
  await page.getByLabel("Location").fill("Bengaluru");
  await page.getByLabel("Description").fill("A production AI engineering conference.");
  await page.getByText("Branding", { exact: true }).click();
  await page.getByLabel("Event website").fill("https://aiengineer.example/summit-2027");
  await page.locator("#logo-file").setInputFiles({ name: "event-logo.png", mimeType: "image/png", buffer: Buffer.from("event-logo") });
  await page.getByRole("button", { name: "Upload logo" }).click();
  await expect(page.locator("#logo-status")).toContainText("Save changes to use this image");

  const save = page.getByRole("button", { name: "Create active event" });
  await save.click();
  await expect(page.locator("#editor-status")).toContainText("Reference: request-1");
  await expect(save).toBeEnabled();
  await expect(page.getByLabel("Event name")).toHaveValue("AIEngineer Summit 2027");
  await expect(page).toHaveURL(new RegExp("/admin/events/new"));
});

test("a slower older event search cannot replace a newer response", async ({ page }) => {
  await mockIdentity(page);
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events?*`, async (route) => {
    const query = new URL(route.request().url()).searchParams.get("q") || "";
    if (query === "older") await new Promise((resolve) => setTimeout(resolve, 650));
    const name = query === "newer" ? "Newer result" : query === "older" ? "Older result" : "Initial event";
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ ...eventFixture(), id: query || "initial", name }], next_cursor: null }) });
  });

  await page.goto("/admin");
  const search = page.getByRole("searchbox", { name: "Search" });
  await search.fill("older");
  await page.waitForTimeout(350);
  await search.fill("newer");
  await expect(page.getByRole("link", { name: "Newer result" })).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page.getByRole("link", { name: "Older result" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Newer result" })).toBeVisible();
  await expect(page).toHaveURL(/q=newer/);
});

test("an archived event keeps its past dates when saved from routed settings", async ({ page }) => {
  const historicalEvent = {
    ...eventFixture(),
    id: "historical-event",
    name: "AIEngineer Archive 2025",
    status: "archived",
    version: 4,
    starts_at_ms: Date.UTC(2025, 5, 3, 3, 30),
    ends_at_ms: Date.UTC(2025, 5, 4, 11, 30),
    delivery_mode: "in_person",
    description: "A completed conference retained for the public archive.",
  };
  await mockIdentity(page);
  await page.route("**/api/v1/admin/events/historical-event", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(historicalEvent) });
      return;
    }
    expect(route.request().method()).toBe("PATCH");
    expect(route.request().postDataJSON()).toMatchObject({
      name: "AIEngineer Historical Archive",
      status: "archived",
      starts_at_ms: historicalEvent.starts_at_ms,
      ends_at_ms: historicalEvent.ends_at_ms,
      version: 4,
    });
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...historicalEvent, name: "AIEngineer Historical Archive", version: 5 }),
    });
  });

  await page.goto("/admin/events/historical-event/settings");
  await expect(page.getByRole("heading", { name: "AIEngineer Archive 2025" })).toBeVisible();
  await expect(page.locator('input[name="start_date"]')).toHaveValue("2025-06-03");
  await expect(page.locator('input[name="end_date"]')).toHaveValue("2025-06-04");
  await expect(page.locator("#lifecycle-state")).toHaveText("archived");
  await page.getByLabel("Event name").fill("AIEngineer Historical Archive");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.locator("#editor-status")).toHaveText("Event saved.");
  await expect(page.locator("#lifecycle-state")).toHaveText("archived");
});
