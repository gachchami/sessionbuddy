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
    workspace_state: "ready",
    workspace_path: "/admin",
    usable_personas: [{ role: "organizer", workspace_state: "ready", workspace_path: "/admin" }],
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

async function openEventNavigation(page: Page) {
  await expect(page.locator(".sb-event-nav")).toHaveCount(1, { timeout: 4000 });
  const menu = page.getByRole("button", { name: "Open navigation", exact: true });
  if (await menu.isVisible()) await menu.click();
  await expect(page.locator(".sb-event-nav")).toBeVisible();
}

test.describe("app shell navigation runtime", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("mobile navigation backdrop covers viewport corners and dismisses safely", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);
    await openEventNavigation(page);
    const backdrop = page.locator(".sb-nav-backdrop");
    await expect(backdrop).toBeVisible();
    await expect(page.locator(".sb-sidebar a").first()).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await expect(backdrop).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.locator(".sb-sidebar a").first()).toBeFocused();
    // Hit testing catches a pill-shaped overlay even when its bounding box
    // spans the viewport. Both exposed corners must actually intercept input.
    expect(await page.evaluate(() => [[innerWidth - 2, 2], [innerWidth - 2, innerHeight - 2]]
      .every(([x, y]) => document.elementFromPoint(x, y)?.classList.contains("sb-nav-backdrop")))).toBe(true);
    await page.mouse.click(388, 842);
    await expect(backdrop).toBeHidden();
    await expect(page.getByRole("button", { name: "Open navigation", exact: true })).toBeFocused();
  });

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

    // Sweep vertically across the sidebar links in under the intent delay:
    // crossing must not arm any prerender.
    const box = (await nav.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + 2);
    await page.mouse.move(box.x + box.width / 2, box.y + box.height - 2, { steps: 8 });
    await page.mouse.move(box.x + box.width + 40, 100);
    await page.waitForTimeout(250);
    await expect(speculationRules(page)).toHaveCount(0);

    // Sections of the current event swap in place on click, so resting on
    // one only warms the HTML fetch; a prerendered document would be wasted.
    const cfpLink = nav.locator('a[href$="/cfp"]');
    await cfpLink.hover();
    await page.waitForTimeout(300);
    await expect(speculationRules(page)).toHaveCount(0);
    await expect(prefetchHints(page)).not.toHaveCount(0);

    // Destinations outside the event are document navigations: resting on a
    // link past the delay arms exactly one candidate, and moving intent
    // elsewhere replaces it instead of accumulating.
    const globalNav = page.locator(".sb-sidebar__nav");
    await globalNav.locator('a[href="/admin"]').hover();
    await page.waitForTimeout(300);
    await expect(speculationRules(page)).toHaveCount(1);
    expect(await speculationRules(page).textContent()).toContain("/admin");
    await globalNav.locator('a[href="/admin/people"]').hover();
    await page.waitForTimeout(300);
    await expect(speculationRules(page)).toHaveCount(1);
    expect(await speculationRules(page).textContent()).toContain("/admin/people");

    expect(cspViolations).toEqual([]);
  });

  test("touch intent warms the cache and never prerenders", async ({ page, isMobile }) => {
    test.skip(!isMobile, "Touch semantics are covered by the mobile-chrome project.");
    await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);
    await openEventNavigation(page);

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

    await openEventNavigation(page);
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

  test("a refused Back/Forward restores the URL, and consent lets it proceed", async ({ page }) => {
    await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);
    await openEventNavigation(page);
    await page.locator('.sb-event-nav a[href$="/cfp"]').click();
    await page.waitForURL(`**/admin/events/${eventId}/cfp`);
    await expect(page).toHaveTitle("Call for Proposals · SessionBuddy");

    // Stand in for a page with unsaved work: refuse to leave until consent,
    // bound to the page signal exactly as the editors do.
    await page.evaluate(() => {
      const w = window as Window & { __decision?: boolean; __prompts?: number; __sameDocument?: boolean };
      w.__sameDocument = true;
      w.__prompts = 0;
      w.__decision = false;
      window.addEventListener("sessionbuddy:navigate", (event) => event.preventDefault(), {
        signal: (window as Window & { SessionBuddyPage?: { signal: AbortSignal } }).SessionBuddyPage!.signal,
      });
      window.confirm = () => { w.__prompts! += 1; return Boolean(w.__decision); };
    });

    // Cancel: the browser had already moved, so the URL comes back to the
    // page that is still on screen.
    await page.goBack().catch(() => {});
    await expect(page).toHaveURL(new RegExp(`/admin/events/${eventId}/cfp$`));
    await expect(page).toHaveTitle("Call for Proposals · SessionBuddy");
    expect(await page.evaluate(() => (window as Window & { __prompts?: number }).__prompts)).toBe(1);

    // Accept: the same traversal now completes as an in-place swap.
    await page.evaluate(() => { (window as Window & { __decision?: boolean }).__decision = true; });
    await page.goBack().catch(() => {});
    await expect(page).toHaveURL(new RegExp(`/admin/events/${eventId}$`));
    await expect(page).toHaveTitle(/DevFlow|Event overview|SessionBuddy/);
    await expect(page.locator('.sb-event-nav a[href$="/cfp"]')).not.toHaveAttribute("aria-current", "page");
    expect(await page.evaluate(() => (window as Window & { __sameDocument?: boolean }).__sameDocument)).toBe(true);
  });

  test("a destination whose assets fail falls back to a document navigation", async ({ page }) => {
    await mockApis(page);
    await page.route("**/admin/agenda/assets/agenda.css*", (route: Route) => route.abort());
    await page.goto(`/admin/events/${eventId}`);
    await openEventNavigation(page);
    await page.evaluate(() => { (window as Window & { __sameDocument?: boolean }).__sameDocument = true; });
    await page.locator('.sb-event-nav a[href$="/agenda"]').click();
    await page.waitForURL(`**/admin/events/${eventId}/agenda`);
    // The working page was never half-swapped: the browser loaded the
    // destination as a fresh document instead.
    await expect.poll(() => page.evaluate(() => (window as Window & { __sameDocument?: boolean }).__sameDocument)).toBeUndefined();
    await expect(page.locator("main")).toHaveCount(1);
  });

  test("rapid overlapping section clicks settle on the last destination", async ({ page }) => {
    await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);
    await openEventNavigation(page);
    // Two clicks inside one task, faster than any drawer or fetch can settle.
    await page.evaluate(() => {
      (document.querySelector('.sb-event-nav a[href$="/cfp"]') as HTMLAnchorElement).click();
      (document.querySelector('.sb-event-nav a[href$="/agenda"]') as HTMLAnchorElement).click();
    });
    await page.waitForURL(`**/admin/events/${eventId}/agenda`);
    await expect(page).toHaveTitle("Agenda editor · SessionBuddy");
    await expect(page.locator('script[src*="/admin/agenda/assets/agenda.js"]')).toHaveCount(1);
    await expect(page.locator('script[src*="admin-programs.js"]')).toHaveCount(0);
    await expect(page.locator('link[href*="/admin/agenda/assets/agenda.css"]')).toHaveCount(1);
    await expect(page.locator("main")).toHaveCount(1);
    await expect(page.locator('.sb-event-nav a[href$="/agenda"]')).toHaveAttribute("aria-current", "page");
  });

  test("navigating between event pages keeps the shell and swaps the section in place", async ({ page }) => {
    const control = await mockApis(page);
    await page.goto(`/admin/events/${eventId}`);
    await openEventNavigation(page);
    await page.waitForFunction(() => sessionStorage.getItem("sessionbuddy:shell-session") !== null);

    // The persisted record is presentation-only: no CSRF token, no user id.
    const cached = await page.evaluate(() => JSON.parse(sessionStorage.getItem("sessionbuddy:shell-session")!));
    expect(cached.session.csrf_token).toBeUndefined();
    expect(cached.session.user_id).toBeUndefined();
    expect(cached.session.workspace_path).toBeUndefined();
    expect(cached.session.organization_access).toBeUndefined();

    // Inside one event the shell and its confirmed session stay on screen:
    // only the section body is fetched and swapped, and the URL still moves.
    await page.evaluate(() => { (window as Window & { __sameDocument?: boolean }).__sameDocument = true; });
    control.delayMs = 2500;
    await page.locator('.sb-event-nav a[href$="/cfp"]').click();
    await page.waitForURL(`**/admin/events/${eventId}/cfp`);
    await expect(page).toHaveTitle("Call for Proposals · SessionBuddy");
    await expect(page.locator(".sb-event-nav")).toHaveCount(1);
    await expect(page.locator(".sb-account")).toHaveCount(1);
    await expect(page.locator('.sb-event-nav a[href$="/cfp"]')).toHaveAttribute("aria-current", "page");
    expect(await page.evaluate(() => (window as Window & { __sameDocument?: boolean }).__sameDocument)).toBe(true);
    await expect(page.locator("main")).toBeFocused();

    // Durable URLs stay the browser's: Back and Forward replay the same
    // in-place swap without leaving the document.
    await page.goBack();
    await page.waitForURL(`**/admin/events/${eventId}`);
    await expect(page.locator('.sb-event-nav a[href$="/cfp"]')).not.toHaveAttribute("aria-current", "page");
    expect(await page.evaluate(() => (window as Window & { __sameDocument?: boolean }).__sameDocument)).toBe(true);
    await page.goForward();
    await page.waitForURL(`**/admin/events/${eventId}/cfp`);
    await expect(page).toHaveTitle("Call for Proposals · SessionBuddy");
    expect(await page.evaluate(() => (window as Window & { __sameDocument?: boolean }).__sameDocument)).toBe(true);
  });
});
