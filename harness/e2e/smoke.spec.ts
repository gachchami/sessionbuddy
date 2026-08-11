import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const landingHtml = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/landing.html"),
  "utf8",
);
const landingCss = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/landing.css"),
  "utf8",
);
const appShellCss = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/app_shell.css"),
  "utf8",
);
const appShellJavaScript = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/app_shell.js"),
  "utf8",
);
const apiClientJavaScript = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/api_client.js"),
  "utf8",
);
const setupHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/setup.html"), "utf8");
const setupCss = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/setup.css"), "utf8");
const setupJavaScript = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/setup.js"), "utf8");
const homepageUrl = /^https?:\/\/[^/]+\/(?:\?.*)?$/;

async function serveConfiguredHomepage(page: import("@playwright/test").Page) {
  await page.route(homepageUrl, async (route) => {
    await route.fulfill({ contentType: "text/html", body: landingHtml });
  });
  await page.route("**/landing/assets/landing.css*", async (route) => {
    await route.fulfill({ contentType: "text/css", body: landingCss });
  });
  await page.route("**/app-shell/assets/app-shell.css*", async (route) => {
    await route.fulfill({ contentType: "text/css", body: appShellCss });
  });
  await page.route("**/app-shell/assets/api-client.js*", async (route) => {
    await route.fulfill({ contentType: "text/javascript", body: apiClientJavaScript });
  });
  await page.route("**/app-shell/assets/app-shell.js*", async (route) => {
    await route.fulfill({ contentType: "text/javascript", body: appShellJavaScript });
  });
  await page.route("**/api/v1/setup/status", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: true }) });
  });
}

