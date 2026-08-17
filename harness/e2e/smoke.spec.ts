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
const eventWorkspaceHtml = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/event_workspace.html"),
  "utf8",
);
const eventWorkspaceJavaScript = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/event_workspace.js"),
  "utf8",
);
const agendaAdminHtml = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/agenda_admin.html"),
  "utf8",
);
const agendaJavaScript = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/agenda.js"),
  "utf8",
);
const adminSubmissionsHtml = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/admin_submissions.html"),
  "utf8",
);
const publicCfpHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/public_cfp.html"), "utf8");
const publicCfpJavaScript = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/public_cfp.js"), "utf8");
const publicEventMastheadJavaScript = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/public_event_masthead.js"), "utf8");
const setupHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/setup.html"), "utf8");
const authLinkConfirmHtml = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/auth_link_confirm.html"),
  "utf8",
);
const authLinkConfirmJavaScript = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/auth_link_confirm.js"),
  "utf8",
);

async function servePublicCfpPage(page: import("@playwright/test").Page, slug: string) {
  const selfContainedHtml = publicCfpHtml
    .replace(/<script src="\/app-shell\/assets\/api-client\.js\?v=\d+" defer><\/script>/, "")
    .replace(/<script src="\/public\/assets\/event-masthead\.js\?v=[^"]+" defer><\/script>/, "")
    .replace(/<script src="\/product\/assets\/public-cfp\.js\?v=\d+" defer><\/script>/, "")
    .replace("</body>", `<script>${apiClientJavaScript}</script><script>${publicEventMastheadJavaScript}</script><script>${publicCfpJavaScript}</script></body>`);
  await page.route(`**/cfp/mobile/${slug}`, (route) => route.fulfill({
    contentType: "text/html",
    body: selfContainedHtml,
  }));
}

