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
    await expect(page.getByRole("link", { name: "Request a new sign-in link" })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("resource_not_found");
  });
});

test.describe("administration empty states", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("an administrator can start with an organization and no events", async ({ page }) => {
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
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) });
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
    await expect(page.getByRole("textbox", { name: "Event time zone" })).not.toHaveValue("");
    await expect(page.getByRole("button", { name: "Create event" })).toBeEnabled();
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

    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.getByRole("status").first()).toHaveText("Proposal submitted.");
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
