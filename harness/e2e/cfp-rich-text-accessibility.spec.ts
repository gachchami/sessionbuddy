import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const template = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/admin_programs.html"),
  "utf8",
);
const productCss = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/product.css"),
  "utf8",
);
const apiClient = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/api_client.js"),
  "utf8",
);
const programsScript = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/admin_programs.js"),
  "utf8",
);
const pageHtml = template
  .replace(/<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/, `<style>${productCss}</style>`)
  .replace(/<link rel="stylesheet" href="\/app-shell\/assets\/app-shell\.css\?v=\d+">/, "")
  .replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, "")
  .replace("</body>", `<script>${apiClient}</script><script>${programsScript}</script></body>`);

async function serveBuilder(page: Page) {
  await page.route(`**/admin/events/${eventId}/cfp`, (route) => route.fulfill({
    contentType: "text/html",
    body: pageHtml,
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "organizer@example.test",
      csrf_token: "browser-test-csrf",
      organization_id: organizationId,
      event_id: eventId,
    }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      organization_id: organizationId,
      event_id: eventId,
      event_name: "Accessible Conference",
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

async function selectDescription(page: Page) {
  await page.locator("#cfp-description-editor").evaluate((editor) => {
    editor.textContent = "Read the submission guidelines";
    const range = document.createRange();
    range.selectNodeContents(editor);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
  });
}

test.describe("CFP rich text accessibility", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("editor and link dialog support keyboard use, validation, focus return, and Axe", async ({ page }) => {
    await serveBuilder(page);
    await page.goto(`/admin/events/${eventId}/cfp`);

    const editor = page.getByRole("textbox", { name: "CFP description" });
    await expect(editor).toBeVisible();
    await expect(editor).toHaveAttribute("aria-describedby", "cfp-description-help");
    await selectDescription(page);
    const linkButton = page.getByRole("button", { name: "Link", exact: true });
    await linkButton.click();

    const dialog = page.getByRole("dialog", { name: "Add a link" });
    await expect(dialog).toBeVisible();
    const input = page.getByLabel("Web address");
    await expect(input).toBeFocused();
    await input.fill("javascript:alert(1)");
    await page.getByRole("button", { name: "Add link" }).click();
    await expect(page.locator("#cfp-link-error")).toContainText("https:// or http://");
    await expect(input).toBeFocused();

    const results = await new AxeBuilder({ page })
      .include("#cfp-link-dialog")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter(
      ({ impact }) => impact === "critical" || impact === "serious",
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);

    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(dialog).toBeHidden();
    await expect(linkButton).toBeFocused();

    await selectDescription(page);
    await linkButton.click();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(linkButton).toBeFocused();

    await selectDescription(page);
    await linkButton.click();
    await input.fill("https://example.test/guidelines");
    await page.getByRole("button", { name: "Add link" }).click();
    await expect(dialog).toBeHidden();
    await expect(linkButton).toBeFocused();
    await expect(editor.locator('a[href="https://example.test/guidelines"]')).toHaveText(
      "Read the submission guidelines",
    );
  });

  test("link dialog fits a narrow mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await serveBuilder(page);
    await page.goto(`/admin/events/${eventId}/cfp`);
    await selectDescription(page);
    await page.getByRole("button", { name: "Link", exact: true }).click();

    const bounds = await page.locator("#cfp-link-dialog").boundingBox();
    expect(bounds).toBeTruthy();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(321);
  });
});
