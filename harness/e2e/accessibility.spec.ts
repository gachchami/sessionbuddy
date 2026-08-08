import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.describe("MVP experience accessibility", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("home page has no automatically detectable serious violations", async ({ page }) => {
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
    const response = await page.goto(`/auth/verify?token=${expiredToken}`);
    expect(response?.status()).toBe(404);
    await expect(page.locator("main")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter(
      ({ impact }) => impact === "critical" || impact === "serious",
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
  });

  const routes: Array<{ path: string; sessionEndpoint?: string }> = [
    { path: "/cfp-integration" },
    { path: "/admin", sessionEndpoint: "/api/v1/demo/session" },
    { path: "/admin/events", sessionEndpoint: "/api/v1/demo/session" },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222",
      sessionEndpoint: "/api/v1/demo/session",
    },
    { path: "/admin/speakers", sessionEndpoint: "/api/v1/demo/session" },
    { path: "/account", sessionEndpoint: "/api/v1/demo/session" },
    { path: "/admin/programs", sessionEndpoint: "/api/v1/demo/session" },
    { path: "/reviews", sessionEndpoint: "/api/v1/demo/session" },
    { path: "/speaker", sessionEndpoint: "/api/v1/demo/speaker-session" },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/access",
      sessionEndpoint: "/api/v1/demo/session",
    },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/onboarding",
      sessionEndpoint: "/api/v1/demo/session",
    },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/agenda",
      sessionEndpoint: "/api/v1/demo/session",
    },
    {
      path: "/admin/events/22222222-2222-4222-8222-222222222222/workspace",
      sessionEndpoint: "/api/v1/demo/session",
    },
    {
      path: "/events/22222222-2222-4222-8222-222222222222/speakers",
      sessionEndpoint: "/api/v1/demo/session",
    },
    { path: "/events/22222222-2222-4222-8222-222222222222/schedule" },
    { path: "/embeds/events/22222222-2222-4222-8222-222222222222/schedule" },
  ];

  routes.forEach((route, routeIndex) => {
    test(`${route.path} has no automatically detectable serious violations`, async ({ page }) => {
      if (route.sessionEndpoint) {
        const session = await page.request.post(route.sessionEndpoint, {
          headers: { "cf-connecting-ip": `192.0.2.${routeIndex + 1}` },
        });
        expect(session.ok()).toBeTruthy();
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