test.describe("public smoke checks", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("public homepage presents the product and role entry points", async ({ page }) => {
    await serveConfiguredHomepage(page);
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
    await serveConfiguredHomepage(page);
    await page.setViewportSize({ width: 390, height: 844 });
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in", exact: true }).first()).toBeVisible();
    const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(documentWidth).toBeLessThanOrEqual(390);
  });

  test("an authenticated administrator never sees a sign-in call to action", async ({ page }) => {
    await serveConfiguredHomepage(page);
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: null,
          csrf_token: "browser-test-csrf",
          profile_complete: true,
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
    await expect(page.getByRole("link", { name: "Open dashboard", exact: true })).toHaveCount(3);
    await expect(page.locator("[data-auth-entry]")).toHaveCount(2);
    await expect(page.locator("[data-auth-entry]")).toHaveText(["Open dashboard", "Open dashboard"]);
    await expect(page.locator("[data-auth-entry]").first()).toHaveAttribute("href", "/admin");
    await expect(page.locator("[data-auth-entry]").last()).toHaveAttribute("href", "/admin");
  });

  test("an expired magic link offers browser recovery instead of JSON", async ({ page }) => {
    const expiredToken = "expired-link".padEnd(32, "x");
    const response = await page.goto(`/auth/verify?token=${expiredToken}`);
    expect(response?.ok()).toBeTruthy();
    await expect(page).toHaveTitle(/Confirm sign in/);
    await page.getByRole("button", { name: "Continue" }).click();
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

  test("the CFP defers email verification instead of demanding sign-in up front", async ({ page }) => {
    const slug = "speaker-login";
    let requestCount = 0;
    await page.addInitScript(() => {
      if (!crypto.randomUUID) {
        Object.defineProperty(crypto, "randomUUID", {
          configurable: true,
          value: () => "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        });
      }
    });
    await page.route(`**/api/v1/forms/${slug}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ slug, event_name: "Conference", welcome_text: "Submit a proposal.", accepting_submissions: true, fields: [], conditions: [] }),
    }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "Sign in required" } }),
    }));
    await page.route("**/api/v1/auth/magic-links", (route) => {
      requestCount += 1;
      return route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
    });

    await page.goto(`/cfp/${slug}`);
    // Anonymous visitors get the whole form immediately; identity is only
    // established at submission time.
    await expect(page.getByRole("status").first()).toHaveText(
      "Complete the proposal. We will verify your email only when you submit.",
    );
    await expect(page.getByRole("button", { name: "Review proposal" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in" })).toHaveCount(0);
    expect(requestCount).toBe(0);
  });

  test("an empty instance starts with secured administrator onboarding", async ({ page }) => {
    let bootstrapBody: Record<string, unknown> | null = null;
    let bootstrapKey = "";
    await page.route(homepageUrl, async (route) => {
      await route.fulfill({
        contentType: "text/html",
        body: "<!doctype html><title>SessionBuddy</title><h1>SessionBuddy home</h1>",
      });
    });
    await page.route(/^https?:\/\/[^/]+\/setup(?:\?.*)?$/, (route) => route.fulfill({ contentType: "text/html", body: setupHtml }));
    await page.route("**/setup/assets/setup.css*", (route) => route.fulfill({ contentType: "text/css", body: setupCss }));
    await page.route("**/setup/assets/setup.js*", (route) => route.fulfill({ contentType: "text/javascript", body: setupJavaScript }));
    await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({ contentType: "text/javascript", body: apiClientJavaScript }));
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
    await page.getByLabel("Time zone").fill("Mars/Phobos");
    await page.getByRole("button", { name: "Complete setup" }).click();
    expect(bootstrapBody).toBeNull();
    await page.getByLabel("Time zone").fill("Asia/Kolkata");
    await page.getByRole("button", { name: "Complete setup" }).click();

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "SessionBuddy home" })).toBeVisible();
    expect(bootstrapKey).toBe("x".repeat(40));
    expect(bootstrapBody).toMatchObject({
      organization_name: "Noneli Events",
      admin_name: "Devang Hanushali",
      admin_email: "me@example.com",
      admin_time_zone: "Asia/Kolkata",
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
          profile_complete: true,
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
    await page.route("**/api/v1/admin/organizations/*/events**", async (route) => {
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
    const eventName = page.getByRole("textbox", { name: "Event name" });
    await expect(eventName).toHaveValue("");
    await expect(eventName).not.toHaveAttribute("placeholder", /.+/);
    for (const fieldName of [
      "name",
      "time_zone",
      "delivery_mode",
      "start_date",
      "start_time",
      "end_date",
      "end_time",
    ]) {
      const field = eventDialog.locator(`[name="${fieldName}"]`);
      // The marker lives inside the .field-label wrapper, not as a direct child.
      const marker = eventDialog.locator(`label:has([name="${fieldName}"]) .required-marker`);
      await expect(field).toHaveAttribute("required", "");
      await expect(marker).toHaveText("*");
      await expect(marker).toHaveCSS("color", "rgb(180, 35, 24)");
    }
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
    await page.getByRole("textbox", { name: "Location" }).fill("Rehearsal Hall, Pune");
    await page.getByRole("textbox", { name: /^Description/ }).fill("Timezone rehearsal event.");
    await page.getByRole("textbox", { name: "Sender name" }).fill("Program Team");
    await page.getByRole("textbox", { name: "Reply-to email" }).fill("program@example.test");
    await page.getByRole("combobox", { name: "Attendance format" }).selectOption("in_person");
    await page.getByText("Branding", { exact: true }).click();
    await page.getByRole("textbox", { name: "Event website" }).fill("http://example.test");
    await eventDialog.getByRole("button", { name: "Create event" }).click();
    expect(createdEvent).toBeNull();
    await page.getByRole("textbox", { name: "Event website" }).fill("https://example.test");
    await eventDialog.getByRole("button", { name: "Create event" }).click();
    // The dialog's live date preview is also role=status; target the page
    // status message rather than whichever status happens to come first.
    await expect(page.getByRole("status").filter({ hasText: "Event created." })).toBeVisible();
    expect(createdEvent).toMatchObject({
      name: "Timezone Rehearsal",
      starts_at_ms: Date.UTC(2026, 8, 12, 3, 30),
      ends_at_ms: Date.UTC(2026, 8, 12, 11, 30),
      time_zone: "Asia/Kolkata",
      email_sender_name: "Program Team",
      email_reply_to: "program@example.test",
      website_url: "https://example.test",
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
          profile_complete: true,
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
    await page.getByRole("button", { name: "Invite someone" }).click();
    await page.getByRole("textbox", { name: "Email address" }).fill("speaker@example.com");
    await page.getByRole("textbox", { name: "Name", exact: true }).fill("Example Speaker");
    await page.getByRole("button", { name: "Send invitation" }).click();

    // The success feedback must survive the list refresh (load() writes its
    // own generic status; the outcome message is set after it).
    await expect(page.getByRole("status")).toHaveText("Invitation created and emailed to the invitee.");
    // A successful send closes the dialog and the invitation appears in the
    // list; reopening presents a fresh, reset form.
    await expect(page.locator("#invite-dialog")).not.toHaveAttribute("open", "");
    await expect(page.getByText("speaker@example.com · speaker · pending")).toBeVisible();
    await page.getByRole("button", { name: "Invite someone" }).click();
    await expect(page.getByRole("textbox", { name: "Email address" })).toHaveValue("");
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue("");
  });

  test("the authenticated event workspace stays navigable on mobile", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const programId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        event_id: eventId,
        csrf_token: "browser-test-csrf",
          profile_complete: true,
        email: "admin@example.com",
        display_name: "Admin User",
        organization_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", roles: ["organization_admin"] }],
        event_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId, roles: ["event_admin"] }],
      }),
    }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Open Source Summit", status: "active", version: 1 }] }) }));
    await page.route("**/api/v1/admin/organizations/*/events**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: eventId, organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Open Source Summit 2026", status: "active", starts_at_ms: Date.UTC(2026, 10, 12, 3, 30), ends_at_ms: Date.UTC(2026, 10, 14, 11, 30), time_zone: "Asia/Kolkata", delivery_mode: "hybrid", location: "Bengaluru" }] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-targets`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId, event_name: "Open Source Summit 2026", event_starts_at_ms: Date.UTC(2026, 10, 12, 3, 30), published_form: { slug: "open-source-summit" } }) }));

    await page.goto(`/admin/events/${eventId}`);
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("navigation", { name: "Current event" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Submissions & reviews" })).toHaveAttribute("href", `/admin/events/${eventId}/submissions`);
    await expect(page.getByRole("link", { name: "Messages" })).toHaveAttribute("href", `/admin/events/${eventId}/messages`);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });

  test("submissions keep the event navigation", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId,
        csrf_token: "browser-test-csrf",
          profile_complete: true, email: "admin@example.com", display_name: "Admin User",
        organization_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", roles: ["organization_admin"] }], event_access: [],
      }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: eventId,
        organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        name: "World Fair 2026",
        status: "active",
        version: 1,
        time_zone: "Asia/Kolkata",
        delivery_mode: "hybrid",
        starts_at_ms: Date.UTC(2026, 10, 12, 3, 30),
        ends_at_ms: Date.UTC(2026, 10, 14, 11, 30),
      }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/submissions`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId, data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/evaluators`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/evaluation-rounds**`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));

    await page.goto(`/admin/events/${eventId}/submissions`);
    await expect(page.getByRole("navigation", { name: "Current event" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Submissions & reviews" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("link", { name: "Messages" })).toHaveAttribute("href", `/admin/events/${eventId}/messages`);
  });

  test("a published call for speakers remains connected after reload", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: eventId,
          csrf_token: "browser-test-csrf",
          profile_complete: true,
          email: "admin@example.com",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            roles: ["organization_admin"],
          }],
          event_access: [],
        }),
      });
    });
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: eventId,
        organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        name: "World Fair 2026",
        status: "active",
        version: 1,
        time_zone: "Asia/Kolkata",
        delivery_mode: "hybrid",
        starts_at_ms: Date.UTC(2026, 10, 12, 3, 30),
        ends_at_ms: Date.UTC(2026, 10, 14, 11, 30),
      }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/cfp`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: eventId,
          event_name: "World Fair 2026",
          event_starts_at_ms: Date.UTC(2026, 10, 12, 3, 30),
          published_form: { slug: "world-fair-2026" },
        }),
      });
    });

    await page.goto(`/admin/events/${eventId}/cfp`);
    await expect(page.getByRole("status").first()).toHaveText(
      "Your CFP is published and ready to share.",
    );
    await expect(page.getByLabel("Public CFP URL")).toHaveValue(
      `${new URL(page.url()).origin}/cfp/world-fair-2026`,
    );
    await expect(page.getByRole("link", { name: "Review submissions" })).toHaveAttribute(
      "href",
      `/admin/events/${eventId}/submissions`,
    );

    await page.reload();
    await expect(page.getByLabel("Public CFP URL")).toHaveValue(
      `${new URL(page.url()).origin}/cfp/world-fair-2026`,
    );
  });
});

