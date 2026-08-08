import { expect, test } from "@playwright/test";

test.describe("public smoke checks", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("public homepage presents the product and role entry points", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    await expect(page).toHaveTitle(/SessionBuddy/);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", {
      level: 1,
      name: "Turn a call for speakers into a schedule everyone can trust.",
    })).toBeVisible();
    await expect(page.getByRole("link", { name: /Start organizing/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Open organizer workspace/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Open review workspace/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Open speaker portal/ })).toBeVisible();
  });

  test("public homepage remains usable at a mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Start organizing/ })).toBeVisible();
    const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(documentWidth).toBeLessThanOrEqual(390);
  });

  test("an expired magic link offers browser recovery instead of JSON", async ({ page }) => {
    const expiredToken = "expired-link".padEnd(32, "x");
    const response = await page.goto(`/auth/verify?token=${expiredToken}`);
    expect(response?.status()).toBe(404);
    await expect(page).toHaveTitle(/Sign-in link unavailable/);
    await expect(page.getByRole("heading", {
      level: 1,
      name: "This sign-in link can’t be used.",
    })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in", exact: true })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("resource_not_found");
  });

  test("a sent sign-in link locks the email until the user chooses to change it", async ({ page }) => {
    let requestCount = 0;
    await page.route("**/api/v1/auth/magic-links", async (route) => {
      requestCount += 1;
      await route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
    });

    const response = await page.goto("/sign-in");
    expect(response?.ok()).toBeTruthy();
    const email = page.getByRole("textbox", { name: "Email address" });
    const send = page.getByRole("button", { name: "Send sign-in link" });
    await email.fill("speaker@example.com");
    await send.click();

    await expect(page.getByRole("status")).toContainText("Check your email");
    await expect(email).toBeDisabled();
    await expect(page.getByRole("button", { name: "Email sent" })).toBeDisabled();
    expect(requestCount).toBe(1);

    await page.getByRole("button", { name: "Use a different email" }).click();
    await expect(email).toBeEnabled();
    await expect(email).toBeFocused();
    await expect(page.getByRole("button", { name: "Send sign-in link" })).toBeEnabled();
  });
});

test.describe("competition gap-closure APIs", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("resources, custom tasks, and the read-only event feed work together", async ({ page }, testInfo) => {
    const eventId = "22222222-2222-4222-8222-222222222222";
    const origin = "http://localhost:8787";
    const sourceIp = `198.51.100.${testInfo.workerIndex + 20}`;
    await page.request.post("/api/v1/demo/speaker-session", { headers: { "cf-connecting-ip": sourceIp } });
    const adminSession = await page.request.post("/api/v1/demo/session", { headers: { "cf-connecting-ip": sourceIp } });
    expect(adminSession.ok()).toBeTruthy();
    const csrf = (await adminSession.json()).csrf_token;
    const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const mutationHeaders = (suffix: string) => ({
      "content-type": "application/json",
      "x-csrf-token": csrf,
      "idempotency-key": `${testInfo.project.name}-${unique}-${suffix}`,
      origin,
    });

    const resource = await page.request.post(`/api/v1/admin/events/${eventId}/resources`, {
      headers: mutationHeaders("resource"),
      data: { title: `Speaker guide ${unique}`, slug: `speaker-guide-${unique}`, summary: "Contest rehearsal", body_text: "Arrive 20 minutes early.", embed_url: null, status: "published", sort_order: 10 },
    });
    expect(resource.status()).toBe(201);

    const targets = await page.request.get(`/api/v1/admin/events/${eventId}/speaker-targets`);
    expect(targets.ok()).toBeTruthy();
    const accepted = (await targets.json()).data.find((target: { selection_status: string }) => target.selection_status === "accepted");
    expect(accepted).toBeTruthy();
    const task = await page.request.post(`/api/v1/admin/events/${eventId}/speaker-tasks`, {
      headers: mutationHeaders("task"),
      data: { event_speaker_id: accepted.event_speaker_id, submission_id: null, title: `Confirm arrival ${unique}`, help_text: "Tell the team you are ready.", due_at_ms: null, fields: [{ key: "ready", label: "I am ready", type: "checkbox", required: true, choices: [] }] },
    });
    expect(task.status()).toBe(201);

    const tokenResponse = await page.request.post(`/api/v1/admin/events/${eventId}/integrations/accelevents/tokens`, {
      headers: mutationHeaders("token"), data: { label: `E2E ${unique}` },
    });
    expect(tokenResponse.status()).toBe(201);
    const token = (await tokenResponse.json()).token;
    expect(token.length).toBeGreaterThan(32);
    const feed = await page.request.post(`/v1/event/${eventId}/speakers`, { headers: { "x-access-token": token } });
    expect(feed.ok()).toBeTruthy();
    expect((await feed.json()).results.length).toBeGreaterThan(0);
  });
});

