import { expect, test, type Page, type Route } from "@playwright/test";

// Runtime coverage for the app-shell navigation work: the source-string tests
// prove the code exists; these prove the behavior against the real Worker -
// that Workerd's CSP actually admits the inline speculation rule, that hover
// intent is debounced to a single prerender candidate, that keyboard
// traversal never executes other pages, and that a document navigation paints
// the shell from the per-tab cached session before the network answers.

const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

function organizerSession() {
  return {
    authenticated: true,
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    email: "shell-navigation@example.test",
    display_name: "Shell Navigator",
    profile_complete: true,
    csrf_token: "browser-test-csrf",
    account_roles: ["organizer"],
    active_role: "organizer",
    default_role: "organizer",
    organization_id: organizationId,
    organization_name: "Example Organization",
    event_id: null,
    organization_access: [{
      organization_id: organizationId,
      organization_name: "Example Organization",
      permissions: ["owner"],
    }],
    event_access: [{
      event_id: eventId,
      event_name: "Example Event",
      assignments: [],
    }],
  };
}

type SessionControl = { delayMs: number };

async function mockApis(page: Page): Promise<SessionControl> {
  const control: SessionControl = { delayMs: 0 };
  // Registered first so the more specific session route below wins.
  await page.route("**/api/v1/**", (route: Route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/auth/session", async (route: Route) => {
    if (control.delayMs) await new Promise((resolve) => setTimeout(resolve, control.delayMs));
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(organizerSession()),
    });
  });
  return control;
}

const speculationRules = (page: Page) => page.locator('script[type="speculationrules"]');
const prefetchHints = (page: Page) => page.locator('link[rel="prefetch"][as="document"]');

test.describe("app shell navigation runtime", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("the Worker's CSP admits the speculation rule and hover keeps one debounced candidate", async ({ page, isMobile }) => {
    test.skip(isMobile, "Pointer hover intent is not a mobile interaction; see the touch test below.");
    const cspViolations: string[] = [];
    page.on("console", (message) => {
      if (message.text().includes("Content Security Policy")) cspViolations.push(message.text());
    });
    await mockApis(page);

    const response = await page.goto(`/admin/events/${eventId}`);
    expect(response!.headers()["content-security-policy"]).toContain("'inline-speculation-rules'");

    const nav = page.locator(".sb-event-nav");
    await expect(nav).toBeVisible();

    // Sweep the pointer across the whole bar in well under the intent delay:
    // crossing must not arm any prerender.
    const box = (await nav.boundingBox())!;
    await page.mouse.move(box.x + 2, box.y + box.height / 2);
    await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2, { steps: 8 });
    await page.mouse.move(box.x + box.width / 2, box.y + box.height + 60);
    await page.waitForTimeout(250);
    await expect(speculationRules(page)).toHaveCount(0);

    // Resting on a link past the delay arms exactly one candidate. Script
    // elements render no text, so the rule body is read via textContent.
    const cfpLink = nav.locator('a[href$="/cfp"]');
    await cfpLink.hover();
    await page.waitForTimeout(300);
    await expect(speculationRules(page)).toHaveCount(1);
    expect(await speculationRules(page).textContent()).toContain("/cfp");

    // ...and moving intent elsewhere replaces it instead of accumulating.
    const agendaLink = nav.locator('a[href$="/agenda"]');
    await agendaLink.hover();
    await page.waitForTimeout(300);
    await expect(speculationRules(page)).toHaveCount(1);
    expect(await speculationRules(page).textContent()).toContain("/agenda");

    expect(cspViolations).toEqual([]);
  });

  test("touch intent warms the cache and never prerenders", async ({ page, isMobile }) => {
    test.skip(!isMobile, "Touch semantics are covered by the mobile-chrome project.");
    await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);
    await expect(page.locator(".sb-event-nav")).toBeVisible();

    // touchstart is dispatched directly so warming can be observed without
    // the tap's click immediately navigating away from the assertions.
    await page.locator('.sb-event-nav a[href$="/cfp"]').dispatchEvent("touchstart");
    await page.locator('.sb-event-nav a[href$="/agenda"]').dispatchEvent("touchstart");
    await page.waitForTimeout(300);

    expect(await prefetchHints(page).count()).toBeGreaterThanOrEqual(2);
    await expect(speculationRules(page)).toHaveCount(0);
  });

  test("keyboard traversal warms the cache without executing other pages", async ({ page }) => {
    const documentLoads: string[] = [];
    page.on("request", (request) => {
      if (request.resourceType() === "document") documentLoads.push(request.url());
    });
    await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);

    const links = page.locator(".sb-event-nav a:not([aria-current='page'])");
    const linkCount = await links.count();
    expect(linkCount).toBeGreaterThan(3);
    for (let index = 0; index < linkCount; index += 1) {
      await links.nth(index).focus();
    }
    await page.waitForTimeout(300);

    await expect(speculationRules(page)).toHaveCount(0);
    expect(await prefetchHints(page).count()).toBeGreaterThan(0);
    // The initial navigation is the only full document load.
    expect(documentLoads.length).toBe(1);
  });

  test("navigating between event pages paints the shell before the network answers", async ({ page }) => {
    const control = await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);
    await expect(page.locator(".sb-event-nav")).toBeVisible();
    await page.waitForFunction(() => sessionStorage.getItem("sessionbuddy:shell-session") !== null);

    // The persisted record is presentation-only: no CSRF token, no user id.
    const cached = await page.evaluate(() => JSON.parse(sessionStorage.getItem("sessionbuddy:shell-session")!));
    expect(cached.session.csrf_token).toBeUndefined();
    expect(cached.session.user_id).toBeUndefined();
    expect(cached.session.event_access[0].event_id).toBe("cccccccc-cccc-4ccc-8ccc-cccccccccccc");

    // Delay the next session response well past the assertion window: only a
    // cached paint can put the chrome on screen this fast on the new document.
    control.delayMs = 2500;
    await page.locator('.sb-event-nav a[href$="/cfp"]').click();
    await page.waitForURL(`**/admin/events/${eventId}/cfp`);
    await expect(page.locator(".sb-event-nav")).toBeVisible({ timeout: 1200 });
    await expect(page.locator(".sb-account")).toBeVisible({ timeout: 1200 });
    await expect(page.locator(".sb-account summary")).toContainText("Shell Navigator");

    // Durable URLs stay the browser's: Back and Forward land on real
    // documents whose shells are equally immediate.
    await page.goBack();
    await page.waitForURL(`**/admin/events/${eventId}`);
    await expect(page.locator(".sb-event-nav")).toBeVisible({ timeout: 1200 });
    await page.goForward();
    await page.waitForURL(`**/admin/events/${eventId}/cfp`);
    await expect(page.locator(".sb-event-nav")).toBeVisible({ timeout: 1200 });
  });
});