test.describe("dynamic form drafts", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("anonymous visitors complete the CFP before email verification and resume it", async ({ page }) => {
    const slug = "deferred-verification";
    let signedIn = false;
    let magicLinkEmail = "";
    let submittedBody: unknown = null;
    await page.route(`**/api/v1/forms/${slug}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: "11111111-1111-4111-8111-111111111111",
        version: 1,
        slug,
        event_id: "22222222-2222-4222-8222-222222222222",
        event_name: "Deferred Auth Conference",
        welcome_text: "Complete the proposal before verifying your email.",
        accepting_submissions: true,
        fields: [
          { key: "speaker_name", label: "Speaker name", type: "text", required: true, choices: [] },
          { key: "speaker_email", label: "Email", type: "email", required: true, choices: [] },
          { key: "proposal_title", label: "Proposal title", type: "text", required: true, choices: [] },
          { key: "proposal_abstract", label: "Proposal abstract", type: "textarea", required: true, choices: [] },
          { key: "format", label: "Format", type: "select", required: false, choices: ["Talk", "Workshop"] },
          { key: "equipment", label: "Workshop equipment", type: "textarea", required: true, choices: [] },
        ],
        conditions: [{ source_key: "format", target_key: "equipment", operator: "equals", value: "Workshop" }],
      }),
    }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill(signedIn ? {
      contentType: "application/json",
      body: JSON.stringify({ csrf_token: "browser-test-csrf",
          profile_complete: true, email: "speaker@example.com" }),
    } : {
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "authentication_required", message: "Authentication required" } }),
    }));
    await page.route("**/api/v1/auth/magic-links", async (route) => {
      magicLinkEmail = route.request().postDataJSON().email;
      await route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
    });
    await page.route(`**/api/v1/forms/${slug}/access`, (route) => route.fulfill({
      contentType: "application/json", body: "{}",
    }));
    await page.route(`**/api/v1/forms/${slug}/submissions/mine`, (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify({ data: [] }),
    }));
    await page.route(`**/api/v1/forms/${slug}/submissions`, async (route) => {
      submittedBody = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ id: "33333333-3333-4333-8333-333333333333", status: "submitted" }),
      });
    });

    await page.goto(`/cfp/${slug}`);
    await expect(page.getByRole("heading", { name: "Verify your email to submit" })).toBeHidden();
    await expect(page.getByText("Workshop equipment (required)")).toHaveCount(0);
    await expect(page.getByText("Additional questions may appear based on your answers.")).toBeVisible();
    await page.getByRole("textbox", { name: /Speaker name/ }).fill("Example Speaker");
    await page.getByRole("textbox", { name: /Email/ }).first().fill("speaker@example.com");
    await page.getByRole("textbox", { name: /Proposal title/ }).fill("Deferred authentication");
    await page.getByRole("textbox", { name: /Proposal abstract/ }).fill("The entire form is complete before sign-in.");
    await page.getByRole("button", { name: "Review proposal" }).click();
    await page.getByRole("button", { name: "Confirm submission" }).click();

    await expect.poll(() => magicLinkEmail).toBe("speaker@example.com");
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
    expect(submittedBody).toBeNull();

    signedIn = true;
    await page.reload();
    // Email verification proves identity, but the speaker still explicitly
    // confirms the final submission from the restored review step.
    await expect(page.getByRole("heading", { name: "Review your proposal" })).toBeVisible();
    await expect(page.getByRole("status").first()).toContainText("Email verified");
    expect(submittedBody).toBeNull();
    await page.getByRole("button", { name: "Confirm submission" }).click();
    await expect.poll(() => submittedBody).not.toBeNull();
    expect(submittedBody).toMatchObject({
      speaker_name: "Example Speaker",
      speaker_email: "speaker@example.com",
      proposal_title: "Deferred authentication",
      proposal_abstract: "The entire form is complete before sign-in.",
    });
  });

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
    await page.route(`**/api/v1/forms/${slug}/access`, async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
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
      co_speakers: [],
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

  test("required proposal fields block review and submission", async ({ page }) => {
    const slug = "required-fields";
    let submissionRequests = 0;
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
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "Not found" } }),
      });
    });
    await page.route(`**/api/v1/forms/${slug}/submissions`, async (route) => {
      submissionRequests += 1;
      await route.fulfill({ status: 201, contentType: "application/json", body: "{}" });
    });
    await page.route(`**/api/v1/forms/${slug}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          slug,
          event_name: "Required Fields Conference",
          welcome_text: "Complete every required field.",
          accepting_submissions: true,
          fields: [
            { key: "speaker_name", label: "Speaker name", type: "text", required: true, choices: [] },
            { key: "speaker_email", label: "Email", type: "email", required: true, choices: [] },
            { key: "proposal_title", label: "Proposal title", type: "text", required: true, choices: [] },
            { key: "proposal_abstract", label: "Proposal abstract", type: "textarea", required: true, choices: [] },
          ],
          conditions: [],
        }),
      });
    });
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ csrf_token: "browser-test-csrf" }),
      });
    });
    await page.route(`**/api/v1/forms/${slug}/access`, async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto(`/cfp/${slug}`);
    await page.getByRole("button", { name: "Review proposal" }).click();

    await expect(page.getByRole("heading", { name: "Review your proposal" })).toBeHidden();
    await expect(page.getByRole("textbox", { name: /Speaker name/ })).toBeFocused();
    expect(submissionRequests).toBe(0);
  });
});
