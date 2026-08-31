import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Opt-in rendered audit of an existing LOCAL demo dataset. This reads real
// Worker pages; it does not seed, reset, submit forms, or modify event records.
// Empty datasets cannot establish populated-state visual coverage.
const enabled = process.env.UI_DESIGN_AUDIT === "1";

async function inspect(page: Page, path: string, label: string, expectedStatus = 200) {
  const response = await page.goto(path);
  await expect(page.locator("main")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  const title = await page.title();
  expect.soft(response?.status(), `${label}: HTTP status`).toBe(expectedStatus);
  if (label !== "sign-in") expect.soft(title, `${label}: must not silently become sign-in`).not.toMatch(/^Sign in/);
  const geometry = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    mainWidth: document.querySelector("main")!.getBoundingClientRect().width,
  }));
  expect.soft(geometry.scrollWidth, `${label}: horizontal overflow`).toBeLessThanOrEqual(geometry.width + 2);
  expect.soft(geometry.mainWidth, `${label}: usable main content`).toBeGreaterThan(240);
  const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const violations = scan.violations.filter(v => v.impact === "critical" || v.impact === "serious");
  expect.soft(violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), `${label}: accessibility`).toEqual([]);
  const capture = test.info().outputPath(`${label}.png`);
  await page.screenshot({ path: capture, fullPage: true });
  await test.info().attach(label, { path: capture, contentType: "image/png" });
}

test.describe("reference design local rendered page audit", () => {
  test.skip(!enabled, "Opt in with UI_DESIGN_AUDIT=1 against the local demo dataset");
  test.setTimeout(240_000);

  test.beforeEach(async ({ baseURL }) => {
    expect(new URL(baseURL!).hostname, "Audit must never target a deployed environment").toMatch(/^(localhost|127\.0\.0\.1|worker)$/);
  });

  test("organizer and published event surfaces", async ({ page }) => {
    await page.goto("/sign-in?redirect=%2Fadmin");
    await page.getByRole("button", { name: "Sign in as demo organizer", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Organizer workspace", exact: true })).toBeVisible();
    const eventLink = page.locator('main a[href^="/admin/events/"]').filter({ hasText: /.+/ }).first();
    await expect(eventLink).toBeVisible();
    // Discover an existing event from actual rendered links, never create one.
    const hrefs = await page.locator('main a[href^="/admin/events/"]').evaluateAll(nodes => nodes.map(n => n.getAttribute("href")!));
    const eventPath = hrefs.find(h => /^\/admin\/events\/[^/?]+$/.test(h) && !h.endsWith("/new"));
    expect(eventPath, "A populated demo event is required").toBeTruthy();
    const publicLinks = await page.locator('main a[href^="/cfp/"], main a[href^="/events/"]').evaluateAll(nodes => nodes.map(n => n.getAttribute("href")!));
    await inspect(page, "/admin", "organizer-home");
    await inspect(page, "/admin/people", "people");
    await inspect(page, "/admin/organization", "organization");
    await inspect(page, "/account", "account");
    await inspect(page, eventPath!, "event-overview");
    const eventLinks = await page.locator(`a[href^="${eventPath}/"]`).evaluateAll(nodes => nodes.map(n => n.getAttribute("href")!));
    const safePaths = [...new Set(eventLinks)].filter(h => !h.includes("?") && !h.includes("#"));
    for (const path of safePaths) await inspect(page, path, `event-${path.split("/").pop()}`);
    for (const path of [...new Set(publicLinks)].slice(0, 3)) await inspect(page, path, `public-${path.split("/").pop()}`);
  });

  test("public entry, documentation, and recovery surfaces", async ({ page }) => {
    for (const [path, label] of [["/", "landing"], ["/sign-in", "sign-in"], ["/calls", "calls"], ["/docs", "api-docs"], ["/engine-room", "engine-room"]]) {
      await inspect(page, path, label);
    }
    await inspect(page, "/design-audit-missing-page", "not-found", 404);
  });

  for (const role of ["reviewer", "speaker"] as const) {
    test(`${role} workspace`, async ({ page }) => {
      await page.goto("/sign-in");
      await page.getByRole("button", { name: `Sign in as demo ${role}`, exact: true }).click();
      await page.waitForURL(role === "reviewer" ? "**/reviews" : "**/speaker");
      await inspect(page, role === "reviewer" ? "/reviews" : "/speaker", `${role}-home`);
    });
  }
});
