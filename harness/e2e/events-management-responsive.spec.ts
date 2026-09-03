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

const organizations = { data: [{ id: organizationId, name: "AIEngineer", status: "active", version: 1, event_count: 12, pending_review_count: 0 }] };

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
  await page.route("**/api/v1/account/profile", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ time_zone: "America/Los_Angeles" }) }));
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
  // Home titles itself with the selected organization; see admin-home-responsive.spec.
  await expect(page.locator("#workspace-title")).toHaveText("AIEngineer");
  await expect(page.locator(".organizer-home-event-row")).toHaveCount(12);
  const firstEvent = page.locator('.organizer-home-event-row[data-event-id="event-0"]');
  await expect(firstEvent.getByRole("link", { name: "AIEngineer Event 1", exact: true })).toHaveAttribute("href", "/admin/events/event-0");
  await expect(firstEvent.getByText("CFP open · 1 proposal", { exact: true })).toBeVisible();
  await expect(firstEvent.getByText("Agenda live · 0 speakers", { exact: true })).toBeVisible();
  await expect(firstEvent.getByRole("link", { name: "Agenda live · 0 speakers — view public agenda for AIEngineer Event 1" })).toHaveAttribute("href", "/events/event-0/schedule");
  // Workflow tools live inside the row's More disclosure.
  await expect(firstEvent.getByRole("link", { name: "Manage CFP", exact: true })).toBeHidden();
  await firstEvent.locator("summary").click();
  await expect(firstEvent.getByRole("link", { name: "Manage CFP", exact: true })).toHaveAttribute("href", "/admin/events/event-0/cfp");
  await expect(firstEvent.getByRole("link", { name: "Manage agenda", exact: true })).toHaveAttribute("href", "/admin/events/event-0/agenda");
  await expect(firstEvent.getByRole("link", { name: "Speakers", exact: true })).toHaveAttribute("href", "/admin/events/event-0/speakers");
  await expect(firstEvent.getByRole("link", { name: "Reviewers", exact: true })).toHaveAttribute("href", "/admin/events/event-0/reviewers");
  await expect(firstEvent.getByRole("link", { name: "Manage", exact: true })).toHaveAttribute("href", "/admin/events/event-0/settings");
  await expect(firstEvent.getByRole("link", { name: "Clone" })).toHaveAttribute("href", "/admin/events/new?source=event-0");
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
  await expect(page.getByLabel("Time zone")).toHaveValue("America/Los_Angeles");
  await page.getByLabel("Time zone").selectOption("Asia/Kolkata");
  await page.getByLabel("Attendance format").selectOption("hybrid");
  await page.locator('input[name="start_date"]').fill("2027-10-12");
  await page.locator('input[name="end_date"]').fill("2027-10-13");
  await page.getByLabel("Location").fill("Bengaluru");
  await page.getByLabel("Description").fill("A production AI engineering conference.");
  await page.getByText("Branding", { exact: true }).click();
  const publicHeaderPreview = page.locator(".event-editor__public-preview");
  await expect(publicHeaderPreview.locator(".public-event-masthead__name")).toHaveText("AIEngineer Summit 2027");
  await expect(publicHeaderPreview.locator(".public-event-masthead__nav")).toHaveCount(0);
  await page.getByLabel("Accent color").fill("#7847d6");
  await expect(publicHeaderPreview.locator(".public-event-masthead__band")).toHaveCSS("min-height", "64px");
  await page.getByLabel("Event website").fill("https://aiengineer.example/summit-2027");
  await page.locator("#logo-file").setInputFiles({ name: "event-logo.png", mimeType: "image/png", buffer: Buffer.from("event-logo") });
  await page.getByRole("button", { name: "Upload logo" }).click();
  await expect(page.locator("#logo-status")).toContainText("Save changes to use this image");

  const save = page.getByRole("button", { name: "Create event and continue setup" });
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
  await expect(page.getByRole("link", { name: "Newer result", exact: true })).toBeVisible();
  await page.waitForTimeout(500);
  await expect(page.getByRole("link", { name: "Older result", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Newer result", exact: true })).toBeVisible();
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

test("a scheduled-event timezone refusal explains the restriction and preserves unsaved edits", async ({ page }) => {
  await mockIdentity(page);
  let eventReads = 0;
  let attemptedSave = false;
  await page.route("**/api/v1/admin/events/event-0", async (route) => {
    if (route.request().method() === "GET") {
      eventReads += 1;
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(eventFixture()) });
      return;
    }
    expect(route.request().method()).toBe("PATCH");
    expect(route.request().postDataJSON()).toMatchObject({ name: "Unsaved organizer edit", time_zone: "UTC", version: 1 });
    attemptedSave = true;
    await route.fulfill({
      status: 409,
      headers: { "x-conflict-type": "event_timezone_has_agenda" },
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "conflict", message: "This event already has scheduled sessions. Changing its time zone is not supported safely yet. Keep the current time zone to save your other changes. Nothing was saved." } }),
    });
  });
  await page.goto("/admin/events/event-0/settings");
  await expect(page.getByLabel("Event name")).toHaveValue("AIEngineer Event 1");
  const readsBeforeSave = eventReads;
  await page.getByLabel("Event name").fill("Unsaved organizer edit");
  await page.getByLabel("Time zone").selectOption("UTC");
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.locator("#editor-status")).toContainText("Keep the current time zone");
  await expect(page.locator("#editor-status")).not.toContainText("Someone else saved");
  await expect(page.getByLabel("Event name")).toHaveValue("Unsaved organizer edit");
  await expect(page.getByLabel("Time zone")).toHaveValue("UTC");
  await expect(page.getByRole("button", { name: "Save changes", exact: true })).toBeEnabled();
  expect(attemptedSave).toBe(true);
  expect(eventReads).toBe(readsBeforeSave);
});