test.describe("administration empty states", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("an administrator can start with an organization and no events", async ({ page }) => {
    let createdEvent: Record<string, unknown> | null = null;
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: null,
          csrf_token: "browser-test-csrf",
        }),
      });
    });
    await page.route("**/api/v1/admin/organizations", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          data: [{
            id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            name: "Empty Event Organization",
            status: "active",
            version: 1,
          }],
        }),
      });
    });
    await page.route("**/api/v1/admin/organizations/*/events", async (route) => {
      if (route.request().method() === "POST") {
        createdEvent = route.request().postDataJSON();
        await route.fulfill({ status: 201, contentType: "application/json", body: "{}" });
        return;
      }
      const data = createdEvent ? [{
        id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ...createdEvent,
        status: "draft",
        version: 1,
      }] : [];
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data }) });
    });

    const response = await page.goto("/admin/events");
    expect(response?.ok()).toBeTruthy();
    await expect(page.getByText(/No events yet\./)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Create an event" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Event name" })).toHaveValue("");
    await expect(page.getByRole("textbox", { name: "Event name" })).toHaveAttribute(
      "placeholder",
      "e.g. Community Tech Summit 2026",
    );
    await expect(page.getByRole("combobox", { name: "Attendance format" })).toHaveValue("");
    await expect(page.getByText("Choose one explicitly; SessionBuddy will not assume a format.")).toBeVisible();
    const timeZone = page.getByLabel("Event time zone");
    await expect(timeZone).not.toHaveValue("");
    await expect(page.getByLabel("Start date")).toHaveValue("");
    await expect(page.getByLabel("Start time")).toHaveValue("09:00");
    await expect(page.getByLabel("End date")).toHaveValue("");
    await expect(page.getByLabel("End time")).toHaveValue("17:00");
    await page.getByLabel("Start date").fill("2026-09-12");
    await page.getByLabel("Start date").dispatchEvent("change");
    await expect(page.getByLabel("End date")).toHaveValue("2026-09-12");
    await expect(page.getByRole("status").filter({ hasText: /2026|Sep/ })).toContainText(await timeZone.inputValue());
    await expect(page.getByRole("button", { name: "Create event" })).toBeEnabled();
    await timeZone.fill("Asia/Kolkata");
    await page.getByRole("textbox", { name: "Event name" }).fill("Timezone Rehearsal");
    await page.getByRole("combobox", { name: "Attendance format" }).selectOption("in_person");
    await page.getByRole("button", { name: "Create event" }).click();
    await expect(page.getByRole("status").first()).toHaveText("Event created.");
    expect(createdEvent).toMatchObject({
      name: "Timezone Rehearsal",
      starts_at_ms: Date.UTC(2026, 8, 12, 3, 30),
      ends_at_ms: Date.UTC(2026, 8, 12, 11, 30),
      time_zone: "Asia/Kolkata",
    });
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(page.getByLabel("Start date")).toHaveValue("2026-09-12");
    await expect(page.getByLabel("Start time")).toHaveValue("09:00");
    await expect(page.getByLabel("End date")).toHaveValue("2026-09-12");
    await expect(page.getByLabel("End time")).toHaveValue("17:00");
  });

  test("an invitation form survives its asynchronous request and resets", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    let invitations: Array<Record<string, unknown>> = [];
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: eventId,
          csrf_token: "browser-test-csrf",
        }),
      });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/invitations`, async (route) => {
      if (route.request().method() === "POST") {
        invitations = [{
          id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          email: "speaker@example.com",
          role: "speaker",
          status: "pending",
        }];
        await route.fulfill({ status: 201, contentType: "application/json", body: "{}" });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: invitations }),
      });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/members`, async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) });
    });

    const response = await page.goto(`/admin/events/${eventId}/access`);
    expect(response?.ok()).toBeTruthy();
    await page.getByRole("textbox", { name: "Email address" }).fill("speaker@example.com");
    await page.getByRole("button", { name: "Invite" }).click();

    await expect(page.getByRole("status")).toHaveText("Event access is up to date.");
    await expect(page.getByRole("textbox", { name: "Email address" })).toHaveValue("");
    await expect(page.getByText("speaker@example.com · speaker · pending")).toBeVisible();
  });
});