function publicForm(overrides: Record<string, unknown>) {
  return {
    id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
    event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    event_name: "Conference",
    event_starts_at_ms: Date.UTC(2027, 5, 1),
    event_ends_at_ms: Date.UTC(2027, 5, 2),
    event_time_zone: "UTC",
    event_location: "Online",
    event_delivery_mode: "virtual",
    important_dates: [],
    fields: [],
    conditions: [],
    routing_rules: [],
    co_speaker_limit: 1,
    accepting_submissions: true,
    success_title: "Proposal received",
    success_message: "We received your proposal.",
    redirect_to_portal: true,
    ...overrides,
  };
}
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
      name: "From open call to published agenda.",
    })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in", exact: true }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Explore event" }).first()).toBeVisible();
  });

  test("public homepage discovers an open call from the public events API", async ({ page }) => {
    await serveConfiguredHomepage(page);
    await page.route("**/api/v1/public/events", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: [{
          id: "22222222-2222-4222-8222-222222222222",
          name: "Agent Platforms Summit",
          starts_at_ms: Date.UTC(2027, 5, 1),
          ends_at_ms: Date.UTC(2027, 5, 2),
          time_zone: "UTC",
          location: "Online",
          delivery_mode: "virtual",
          cfp_slug: "agent-platforms",
          schedule_published: false,
          speaker_count: 0,
        }] }),
      });
    });

    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Explore events" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Agent Platforms Summit" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Call for Proposals →" })).toHaveAttribute(
      "href",
      "/cfp/222222/agent-platforms",
    );
    await expect(page.locator("[data-public-events]")).toHaveAttribute("aria-busy", "false");
  });

  test("public homepage recovers from a failed event request", async ({ page }) => {
    await serveConfiguredHomepage(page);
    let attempts = 0;
    await page.route("**/api/v1/public/events", async (route) => {
      attempts += 1;
      if (attempts <= 2) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "unavailable", message: "Database unavailable" } }),
        });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: [{
          name: "A very long international conference title مؤتمر تقني عالمي 🌍",
          starts_at_ms: null,
          location: "",
          delivery_mode: null,
          cfp_slug: "global-call",
          cfp_boundary_at_ms: "invalid",
          cfp_state: "open",
          schedule_published: false,
          speaker_count: 0,
        }] }),
      });
    });

    await page.goto("/");
    const error = page.getByRole("alert");
    await expect(error).toContainText("Public events could not be loaded.");
    await expect(error).not.toContainText("Database unavailable");
    await error.getByRole("button", { name: "Try again" }).click();
    await expect(error).toContainText("Public events could not be loaded.");
    await error.getByRole("button", { name: "Try again" }).click();

    await expect(page.getByRole("heading", { name: /A very long international/ })).toBeVisible();
    await expect(page.getByText("Date to be announced")).toBeVisible();
    await expect(page.getByText("Event details coming soon.")).toBeVisible();
    await expect(page.getByRole("link", { name: "Call for Proposals →" })).toHaveAttribute(
      "href",
      "/cfp/global-call",
    );
    await expect(page.locator("[data-public-events]")).toHaveAttribute("aria-busy", "false");
  });

  test("sharing tools send organizers to the agenda publication controls", async ({ page }) => {
    const eventId = "22222222-2222-4222-8222-222222222222";
    await page.route(`**/admin/events/${eventId}/workspace`, (route) => route.fulfill({
      contentType: "text/html",
      body: eventWorkspaceHtml,
    }));
    await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: apiClientJavaScript,
    }));
    await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: appShellJavaScript,
    }));
    await page.route("**/admin/workspace/assets/workspace.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: eventWorkspaceJavaScript,
    }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        email: "organizer@example.com",
        display_name: "Example Organizer",
        profile_complete: true,
        csrf_token: "browser-test-csrf",
        account_roles: ["organizer"],
        active_role: "organizer",
        default_role: "organizer",
        organization_access: [{
          organization_id: "11111111-1111-4111-8111-111111111111",
          organization_name: "Agent Platforms",
          permissions: ["owner"],
        }],
        event_access: [{
          organization_id: "11111111-1111-4111-8111-111111111111",
          event_id: eventId,
          event_name: "Agent Platforms Summit",
          assignments: [],
        }],
      }),
    }));

    await page.goto(`/admin/events/${eventId}/workspace`);

    await expect(page.getByRole("heading", { name: "Share & integrations" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Review & publish agenda" })).toHaveAttribute(
      "href",
      `/admin/events/${eventId}/agenda`,
    );
    await expect(page.getByRole("link", { name: "Agenda", exact: true })).toHaveAttribute(
      "href",
      `/admin/events/${eventId}/agenda`,
    );
    await expect(page.getByText("Embed and feed builder", { exact: true })).toBeVisible();
    await expect(page.getByText("Presets are stored in this browser.", { exact: false })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Embed enabled" })).toHaveCount(0);
    await expect(page.locator("#embed-code")).toHaveValue(/<iframe/);
  });

  test("agenda resource archive explains consequences before mutation", async ({ page }) => {
    const eventId = "22222222-2222-4222-8222-222222222222";
    let patchRequests = 0;
    await page.route(`**/admin/events/${eventId}/agenda`, (route) => route.fulfill({
      contentType: "text/html",
      body: agendaAdminHtml,
    }));
    await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: apiClientJavaScript,
    }));
    await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: appShellJavaScript,
    }));
    await page.route("**/admin/agenda/assets/agenda.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: agendaJavaScript,
    }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        email: "organizer@example.com",
        display_name: "Example Organizer",
        profile_complete: true,
        csrf_token: "browser-test-csrf",
        account_roles: ["organizer"],
        active_role: "organizer",
        default_role: "organizer",
        organization_access: [{
          organization_id: "11111111-1111-4111-8111-111111111111",
          organization_name: "Agent Platforms",
          permissions: ["owner"],
        }],
        event_access: [{
          organization_id: "11111111-1111-4111-8111-111111111111",
          event_id: eventId,
          event_name: "Agent Platforms Summit",
          assignments: [],
        }],
      }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/agenda`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          event: { id: eventId, name: "Agent Platforms Summit", time_zone: "UTC", starts_at_ms: Date.UTC(2027, 5, 1) },
          revision: { id: "revision-1", version: 1, state: "draft" },
          published_revision: null,
          items: [],
          unscheduled_sessions: [],
          rooms: [{ id: "room-1", name: "Overflow", status: "active", version: 1 }],
          tracks: [],
          labels: [],
          archived_rooms: [],
          archived_tracks: [],
          archived_labels: [],
          can_manage_resource_lifecycle: true,
        }),
      });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/**`, async (route) => {
      if (route.request().method() === "PATCH") patchRequests += 1;
      await route.abort();
    });

    await page.goto(`/admin/events/${eventId}/agenda`);
    await page.getByText("Schedule tools", { exact: true }).click();
    await page.getByRole("button", { name: "Archive Overflow" }).click();

    await expect(page.getByRole("dialog", { name: "Archive Overflow?" })).toBeVisible();
    await expect(page.getByText("Remove or move every scheduled session")).toBeVisible();
    expect(patchRequests).toBe(0);
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
        body: JSON.stringify(publicForm({
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          authenticated: true,
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: null,
          csrf_token: "browser-test-csrf",
          profile_complete: true,
          email: "admin@example.com",
          display_name: "Admin User",
          account_roles: ["organizer"],
          active_role: "organizer",
          default_role: "organizer",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            organization_name: "Example Organization",
            permissions: ["owner"],
          }],
          event_access: [],
        })),
      });
    });
    await page.route("**/api/v1/admin/**", (route) => route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "not_found", message: "Not found" } }),
    }));

    await page.goto("/");
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.getByRole("link", { name: "Sign in", exact: true })).toHaveCount(0);
  });

  test("an expired magic link offers browser recovery instead of JSON", async ({ page }) => {
    const expiredToken = "expired-link".padEnd(32, "x");
    const response = await page.goto(`/auth/verify#token=${expiredToken}`);
    expect(response?.ok()).toBeTruthy();
    await page.getByRole("button", { name: "Continue to your account" }).click();
    await expect(page).toHaveTitle(/Sign-in link unavailable/);
    await expect(page.getByRole("heading", {
      level: 1,
      name: "This link has expired",
    })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in", exact: true })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("resource_not_found");
  });

  test("an existing-user magic link removes its fragment and waits for confirmation", async ({ page }) => {
    const token = "browser-auto-sign-in-token".padEnd(40, "x");
    const action = "/auth/verify";
    const confirmation = authLinkConfirmHtml
      .replace("__CONFIRM_PAGE_TITLE__", "Confirm sign in")
      .replace("__CONFIRM_HEADING__", "Continue to your account")
      .replace("__CONFIRM_INTRO__", "Confirm that you want to sign in to SessionBuddy.")
      .replace("__CONFIRM_ERROR__", "")
      .replace(
        "__REGISTRATION_FIELDS__",
        '<input type="hidden" name="token" autocomplete="off" value="">',
      )
      .replace("__CONFIRM_BUTTON__", "Continue to your account")
      .replace("__AUTO_SUBMIT_ATTRIBUTE__", "")
      .replace("__CONFIRM_ACTION__", action)
      .replace(
        "__CONFIRM_SCRIPT__",
        '<script src="/auth/assets/auth-link-confirm.js?v=3" defer></script>',
      );
    let posts = 0;
    await page.route(`**${action}`, async (route) => {
      if (route.request().method() === "POST") {
        posts += 1;
        expect(route.request().postData()).toContain(`token=${encodeURIComponent(token)}`);
        await route.fulfill({ status: 303, headers: { location: "/automatic-sign-in-complete" } });
        return;
      }
      await route.fulfill({ contentType: "text/html", body: confirmation });
    });
    await page.route("**/automatic-sign-in-complete", (route) => route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Signed in</title><h1>Administrator workspace</h1>",
    }));
    await page.route("**/auth/assets/auth-link-confirm.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: authLinkConfirmJavaScript,
    }));

    await page.goto(`${action}#token=${token}`);

    await expect(page).toHaveURL(/\/auth\/verify$/);
    expect(page.url()).not.toContain(token);
    expect(posts).toBe(0);
    await page.getByRole("button", { name: "Continue to your account" }).click();
    await expect(page).toHaveURL(/\/automatic-sign-in-complete$/);
    expect(posts).toBe(1);
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
    const send = page.getByRole("button", { name: "Send a sign-in link" });
    await email.fill("speaker@example.com");
    await send.click();

    await expect(email).toBeHidden();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
    expect(requestCount).toBe(1);

    await page.getByRole("button", { name: "Use a different email" }).click();
    await expect(email).toBeEnabled();
    await expect(email).toBeFocused();
    await expect(page.getByRole("button", { name: "Send a sign-in link" })).toBeEnabled();
  });

  test("the CFP defers speaker registration until proposal submission", async ({ page }) => {
    const slug = "speaker-login";
    await servePublicCfpPage(page, slug);
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
      body: JSON.stringify(publicForm({ slug, event_name: "Conference", welcome_text: "Submit a proposal." })),
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

    await page.goto(`/cfp/mobile/${slug}`);
    await expect(page.getByRole("status").first()).toHaveText(
      "Read every question and start your proposal below. You verify your email when you submit it.",
    );
    await expect(page.getByRole("heading", { name: "Proposal details" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Review proposal" })).toBeVisible();
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
    await page.getByLabel("Administrator first name").fill("Devang");
    await page.getByLabel("Administrator last name").fill("Hanushali");
    await page.getByLabel("Administrator email").fill("me@example.com");
    await page.getByLabel("Deployment setup key").fill("x".repeat(40));
    await page.getByLabel("Time zone").fill("Mars/Phobos");
    await page.getByRole("button", { name: "Complete setup" }).click();
    expect(bootstrapBody).toBeNull();
    await page.getByLabel("Time zone").fill("Asia/Kolkata");
    await page.getByLabel("Administrator first name").fill("   ");
    await page.getByRole("button", { name: "Complete setup" }).click();
    expect(bootstrapBody).toBeNull();
    await page.getByLabel("Administrator first name").fill("  Devang  ");
    await page.getByRole("button", { name: "Complete setup" }).click();

    await expect(page).toHaveURL(/\/setup$/);
    await expect(page.getByRole("heading", { name: "Check your email." })).toBeVisible();
    await expect(page.getByText("me@example.com", { exact: true })).toBeVisible();
    await expect(page.getByText("Open the newest link within 15 minutes")).toBeVisible();
    await expect(page.getByRole("link", { name: "Request a new sign-in link" })).toBeHidden();
    expect(bootstrapKey).toBe("x".repeat(40));
    expect(bootstrapBody).toMatchObject({
      organization_name: "Noneli Events",
      admin_name: "Devang Hanushali",
      admin_first_name: "Devang",
      admin_last_name: "Hanushali",
      admin_email: "me@example.com",
      admin_time_zone: "Asia/Kolkata",
    });
  });
});

test.describe("administration empty states", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("an administrator can start with an organization and no events", async ({ page }) => {
    let createdEvent: Record<string, unknown> | null = null;
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(publicForm({
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: null,
          csrf_token: "browser-test-csrf",
          profile_complete: true,
          email: "admin@example.com",
          account_roles: ["organizer"],
          active_role: "organizer",
          default_role: "organizer",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            organization_name: "Empty Event Organization",
            permissions: ["owner"],
          }],
          event_access: [],
        })),
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
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({
            id: eventId,
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            ...createdEvent,
            status: "active",
            version: 1,
            proposal_count: 0,
            pending_review_count: 0,
            schedule_status: "not_started",
            cfp_status: "not_started",
          }),
        });
        return;
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], next_cursor: null }) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}`, async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: eventId,
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          ...createdEvent,
          status: "active",
          version: 1,
          proposal_count: 0,
          pending_review_count: 0,
          schedule_status: "not_started",
          cfp_status: "not_started",
        }),
      });
    });
    await page.route("**/api/v1/admin/organizations/*/activities", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    }));

    const response = await page.goto("/admin");
    expect(response?.ok()).toBeTruthy();
    await expect(page.getByText(/No events yet\./)).toBeVisible();
    await page.getByRole("link", { name: "Create event" }).click();
    await expect(page).toHaveURL(/\/admin\/events\/new\?organization_id=/);
    await expect(page.getByRole("heading", { name: "Create event" })).toBeVisible();
    const eventEditor = page.locator("#event-editor-form");
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
      "location",
      "description",
    ]) {
      const field = eventEditor.locator(`[name="${fieldName}"]`);
      await expect(field).toHaveAttribute("required", "");
    }
    await expect(page.getByRole("combobox", { name: "Attendance format" })).toHaveValue("");
    const timeZone = page.getByLabel("Time zone");
    await expect(timeZone).not.toHaveValue("");
    await expect(eventEditor.locator('[name="start_date"]')).toHaveValue("");
    await expect(eventEditor.locator('[name="start_time"]')).toHaveValue("09:00");
    await expect(eventEditor.locator('[name="end_date"]')).toHaveValue("");
    await expect(eventEditor.locator('[name="end_time"]')).toHaveValue("17:00");
    await eventEditor.locator('[name="start_date"]').fill("2026-09-12");
    await eventEditor.locator('[name="end_date"]').fill("2026-09-12");
    await expect(page.locator("#date-time-preview")).toContainText(await timeZone.inputValue());
    await expect(eventEditor.getByRole("button", { name: "Create active event" })).toBeEnabled();
  await timeZone.selectOption("Asia/Kolkata");
    await page.getByRole("textbox", { name: "Event name" }).fill("Timezone Rehearsal");
    await page.getByRole("textbox", { name: "Location" }).fill("Rehearsal Hall, Pune");
    await page.getByRole("textbox", { name: /^Description/ }).fill("Timezone rehearsal event.");
    await page.getByText("Email", { exact: true }).click();
    await page.getByRole("textbox", { name: "Sender name" }).fill("Program Team");
    await page.getByRole("textbox", { name: "Reply-to email" }).fill("program@example.test");
    await page.getByRole("combobox", { name: "Attendance format" }).selectOption("in_person");
    await page.getByText("Branding", { exact: true }).click();
    await page.getByRole("textbox", { name: "Event website" }).fill("http://example.test");
    await eventEditor.getByRole("button", { name: "Create active event" }).click();
    expect(createdEvent).toBeNull();
    await expect(page.getByRole("textbox", { name: "Event website" })).toHaveAttribute("aria-invalid", "true");
    await page.getByRole("textbox", { name: "Event website" }).fill("https://example.test");
    await eventEditor.getByRole("button", { name: "Create active event" }).click();
    expect(createdEvent).toMatchObject({
      name: "Timezone Rehearsal",
      starts_at_ms: Date.UTC(2026, 8, 12, 3, 30),
      ends_at_ms: Date.UTC(2026, 8, 12, 11, 30),
      time_zone: "Asia/Kolkata",
      email_sender_name: "Program Team",
      email_reply_to: "program@example.test",
      website_url: "https://example.test",
      status: "active",
    });
    await expect(page).toHaveURL(`/admin/events/${eventId}`);
    await expect(page.getByRole("link", { name: "Settings", exact: true })).toHaveAttribute(
      "href",
      `/admin/events/${eventId}/settings`,
    );
  });

  test("an invitation form survives its asynchronous request and resets", async ({ page }) => {
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
          account_roles: ["organizer"],
          active_role: "organizer",
          default_role: "organizer",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            organization_name: "Example Organization",
            permissions: ["owner"],
          }],
          event_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            event_id: eventId,
            event_name: "Example Event",
            assignments: [],
          }],
        }),
      });
    });
    const invitationBodies: Array<Record<string, unknown>> = [];
    const bulkImportBodies: Array<Record<string, unknown>> = [];
    await page.route(`**/api/v1/admin/events/${eventId}/invitations`, async (route) => {
      if (route.request().method() === "POST") {
        invitationBodies.push(route.request().postDataJSON());
        await route.fulfill({ status: 201, contentType: "application/json", body: "{}" });
        return;
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-invitations/import`, async (route) => {
      const body = route.request().postDataJSON() as { mode: string; rows: Array<Record<string, unknown>> };
      bulkImportBodies.push(body);
      const executed = body.mode === "execute";
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          mode: body.mode,
          data: body.rows.map((row) => ({
            row_number: row.row_number,
            email: row.email,
            display_name: row.display_name,
            outcome: executed ? "created" : "ready",
            reason: executed ? "Invitation created and email queued." : "",
            allowed_dispositions: [],
            invitation_id: executed ? `invitation-${row.row_number}` : null,
          })),
          created_count: executed ? body.rows.length : 0,
          skipped_count: 0,
          needs_resolution_count: 0,
          rejected_count: 0,
          failed_count: 0,
        }),
      });
    });
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Example Organization" }] }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Example Event" }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-targets`, async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) });
    });

    const response = await page.goto(`/admin/events/${eventId}/speakers`);
    expect(response?.ok()).toBeTruthy();
    await page.getByRole("button", { name: "Invite speaker" }).click();
    await page.getByRole("dialog", { name: "Invite speaker" }).getByRole("textbox", { name: "Email address" }).fill("speaker@example.com");
    await page.getByRole("textbox", { name: "Name", exact: true }).fill("Example Speaker");
    await page.getByRole("button", { name: "Send invitation" }).click();

    // The success feedback must survive the list refresh (load() writes its
    // own generic status; the outcome message is set after it).
    await expect(page.getByRole("status")).toHaveText("Speaker invitation created and emailed. The roster is up to date.");
    await expect(page.locator("#invite-speaker-dialog")).not.toHaveAttribute("open", "");
    await page.getByRole("button", { name: "Invite speaker" }).click();
    await expect(page.getByRole("dialog", { name: "Invite speaker" }).getByRole("textbox", { name: "Email address" })).toHaveValue("");
    await expect(page.getByRole("textbox", { name: "Name", exact: true })).toHaveValue("");
    await page.getByRole("button", { name: "Close speaker invitation" }).click();

    await page.getByRole("button", { name: "Import CSV" }).click();
    const importDialog = page.getByRole("dialog", { name: "Import speaker invitations" });
    await importDialog.locator('input[type="file"]').setInputFiles({
      name: "speakers.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "email,display_name,job_title,company\n" +
        "marcus@example.com,Marcus Okafor,Staff Engineer,Example Co\n" +
        "\n" +
        'lee@example.com,"Lee, Morgan",Moderator,Community Guild\n',
      ),
    });
    await importDialog.getByRole("button", { name: "Review import" }).click();
    await expect(importDialog.getByText("2 ready to import; 0 duplicate or existing; 0 invalid; 0 need a decision.")).toBeVisible();
    await importDialog.getByRole("button", { name: "Import reviewed rows" }).click();
    await expect(importDialog.getByText("2 imported; 0 skipped; 0 rejected; 0 failed; 0 still need a decision.")).toBeVisible();
    await expect(importDialog.getByRole("button", { name: "Import complete" })).toBeDisabled();
    expect(bulkImportBodies.map((body) => body.mode)).toEqual(["preview", "execute"]);
    expect(bulkImportBodies[1].rows).toEqual([
      {
        row_number: 2, email: "marcus@example.com", display_name: "Marcus Okafor",
        job_title: "Staff Engineer", company: "Example Co", biography: "", disposition: "import",
      },
      {
        row_number: 4, email: "lee@example.com", display_name: "Lee, Morgan",
        job_title: "Moderator", company: "Community Guild", biography: "", disposition: "import",
      },
    ]);
  });

  test("the authenticated event workspace stays navigable on mobile", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const programId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(publicForm({
        user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        event_id: eventId,
        csrf_token: "browser-test-csrf",
          profile_complete: true,
        email: "admin@example.com",
        display_name: "Admin User",
        account_roles: ["organizer"], active_role: "organizer", default_role: "organizer",
        organization_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", organization_name: "Open Source Summit", permissions: ["owner"] }],
        event_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId, event_name: "Open Source Summit 2026", assignments: [] }],
      })),
    }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Open Source Summit", status: "active", version: 1 }] }) }));
    await page.route("**/api/v1/admin/organizations/*/events**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: eventId, organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Open Source Summit 2026", status: "active", starts_at_ms: Date.UTC(2026, 10, 12, 3, 30), ends_at_ms: Date.UTC(2026, 10, 14, 11, 30), time_zone: "Asia/Kolkata", delivery_mode: "hybrid", location: "Bengaluru" }] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: eventId, organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", name: "Open Source Summit 2026", status: "active", starts_at_ms: Date.UTC(2026, 10, 12, 3, 30), ends_at_ms: Date.UTC(2026, 10, 14, 11, 30), time_zone: "Asia/Kolkata", delivery_mode: "hybrid", location: "Bengaluru", version: 1 }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-targets`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/submissions`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], total: 0 }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/evaluation-rounds/current`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "round-a" }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/agenda`, (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not started" }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId, event_name: "Open Source Summit 2026", event_starts_at_ms: Date.UTC(2026, 10, 12, 3, 30), published_form: { slug: "open-source-summit", cover_image_url: "/landing/assets/sessionbuddy-favicon.svg", logo_url: "/landing/assets/sessionbuddy-favicon.svg" } }) }));

    await page.goto(`/admin/events/${eventId}`);
    await expect(page.getByRole("button", { name: "Open navigation" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Event navigation" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Open Source Summit 2026" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Edit event" })).toHaveAttribute("href", `/admin/events/${eventId}/settings`);
    await expect(page.locator(".event-overview-public-preview")).toHaveCount(0);
    const eventStatus = page.locator(".event-signal-list");
    await expect(eventStatus).toBeVisible();
    await expect(eventStatus.getByText("0 submitted", { exact: true })).toBeVisible();
    await expect(eventStatus.getByText("0 confirmed", { exact: true })).toBeVisible();
    await expect(eventStatus.getByText("0 sessions", { exact: true })).toBeVisible();
    await expect(eventStatus.getByRole("link", { name: "CFP", exact: true })).toHaveAttribute("href", `/admin/events/${eventId}/cfp`);
    const eventNavigation = page.getByRole("navigation", { name: "Event navigation" });
    await expect(eventNavigation.getByRole("link", { name: "Proposals", exact: true })).toHaveAttribute("href", `/admin/events/${eventId}/submissions`);
    await expect(eventNavigation.getByRole("link", { name: "Speakers" })).toHaveAttribute("href", `/admin/events/${eventId}/speakers`);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });

  test("submissions keep the event navigation", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    await page.route(`**/admin/events/${eventId}/submissions`, (route) => route.fulfill({
      contentType: "text/html",
      body: adminSubmissionsHtml,
    }));
    await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: apiClientJavaScript,
    }));
    await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({
      contentType: "text/javascript",
      body: appShellJavaScript,
    }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId,
        csrf_token: "browser-test-csrf",
          profile_complete: true, email: "admin@example.com", display_name: "Admin User",
        account_roles: ["organizer"], active_role: "organizer", default_role: "organizer",
        organization_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", organization_name: "Open Source Summit", permissions: ["owner"] }],
        event_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId, event_name: "World Fair 2026", assignments: [] }],
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
    await expect(page.getByRole("navigation", { name: "Event navigation" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Event navigation" }).getByRole("link", { name: "Proposals", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("link", { name: "Speakers" })).toHaveAttribute("href", `/admin/events/${eventId}/speakers`);
  });

  test("a published call for speakers remains connected after reload", async ({ page }) => {
    const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    let publishedForm = {
      ...publicForm({ slug: "world-fair-2026", welcome_text: "Submit your proposal." }),
      confirmation_subject: "We received your World Fair proposal",
      confirmation_body: "Thank you. The program team will review your proposal.",
    };
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
          account_roles: ["organizer"],
          active_role: "organizer",
          default_role: "organizer",
          organization_access: [{
            organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            organization_name: "Example Organization",
            permissions: ["owner"],
          }],
          event_access: [{ organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", event_id: eventId, event_name: "World Fair 2026", assignments: [] }],
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
      if (route.request().method() === "PATCH") {
        const update = route.request().postDataJSON();
        expect(update.confirmation_subject).toBe("Your revised confirmation");
        expect(update.confirmation_body).toBe("Your revised confirmation message.");
        publishedForm = { ...publishedForm, ...update, version: publishedForm.version + 1 };
        await route.fulfill({ contentType: "application/json", body: JSON.stringify(publishedForm) });
        return;
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          event_id: eventId,
          event_name: "World Fair 2026",
          event_starts_at_ms: Date.UTC(2026, 10, 12, 3, 30),
          published_form: publishedForm,
        }),
      });
    });

    await page.goto(`/admin/events/${eventId}/cfp`);
    await expect(page.locator("#cfp-state")).toHaveText("Open");
    await expect(page.getByLabel("Public CFP URL")).toHaveValue(
      `${new URL(page.url()).origin}/cfp/cccccc/world-fair-2026`,
    );
    await expect(page.getByRole("navigation", { name: "Event navigation" }).getByRole("link", { name: "Proposals", exact: true })).toHaveAttribute(
      "href",
      `/admin/events/${eventId}/submissions`,
    );
    await page.getByRole("button", { name: "Confirmation", exact: true }).click();
    await expect(page.getByLabel("Email subject")).toHaveValue("We received your World Fair proposal");
    await expect(page.getByLabel("Email message")).toHaveValue("Thank you. The program team will review your proposal.");
    await page.getByLabel("Email subject").fill("Your revised confirmation");
    await page.getByLabel("Email message").fill("Your revised confirmation message.");
    await page.getByRole("button", { name: "Update live CFP" }).click();
    await expect(page.locator("#status")).toHaveText("Changes saved.");

    await page.reload();
    await expect(page.getByLabel("Public CFP URL")).toHaveValue(
      `${new URL(page.url()).origin}/cfp/cccccc/world-fair-2026`,
    );
    await page.getByRole("button", { name: "Confirmation", exact: true }).click();
    await expect(page.getByLabel("Email subject")).toHaveValue("Your revised confirmation");
    await expect(page.getByLabel("Email message")).toHaveValue("Your revised confirmation message.");
  });
});

test.describe("dynamic form drafts", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("anonymous visitors complete a proposal before email verification", async ({ page }) => {
    const slug = "deferred-verification";
    await servePublicCfpPage(page, slug);
    let signedIn = false;
    let magicLinkEmail = "";
    let submittedBody: unknown = null;
    await page.route(`**/api/v1/forms/${slug}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(publicForm({
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
      })),
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
    await page.route(`**/api/v1/forms/${slug}/draft`, (route) => route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "not_found", message: "No server draft" } }),
    }));
    await page.route(`**/api/v1/forms/${slug}/submissions`, async (route) => {
      submittedBody = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ id: "33333333-3333-4333-8333-333333333333", status: "submitted" }),
      });
    });

    await page.goto(`/cfp/mobile/${slug}`);
    await expect(page.getByRole("status").first()).toHaveText(
      "Read every question and start your proposal below. You verify your email when you submit it.",
    );
    await expect(page.getByRole("heading", { name: "Verify your email to submit" })).toBeHidden();
    await expect(page.getByText("Workshop equipment (required)")).toHaveCount(0);
    await expect(page.getByText("Additional questions may appear based on your answers.")).toBeVisible();
    await page.getByRole("textbox", { name: /Speaker name/ }).fill("Example Speaker");
    await page.getByRole("textbox", { name: "Email (required)", exact: true }).fill("speaker@example.com");
    await page.getByRole("textbox", { name: /Proposal title/ }).fill("Deferred authentication");
    await page.getByRole("textbox", { name: /Proposal abstract/ }).fill("The entire form is complete before sign-in.");
    await page.getByRole("button", { name: "Review proposal" }).click();
    await page.getByRole("button", { name: "Confirm submission" }).click();

    await expect(page.getByRole("status").first()).toHaveText(
      "Your completed proposal is saved in this browser. Verify the proposal email to submit it.",
    );
    await page.getByRole("button", { name: "Email me a signup link" }).click();
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

  test("anonymous browser drafts remain bound to email verification", async ({ page }) => {
    const slug = "conditional-rehearsal";
    await servePublicCfpPage(page, slug);
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
    await page.addInitScript(({ key, savedAt }) => {
      localStorage.setItem(key, JSON.stringify({
        schemaVersion: 1,
        formVersion: 1,
        answers: {
          speaker_name: "Example Speaker",
          speaker_email: "speaker@example.com",
          proposal_title: "Conditional forms",
          proposal_abstract: "How conditional form submissions work.",
          session_format: "Workshop",
          workshop_requirements: "Bring a laptop",
        },
        coSpeakers: [],
        submissionId: null,
        fileNames: [],
        readyToSubmit: false,
        ownerEmail: "speaker@example.com",
        savedAt,
      }));
    }, {
      key: `sessionbuddy:cfp:${slug}:draft:new`,
      savedAt: Date.now(),
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
        body: JSON.stringify(publicForm({
          version: 1,
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
        })),
      });
    });
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "Authentication required" } }),
      });
    });
    await page.route(`**/api/v1/forms/${slug}/access`, async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
    });

    const response = await page.goto(`/cfp/mobile/${slug}`);
    expect(response?.ok()).toBeTruthy();
    expect(pageErrors).toEqual([]);
    await expect(page.getByRole("status").first()).toHaveText(
      "A recent draft is waiting in this browser. Sign in with its proposal email to restore it.",
    );
    await expect(page.getByRole("textbox", { name: "Email address" })).toHaveValue(
      "speaker@example.com",
    );
    await expect(page.getByRole("textbox", { name: "Proposal title" })).toHaveValue("");
    expect(submittedBody).toBeNull();
  });

  test("required proposal fields validate before deferred registration", async ({ page }) => {
    const slug = "required-fields";
    await servePublicCfpPage(page, slug);
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
        body: JSON.stringify(publicForm({
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
        })),
      });
    });
    await page.route("**/api/v1/auth/session", async (route) => {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "Authentication required" } }),
      });
    });
    await page.route(`**/api/v1/forms/${slug}/access`, async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto(`/cfp/mobile/${slug}`);
    const title = page.getByRole("textbox", { name: /Proposal title/ });
    await expect(title).toBeVisible();
    await page.getByRole("button", { name: "Review proposal" }).click();
    await expect(title).toHaveAttribute("required", "");
    expect(submissionRequests).toBe(0);
  });
});