test("the time zone filter narrows the flat select, explains the offset, and never submits", async ({ page }) => {
  await mockIdentity(page);
  let createRequests = 0;
  await page.route(`**/api/v1/admin/organizations/${organizationId}/events`, async (route) => {
    createRequests += 1;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "service_unavailable", message: "Event service is temporarily unavailable." }, request_id: "request-2" }),
    });
  });

  await page.goto(`/admin/events/new?organization_id=${organizationId}`);
  const timeZone = page.getByLabel("Time zone");
  await expect(timeZone).toHaveValue("America/Los_Angeles");
  await expect(page.locator("#event-time-zone-context")).toContainText("America/Los Angeles");
  // Every required field is complete, so an Enter that reached the form would create the event.
  await page.getByLabel("Event name").fill("Filter rehearsal");
  await page.getByLabel("Attendance format").selectOption("virtual");
  await page.getByLabel("Location").fill("Online");
  await page.getByLabel("Description").fill("A rehearsal for the zone filter.");
  await page.locator('input[name="start_date"]').fill("2027-10-12");
  await page.locator('input[name="end_date"]').fill("2027-10-13");
  await expect(page.locator("#nav-state-general")).toHaveText("Complete");
  await expect(page.locator("#nav-state-date-time")).toHaveText("2 days");

  const filter = page.getByLabel("Find a city or region");
  await filter.fill("kolk");
  await expect(page.locator("#time-zone-filter-status")).toHaveText(/^1 of \d+ zones match · Enter selects it$/);
  await expect(timeZone.locator('option[value="Asia/Kolkata"]')).toHaveCount(1);
  await expect(timeZone).toHaveValue("America/Los_Angeles");
  // The required fields above already made the form dirty; typing in the
  // filter must not change that state or the selected zone.
  await expect(page.locator("#save-state")).toHaveText("Unsaved changes");
  // Enter on a single match selects it and moves focus to the select; neither
  // that Enter nor another on the select submits the form.
  await filter.press("Enter");
  await expect(timeZone).toBeFocused();
  await expect(timeZone).toHaveValue("Asia/Kolkata");
  await expect(page.locator("#save-state")).toHaveText("Unsaved changes");
  await timeZone.press("Enter");
  expect(createRequests).toBe(0);
  await expect(page.locator("#event-time-zone-context")).toContainText("Asia/Kolkata (UTC+05:30)");
  await expect(page.locator("#date-time-preview")).toContainText("Asia/Kolkata");
  await filter.fill("+05:30");
  await expect(timeZone.locator('option[value="Asia/Kolkata"]')).toHaveCount(1);
  await expect(timeZone).toHaveValue("Asia/Kolkata");
  await filter.press("Escape");
  await expect(filter).toHaveValue("");
  await expect(page.locator("#time-zone-filter-status")).toHaveText("");
  await expect(timeZone.locator('option[value="UTC"]')).toHaveCount(1);
  await expect(timeZone).toHaveValue("Asia/Kolkata");

  await page.locator('#event-editor-nav [data-section-link="branding"]').click();
  await expect(page.locator("#branding")).toHaveAttribute("open", "");
  await expect(page.locator('#event-editor-nav [data-section-link="branding"]')).toHaveAttribute("aria-current", "location");
  await expect(page).toHaveURL(/#branding$/);

  await page.getByRole("button", { name: "Create event and continue setup" }).click();
  await expect(page.locator("#editor-status")).toContainText("Reference: request-2");
  expect(createRequests).toBe(1);
});

test("routed settings keep a live summary and open the deep-linked section", async ({ page }) => {
  await mockIdentity(page);
  await page.route("**/api/v1/admin/events/event-0", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(eventFixture()) }));

  await page.goto("/admin/events/event-0/settings#email");
  await expect(page.getByRole("heading", { name: "AIEngineer Event 1" })).toBeVisible();
  await expect(page.locator("#email")).toHaveAttribute("open", "");
  await expect(page.locator('#event-editor-nav [data-section-link="email"]')).toHaveAttribute("aria-current", "location");
  await expect(page.locator("#email-summary-note")).toHaveText("Optional · uses SessionBuddy <events@example.test>");
  await expect(page.locator("#branding")).not.toHaveAttribute("open", "");

  const summary = page.locator("#event-summary-panel");
  await expect(summary).toBeVisible();
  await expect(page.locator("#summary-name")).toHaveText("AIEngineer Event 1");
  await expect(page.locator("#summary-status")).toHaveText("Active");
  await expect(page.locator("#lifecycle-state")).toHaveText("active");
  await expect(page.locator("#nav-state-lifecycle")).toHaveText("Active");
  await expect(page.locator("#summary-dates")).toContainText("Asia/Kolkata");
  await expect(page.locator("#summary-location")).toHaveText("Bengaluru · Hybrid");
  await expect(page.locator("#summary-organization")).toHaveText("AIEngineer");
  await expect(page.locator("#nav-state-date-time")).toHaveText("2 days");

  await page.getByLabel("Event name").fill("Renamed summit");
  await expect(page.locator("#summary-name")).toHaveText("Renamed summit");
  // The heading only changes once the rename is saved.
  await expect(page.getByRole("heading", { name: "AIEngineer Event 1" })).toBeVisible();
  await page.getByLabel("Event name").fill("");
  await expect(page.locator("#summary-name")).toHaveText("Untitled event");
  await expect(page.locator("#nav-state-general")).toHaveText("Needs details");
  await expect(page.locator("#save-state")).toHaveText("Unsaved changes");
});