test.describe("dynamic form drafts", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("restoring a draft recomputes conditional field state", async ({ page }) => {
    const slug = "conditional-rehearsal";
    const pageErrors: string[] = [];
    let submittedBody: unknown = null;
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.addInitScript(() => {
      if (!crypto.randomUUID) {
        Object.defineProperty(crypto, "randomUUID", {
          configurable: true,
          value: () => "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        });
      }
    });
    await page.route(`**/api/v1/forms/${slug}/draft`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          version: 1,
          answers: {
            speaker_name: "Example Speaker",
            speaker_email: "speaker@example.com",
            proposal_title: "Conditional forms",
            proposal_abstract: "How conditional form submissions work.",
            session_format: "Workshop",
            workshop_requirements: "Bring a laptop",
          },
        }),
      });
    });
    await page.route(`**/api/v1/forms/${slug}/submissions`, async (route) => {
      submittedBody = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ id: "ffffffff-ffff-4fff-8fff-ffffffffffff", status: "submitted" }),
      });
    });
    await page.route(`**/api/v1/forms/${slug}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          slug,
          welcome_text: "Test conditional restoration.",
          fields: [
            {
              key: "speaker_name",
              label: "Speaker name",
              type: "text",
              required: true,
              choices: [],
            },
            {
              key: "speaker_email",
              label: "Email",
              type: "email",
              required: true,
              choices: [],
            },
            {
              key: "proposal_title",
              label: "Proposal title",
              type: "text",
              required: true,
              choices: [],
            },
            {
              key: "proposal_abstract",
              label: "Proposal abstract",
              type: "textarea",
              required: true,
              choices: [],
            },
            {
              key: "session_format",
              label: "Session format",
              type: "select",
              required: true,
              choices: ["Talk", "Workshop"],
            },
            {
              key: "workshop_requirements",
              label: "Workshop requirements",
              type: "textarea",
              required: true,
              choices: [],
            },
          ],
          conditions: [{
            source_key: "session_format",
            target_key: "workshop_requirements",
            operator: "equals",
            value: "Workshop",
          }],
        }),
      });
    });
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ csrf_token: "browser-test-csrf" }),
      });
    });

    const response = await page.goto(`/cfp/${slug}`);
    expect(response?.ok()).toBeTruthy();
    await page.waitForTimeout(500);
    expect(pageErrors).toEqual([]);
    await expect(page.getByRole("status").first()).toHaveText(
      "Your saved draft has been restored.",
    );
    await expect(page.getByRole("combobox", { name: "Session format" })).toHaveValue("Workshop");
    const conditional = page.getByRole("textbox", { name: "Workshop requirements" });
    await expect(conditional).toBeEnabled();
    await expect(conditional).toHaveValue("Bring a laptop");
    await expect(conditional).toHaveAttribute("required", "");

    await page.getByRole("button", { name: "Review proposal" }).click();
    await page.getByRole("button", { name: "Confirm submission" }).click();
    await expect(page.getByRole("status").first()).toHaveText(
      "Proposal submitted successfully.",
    );
    expect(submittedBody).toEqual({
      speaker_name: "Example Speaker",
      speaker_email: "speaker@example.com",
      proposal_title: "Conditional forms",
      proposal_abstract: "How conditional form submissions work.",
      answers: {
        speaker_name: "Example Speaker",
        speaker_email: "speaker@example.com",
        proposal_title: "Conditional forms",
        proposal_abstract: "How conditional form submissions work.",
        session_format: "Workshop",
        workshop_requirements: "Bring a laptop",
      },
    });
  });
});
