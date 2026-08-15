import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const landingHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/landing.html"), "utf8");
const landingCss = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/landing.css"), "utf8");
const setupHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/setup.html"), "utf8");
const setupCss = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/setup.css"), "utf8");
const setupJs = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/setup.js"), "utf8");
const errorHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/error_page.html"), "utf8");
const errorCss = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/error_page.css"), "utf8");
const scheduleHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/schedule.html"), "utf8");
const speakerGalleryHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/speaker_gallery.html"), "utf8");
const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

test.describe("MVP experience accessibility", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("home page has no automatically detectable serious violations", async ({ page }) => {
    await page.route(/^https?:\/\/[^/]+\/(?:\?.*)?$/, (route) => route.fulfill({ contentType: "text/html", body: landingHtml }));
    await page.route("**/landing/assets/landing.css*", (route) => route.fulfill({ contentType: "text/css", body: landingCss }));
    await page.route("**/api/v1/setup/status", async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: true }) });
    });
    await page.goto("/");
    await expect(page.locator("body")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    const blocking = results.violations.filter(({ impact }) =>
      impact === "critical" || impact === "serious"
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
  });

  test("expired-link recovery has no automatically detectable serious violations", async ({ page }) => {
    const expiredToken = "expired-link".padEnd(32, "x");
    const responsePromise = page.waitForResponse((response) =>
      response.url().endsWith("/auth/verify") && response.request().method() === "POST",
    );
    await page.goto(`/auth/verify#token=${expiredToken}`);
    await page.getByRole("button", { name: "Continue to your account" }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(404);
    await expect(page.locator("main")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter(
      ({ impact }) => impact === "critical" || impact === "serious",
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
  });

  test("first-time setup has no automatically detectable serious violations", async ({ page }) => {
    await page.route(/^https?:\/\/[^/]+\/setup(?:\?.*)?$/, (route) => route.fulfill({ contentType: "text/html", body: setupHtml }));
    await page.route("**/setup/assets/setup.css*", (route) => route.fulfill({ contentType: "text/css", body: setupCss }));
    await page.route("**/setup/assets/setup.js*", (route) => route.fulfill({ contentType: "text/javascript", body: setupJs }));
    await page.route("**/api/v1/setup/status", async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ configured: false }) });
    });
    await page.goto("/setup");
    await expect(page.locator("main")).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter(
      ({ impact }) => impact === "critical" || impact === "serious",
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
  });

  test("access-denied page has no automatically detectable serious violations", async ({ page }) => {
    const body = errorHtml
      .replace("{{TITLE}}", "Access denied")
      .replace("{{EYEBROW}}", "Access denied")
      .replace("{{STATUS}}", "403")
      .replace("{{HEADING}}", "This page is not available for your active role.")
      .replace("{{MESSAGE}}", "Open your active workspace. If another role has access, switch roles from the account menu.")
      .replace("{{PRIMARY_LABEL}}", "Open active workspace")
      .replace("{{PRIMARY_HREF}}", "/")
      .replace("{{REFERENCE}}", '<p class="reference">Reference: browser-403-test</p>');
    await page.route("**/browser-forbidden", (route) => route.fulfill({ status: 403, contentType: "text/html", body }));
    await page.route("**/app-shell/assets/error-page.css*", (route) => route.fulfill({ contentType: "text/css", body: errorCss }));

    const response = await page.goto("/browser-forbidden");
    expect(response?.status()).toBe(403);
    await expect(page.getByRole("heading", { name: "This page is not available for your active role." })).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter(
      ({ impact }) => impact === "critical" || impact === "serious",
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
  });

  const routes: Array<{ path: string; sessionRole?: "organizer" | "speaker" }> = [
    { path: "/docs" },
    { path: "/admin", sessionRole: "organizer" },
    { path: "/admin/events", sessionRole: "organizer" },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222",
      sessionRole: "organizer",
    },
    { path: "/admin/people", sessionRole: "organizer" },
    { path: "/account", sessionRole: "organizer" },
    { path: `/admin/events/${eventId}/cfp`, sessionRole: "organizer" },
    { path: "/reviews", sessionRole: "organizer" },
    { path: "/speaker", sessionRole: "speaker" },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/access",
      sessionRole: "organizer",
    },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/onboarding",
      sessionRole: "organizer",
    },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/messages",
      sessionRole: "organizer",
    },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/agenda",
      sessionRole: "organizer",
    },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/workspace",
      sessionRole: "organizer",
    },
    {
      path: "/events/22222222-2222-4222-8222-222222222222/speakers",
      sessionRole: "organizer",
    },
    { path: "/events/22222222-2222-4222-8222-222222222222/schedule" },
    { path: "/embeds/events/22222222-2222-4222-8222-222222222222/schedule" },
  ];

  routes.forEach((route) => {
    test(`${route.path} has no automatically detectable serious violations`, async ({ page }) => {
      if (route.path.startsWith("/events/") || route.path.startsWith("/embeds/events/")) {
        await page.route(
          (url) => url.pathname === route.path,
          (documentRoute) => documentRoute.fulfill({
            contentType: "text/html",
            body: route.path.endsWith("/speakers") ? speakerGalleryHtml : scheduleHtml,
          }),
        );
      }
      if (route.sessionRole) {
        const speaker = route.sessionRole === "speaker";
        const session = {
          authenticated: true,
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          email: speaker ? "speaker@example.com" : "admin@example.com",
          display_name: speaker ? "Example Speaker" : "Example Admin",
          csrf_token: "browser-test-csrf",
          account_roles: [route.sessionRole],
          active_role: route.sessionRole,
          default_role: route.sessionRole,
          organization_id: "11111111-1111-4111-8111-111111111111",
          event_id: "22222222-2222-4222-8222-222222222222",
          organization_access: speaker ? [] : [{
            organization_id: "11111111-1111-4111-8111-111111111111",
            organization_name: "Example Organization",
            permissions: ["owner"],
          }],
          event_access: [{
            organization_id: "11111111-1111-4111-8111-111111111111",
            event_id: "22222222-2222-4222-8222-222222222222",
            event_name: "Example Event",
            permissions: speaker ? [] : ["owner"],
            assignments: speaker ? ["speaker"] : [],
          }],
        };
        // Keep page-level loaders on their rendered error/empty states instead
        // of letting an unmocked API return 401 and redirect the Axe scan to
        // the sign-in page. More-specific fixtures registered below win.
        await page.route("**/api/v1/**", async (apiRoute) => {
          await apiRoute.fulfill({
            status: 404,
            contentType: "application/json",
            body: JSON.stringify({ error: { code: "not_found", message: "Not found" } }),
          });
        });
        await page.route("**/api/v1/auth/session", async (apiRoute) => {
          await apiRoute.fulfill({ contentType: "application/json", body: JSON.stringify(session) });
        });
        await page.route("**/api/v1/session", async (apiRoute) => {
          await apiRoute.fulfill({ contentType: "application/json", body: JSON.stringify(session) });
        });
        await page.route("**/api/v1/account/profile", async (apiRoute) => {
          await apiRoute.fulfill({
            contentType: "application/json",
            body: JSON.stringify({ email: session.email, display_name: session.display_name, job_title: null, company: null, time_zone: "UTC", version: 1 }),
          });
        });
      }
      const response = await page.goto(route.path);
      expect(response?.ok()).toBeTruthy();
      await expect(page.locator("main")).toBeVisible();
      await page.waitForLoadState("networkidle");

      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      const blocking = results.violations.filter(
        ({ impact }) => impact === "critical" || impact === "serious",
      );
      expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
    });
  });
});
