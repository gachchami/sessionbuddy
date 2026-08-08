import { expect, test } from "@playwright/test";

test.describe("public smoke checks", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("public homepage presents the product and role entry points", async ({ page }) => {
    await page.route("**/api/v1/setup/status", async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: true }) });
    });
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    await expect(page).toHaveTitle(/SessionBuddy/);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", {
      level: 1,
      name: "Plan your conference program in one place.",
    })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in", exact: true }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Events" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Reviews" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Speaker portal", exact: true }).first()).toBeVisible();
  });

  test("public homepage remains usable at a mobile viewport", async ({ page }) => {
    await page.route("**/api/v1/setup/status", async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: true }) });
    });
    await page.setViewportSize({ width: 390, height: 844 });
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in", exact: true }).first()).toBeVisible();
    const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(documentWidth).toBeLessThanOrEqual(390);
  });

  test("an authenticated administrator never sees a sign-in call to action", async ({ page }) => {
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: null,
          csrf_token: "browser-test-csrf",
          email: "admin@example.com",
          display_name: "Admin User",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            roles: ["organization_admin"],
          }],
          event_access: [],
        }),
      });
    });

    await page.goto("/");
    await expect(page.getByRole("link", { name: "Sign in", exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Open app", exact: true })).toHaveCount(3);
    await expect(page.locator("[data-auth-entry]")).toHaveCount(2);
    await expect(page.locator("[data-auth-entry]")).toHaveText(["Open app", "Open app"]);
    await expect(page.locator("[data-auth-entry]").first()).toHaveAttribute("href", "/admin");
    await expect(page.locator("[data-auth-entry]").last()).toHaveAttribute("href", "/admin");
  });

  test("an expired magic link offers browser recovery instead of JSON", async ({ page }) => {
    const expiredToken = "expired-link".padEnd(32, "x");
    const response = await page.goto(`/auth/verify?token=${expiredToken}`);
    expect(response?.status()).toBe(404);
    await expect(page).toHaveTitle(/Sign-in link unavailable/);
    await expect(page.getByRole("heading", {
      level: 1,
      name: "This link has expired",
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
    const send = page.getByRole("button", { name: "Sign in" });
    await email.fill("speaker@example.com");
    await send.click();

    await expect(email).toBeHidden();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
    expect(requestCount).toBe(1);

    await page.getByRole("button", { name: "Change email" }).click();
    await expect(email).toBeEnabled();
    await expect(email).toBeFocused();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  test("an empty instance starts with secured administrator onboarding", async ({ page }) => {
    let bootstrapBody: Record<string, unknown> | null = null;
    let bootstrapKey = "";
    await page.route("**/api/v1/setup/status", async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: false }) });
    });
    await page.route("**/api/v1/bootstrap", async (route) => {
      bootstrapBody = route.request().postDataJSON();
      bootstrapKey = route.request().headers()["x-bootstrap-token"] || "";
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ organization_id: "org", event_id: null, admin_user_id: "user" }) });
    });
    await page.route("**/api/v1/auth/magic-links", async (route) => {
      await route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
    });

    await page.goto("/setup");
    await expect(page.getByRole("heading", { name: "Set up SessionBuddy." })).toBeVisible();
    await page.getByLabel("Organization name").fill("Noneli Events");
    await page.getByLabel("Administrator name").fill("Devang Hanushali");
    await page.getByLabel("Administrator email").fill("me@example.com");
    await page.getByLabel("Deployment setup key").fill("x".repeat(40));
    await page.getByRole("button", { name: "Complete setup" }).click();

    await expect(page.getByRole("status")).toContainText("Setup complete");
    expect(bootstrapKey).toBe("x".repeat(40));
    expect(bootstrapBody).toMatchObject({
      organization_name: "Noneli Events",
      admin_name: "Devang Hanushali",
      admin_email: "me@example.com",
    });
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
          email: "admin@example.com",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            roles: ["organization_admin"],
          }],
          event_access: [],
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
    await page.getByRole("button", { name: "Create event" }).click();
    await expect(page.getByRole("heading", { name: "Create an event" })).toBeVisible();
    const eventDialog = page.getByRole("dialog", { name: "Create an event" });
    await expect(page.getByRole("textbox", { name: "Event name" })).toHaveValue("");
    await expect(page.getByRole("textbox", { name: "Event name" })).toHaveAttribute(
      "placeholder",
      "Community Tech Summit 2026",
    );
    await expect(page.getByRole("combobox", { name: "Attendance format" })).toHaveValue("");
    const timeZone = page.getByLabel("Time zone");
    await expect(timeZone).not.toHaveValue("");
    await expect(page.getByLabel("Start date")).toHaveValue("");
    await expect(page.getByLabel("Start time")).toHaveValue("09:00");
    await expect(page.getByLabel("End date")).toHaveValue("");
    await expect(page.getByLabel("End time")).toHaveValue("17:00");
    await page.getByLabel("Start date").fill("2026-09-12");
    await page.getByLabel("Start date").dispatchEvent("change");
    await expect(page.getByLabel("End date")).toHaveValue("2026-09-12");
    await expect(page.getByRole("status").filter({ hasText: /2026|Sep/ })).toContainText(await timeZone.inputValue());
    await expect(eventDialog.getByRole("button", { name: "Create event" })).toBeEnabled();
    await timeZone.fill("Asia/Kolkata");
    await page.getByRole("textbox", { name: "Event name" }).fill("Timezone Rehearsal");
    await page.getByRole("textbox", { name: "Sender name" }).fill("Program Team");
    await page.getByRole("textbox", { name: "Reply-to email" }).fill("program@example.test");
    await page.getByRole("combobox", { name: "Attendance format" }).selectOption("in_person");
    await eventDialog.getByRole("button", { name: "Create event" }).click();
    await expect(page.getByRole("status").first()).toHaveText("Event created.");
    expect(createdEvent).toMatchObject({
      name: "Timezone Rehearsal",
      starts_at_ms: Date.UTC(2026, 8, 12, 3, 30),
      ends_at_ms: Date.UTC(2026, 8, 12, 11, 30),
      time_zone: "Asia/Kolkata",
      email_sender_name: "Program Team",
      email_reply_to: "program@example.test",
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
          email: "admin@example.com",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            roles: ["organization_admin"],
          }],
          event_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            event_id: eventId,
            roles: ["event_admin"],
          }],
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
