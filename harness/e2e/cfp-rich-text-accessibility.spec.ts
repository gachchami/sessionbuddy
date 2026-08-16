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
    body: JSON.stringify({
      id: eventId,
      status: "active",
      time_zone: "UTC",
      accent_color: "#6d4aff",
      logo_url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='24'%3E%3C/svg%3E",
      cover_image_url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='120'%3E%3C/svg%3E",
    }),
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

  test("the character count and draft preview preserve rich description edits", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await serveBuilder(page);
    await page.goto(`/admin/events/${eventId}/cfp`);

    const editor = page.getByRole("textbox", { name: "CFP description" });
    await editor.evaluate((node) => {
      node.replaceChildren();
      const paragraph = document.createElement("p");
      const strong = document.createElement("strong");
      strong.textContent = "Bring a practical lesson";
      paragraph.append(strong);
      const list = document.createElement("ul");
      const item = document.createElement("li");
      item.textContent = "Include concrete examples";
      list.append(item);
      const link = document.createElement("a");
      link.href = "https://example.test/guidelines";
      link.textContent = "very-long-guideline-reference-".repeat(12);
      node.append(paragraph, list, link);
      node.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
    });

    await expect(page.locator('[name="description_html"] + .character-counter')).not.toHaveText(/^0 of/);
    await page.getByRole("button", { name: "Preview form", exact: true }).click();
    const preview = page.locator("#cfp-selection-preview");
    await expect(preview.locator("strong", { hasText: "Bring a practical lesson" })).toBeVisible();
    await expect(preview.locator("li", { hasText: "Include concrete examples" })).toBeVisible();
    await expect(preview.getByRole("img", { name: "Accessible Conference logo" })).toBeVisible();
    await expect(preview.getByRole("img", { name: "Accessible Conference cover" })).toBeVisible();
    await expect(preview.locator(".cfp-preview-brand")).toHaveCSS("border-top-color", "rgb(109, 74, 255)");
    const geometry = await preview.evaluate((node) => {
      const content = node.querySelector<HTMLElement>(".cfp-preview-content")!;
      const description = node.querySelector<HTMLElement>(".cfp-preview-description")!;
      const cover = node.querySelector<HTMLElement>(".cfp-preview-brand__cover")!;
      const coverBounds = cover.getBoundingClientRect();
      return {
        contentFits: content.scrollWidth <= content.clientWidth + 1,
        descriptionFits: description.scrollWidth <= description.clientWidth + 1,
        descriptionFontSize: Number.parseFloat(getComputedStyle(description).fontSize),
        coverRatio: coverBounds.width / coverBounds.height,
        previewOverflowY: getComputedStyle(node).overflowY,
      };
    });
    expect(geometry.contentFits).toBe(true);
    expect(geometry.descriptionFits).toBe(true);
    expect(geometry.descriptionFontSize).toBe(16);
    // The draft preview uses the same wide event-cover crop as the published
    // CFP instead of a taller, misleading card-only composition.
    expect(geometry.coverRatio).toBeCloseTo(3, 1);
    expect(geometry.previewOverflowY).toBe("visible");
  });
});
