import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * An eval agent could not operate the CFP builder's "Session format" row: the
 * row and its reorder buttons sat behind the sticky chrome, clicks aimed at
 * them were intercepted, and scrolling them into view did not help because the
 * chrome kept covering the same band (`claude/ux-evidence-log.md`, 2026-08-16).
 *
 * Two causes. `.question-card` carried `overflow: hidden`, which made each card
 * the scrollport `scrollIntoView` resolves against, so the target's
 * scroll-margin was discarded entirely and the row landed at viewport y=0. And
 * the shell's scroll offset was a literal `9rem`, smaller than the `9.5rem`
 * chrome it had to clear, before counting the two sticky bars the builder
 * stacks below it.
 *
 * The sibling spec, `cfp-session-format-rules.spec.ts`, strips `app_shell.css`
 * and never renders the shell, so no assertion in it could have caught this.
 * This spec deliberately loads the real shell stylesheet and the real
 * `app_shell.js`, then hit-tests the row after scrolling it into view -- the
 * exact recovery the eval agent attempted and the product denied.
 */

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const userId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const staticDir = resolve(__dirname, "../../src/sessionbuddy/static");
const read = (name: string) => readFileSync(resolve(staticDir, name), "utf8");

// Same construction as the sibling spec, except the shell stylesheet and the
// shell script are kept: the whole point here is the chrome's geometry.
const pageHtml = read("admin_programs.html")
  .replace(
    /<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/,
    `<style>${read("product.css")}</style>`,
  )
  .replace(
    /<link rel="stylesheet" href="\/app-shell\/assets\/app-shell\.css\?v=\d+">/,
    `<style>${read("app_shell.css")}</style>`,
  )
  .replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, "")
  .replace(
    "</body>",
    `<script>${read("api_client.js")}</script>`
      + `<script>${read("app_shell.js")}</script>`
      + `<script>${read("admin_programs.js")}</script></body>`,
  );

const session = {
  authenticated: true,
  user_id: userId,
  email: "organizer@example.test",
  display_name: "Ola Organizer",
  profile_complete: true,
  csrf_token: "browser-test-csrf",
  active_role: "organizer",
  account_roles: ["organizer"],
  organization_id: organizationId,
  organization_name: "Format Org",
  organization_access: [{ organization_id: organizationId, permissions: ["owner"] }],
  event_access: [{ event_id: eventId, permissions: ["owner"] }],
  event_id: eventId,
};

async function serveBuilder(page: Page) {
  // Registered first on purpose: Playwright matches routes last-registered
  // first, so this catch-all stays the fallback for anything not named below.
  await page.route("**/api/v1/**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route(`**/admin/events/${eventId}/cfp`, (route) => route.fulfill({
    contentType: "text/html",
    body: pageHtml,
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      organization_id: organizationId,
      event_id: eventId,
      event_name: "Format Conference",
      event_starts_at_ms: Date.UTC(2030, 5, 1, 9),
      published_form: null,
    }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ id: eventId, status: "active", time_zone: "UTC" }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda/tracks`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
}

/**
 * True when a real click at the element's centre would reach it, rather than
 * landing on whatever is painted over it. This is the property the eval agent
 * needed and Playwright's own actionability checks hide, because Playwright
 * re-scrolls and retries until a click lands.
 */
async function reachableAtItsCentre(target: Locator): Promise<{ ok: boolean; blockedBy: string }> {
  return target.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const top = document.elementFromPoint(
      Math.round(rect.left + rect.width / 2),
      Math.round(rect.top + rect.height / 2),
    );
    const ok = top === element || element.contains(top);
    return {
      ok,
      blockedBy: ok ? "" : `${top?.tagName ?? "nothing"}.${(top as HTMLElement)?.className ?? ""}`.trim(),
    };
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 720, expectedScrollPadding: 264 },
  { name: "small desktop", width: 1024, height: 768, expectedScrollPadding: 264 },
  { name: "narrow", width: 900, height: 800, expectedScrollPadding: 309.6 },
  { name: "mobile", width: 390, height: 844, expectedScrollPadding: 305.6 },
]) {
  test(`the Session format row stays operable under the sticky chrome on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await serveBuilder(page);
    await page.goto(`/admin/events/${eventId}/cfp`);

    // If the shell did not render, the rest of this spec would be measuring a
    // page with no chrome -- exactly the blind spot that let this ship.
    await expect(page.locator(".sb-topbar")).toBeVisible();
    await expect(page.locator(".sb-event-nav")).toBeVisible();
    const headingActions = page.locator(".cfp-page-heading > .actions");
    await expect(headingActions.locator("#preview-cfp")).toBeVisible();
    const headingActionsBox = await headingActions.boundingBox();
    expect(headingActionsBox).not.toBeNull();
    expect(headingActionsBox!.x).toBeGreaterThanOrEqual(0);
    expect(headingActionsBox!.x + headingActionsBox!.width).toBeLessThanOrEqual(viewport.width + 1);
    const viewportPadding = await page.evaluate(() =>
      Number.parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop),
    );
    expect(viewportPadding).toBeCloseTo(viewport.expectedScrollPadding, 3);

    await page.locator('.cfp-outline-item[data-selection="proposal"]').click();
    const formatCard = page.locator("fieldset.question-card[data-index]").filter({ hasText: "Session format" });
    const summary = formatCard.locator("summary");
    await summary.click();

    for (const target of [
      summary,
      formatCard.locator('[aria-label^="Move Session format up"]'),
      formatCard.locator('[aria-label^="Move Session format down"]'),
    ]) {
      // The recovery an operator (human or agent) reaches for when a control is
      // covered. It has to actually work.
      await target.evaluate((element) => element.scrollIntoView({ block: "start", behavior: "instant" }));
      const { ok, blockedBy } = await reachableAtItsCentre(target);
      expect(
        ok,
        `after scrolling it into view, a click at its centre was intercepted by ${blockedBy}`,
      ).toBe(true);
    }
  });
}
