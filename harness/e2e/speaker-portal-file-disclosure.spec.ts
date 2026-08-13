import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

/**
 * CNT-S2 spent 12 clicks on the `slides.pdf` summary while its parent
 * "Files and activity" <details> was closed. The descendants stayed in the
 * accessibility representation, so a reference resolved, but the control was
 * not actionable and nothing named the parent that had to be opened first.
 *
 * The outer disclosure is gone. These assertions pin the path that replaced it:
 * the file row is reachable with no hidden parent, one click opens its versions,
 * the discussion is then reachable, the flow survives a re-render, and it all
 * works from the keyboard.
 */

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const html = readFileSync(resolve(staticRoot, "speaker_portal.html"), "utf8");
const productCss = readFileSync(resolve(staticRoot, "product.css"), "utf8");
const speakerCss = readFileSync(resolve(staticRoot, "speaker.css"), "utf8")
  .replace(/^@import[^;]+;\s*/, "");
const apiClient = readFileSync(resolve(staticRoot, "api_client.js"), "utf8");
const portalScript = readFileSync(resolve(staticRoot, "speaker_portal.js"), "utf8");

const portalHtml = html
  .replace(/<link rel="stylesheet" href="\/speaker\/assets\/speaker\.css\?v=\d+">/, `<style>${productCss}\n${speakerCss}</style>`)
  .replace(/<link rel="stylesheet" href="\/app-shell\/assets\/app-shell\.css\?v=\d+">/, "")
  .replace(/<script src="\/app-shell\/assets\/api-client\.js\?v=\d+" defer><\/script>/, "")
  .replace(/<script src="\/app-shell\/assets\/app-shell\.js\?v=\d+" defer><\/script>/, "")
  .replace(/<script src="\/speaker\/assets\/speaker-portal\.js\?v=\d+" defer><\/script>/, "")
  .replace("</body>", `<script>${apiClient}</script><script>${portalScript}</script></body>`);

const EVENT_ID = "event-disclosure";
const ASSET_ID = "asset-slides";
const VERSION_ID = "version-2";

const portal = {
  event: {
    id: EVENT_ID,
    name: "AI Engineering Summit 2026",
    starts_at_ms: Date.UTC(2026, 9, 12),
    ends_at_ms: Date.UTC(2026, 9, 14),
    time_zone: "America/New_York",
  },
  event_speaker_id: "event-speaker-disclosure",
  profile: { display_name: "Priya Raman" },
  submissions: [],
  tasks: [],
  notifications: [],
  activities: [],
};

const assets = {
  data: [{
    id: ASSET_ID,
    kind: "slides",
    submission_id: "submission-1",
    filename: "slides.pdf",
    content_type: "application/pdf",
    byte_size: 2048,
    state: "clean",
    generation: 2,
    uploaded_at_ms: Date.UTC(2026, 9, 1),
    version_count: 2,
    version_comment: "Tightened the benchmark slide",
    versions: [
      { id: VERSION_ID, generation: 2, filename: "slides.pdf", content_type: "application/pdf", byte_size: 2048, state: "current", uploaded_at_ms: Date.UTC(2026, 9, 1), version_comment: "Tightened the benchmark slide" },
      { id: "version-1", generation: 1, filename: "slides.pdf", content_type: "application/pdf", byte_size: 1024, state: "superseded", uploaded_at_ms: Date.UTC(2026, 8, 20), version_comment: "Initial upload" },
    ],
  }],
};

async function servePortal(page: Page, { resourceDelayMs = 0 } = {}) {
  await page.route("**/speaker", (route) => route.fulfill({ contentType: "text/html", body: portalHtml }));
  await page.route("**/api/v1/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ csrf_token: "disclosure-csrf" }),
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ csrf_token: "disclosure-csrf" }),
  }));
  await page.route("**/api/v1/speaker/proposal-drafts", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/speaker/portal**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(portal),
  }));
  await page.route("**/api/v1/speaker/resources", async (route) => {
    if (resourceDelayMs) await new Promise((resolveDelay) => setTimeout(resolveDelay, resourceDelayMs));
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) });
  });
  await page.route(`**/api/v1/speaker/events/${EVENT_ID}/assets`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(assets),
  }));
  await page.route(`**/api/v1/speaker/events/${EVENT_ID}/assets/${ASSET_ID}`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      ...assets.data[0],
      comments: [{
        id: "comment-1",
        version_id: VERSION_ID,
        author_name: "Jordan Alvarez",
        body_text: "Please raise the contrast on slide 4.",
        parent_comment_id: null,
        visibility: "shared",
        created_at_ms: Date.UTC(2026, 9, 2),
      }],
    }),
  }));
}

const fileCard = (page: Page) => page.locator("details.asset-history-card").filter({ hasText: "slides.pdf" });

test.describe("speaker portal file disclosure", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("the file row is reachable and expands without opening another control", async ({ page }) => {
    await servePortal(page);
    await page.goto("/speaker");

    // 1. No hidden parent: the Files section renders as a plain section, and
    //    slides.pdf is visible without expanding anything first.
    await expect(page.locator("details.event-group__more")).toHaveCount(0);
    const card = fileCard(page);
    const summary = card.locator("> summary");
    await expect(summary).toBeVisible();
    await expect(card).not.toHaveAttribute("open", /.*/);

    // 2. One click expands the version history. `click` asserts actionability,
    //    which is exactly what failed before.
    await summary.click();
    await expect(card).toHaveAttribute("open", "");
    await expect(card.getByText("Version 2")).toBeVisible();
    await expect(card.getByText("Version 1")).toBeVisible();

    // The replacement upload form is a plain section inside the open card, not
    // a further disclosure, and it precedes the history.
    const replace = card.locator("section.asset-replace");
    await expect(replace).toBeVisible();
    await expect(replace.locator("form")).toHaveAttribute("id", `asset-upload-form-${ASSET_ID}`);
    await expect(replace.getByRole("button", { name: "Upload slides" })).toBeVisible();
    await expect(replace.getByRole("button", { name: "Upload slides" })).toBeEnabled();

    // 3. Discussion is then reachable and actionable.
    const discussion = card.locator("details.asset-discussion").first();
    await expect(discussion.locator("> summary")).toBeVisible();
    await discussion.locator("> summary").click();
    await expect(discussion.locator(".asset-comment__body").getByText("Please raise the contrast on slide 4.", { exact: true })).toBeVisible();
    await expect(discussion.locator("form.asset-comment-form textarea")).toBeVisible();
  });

  test("a portfolio re-render does not reintroduce a hidden parent", async ({ page }) => {
    // Resources finish after assets. Their completion invokes renderPortfolio,
    // giving this test a real rebuild of the file nodes to observe.
    await servePortal(page, { resourceDelayMs: 500 });
    await page.goto("/speaker");
    const original = fileCard(page);
    await expect(original.locator("> summary")).toBeVisible();
    await original.evaluate((node) => { node.dataset.testOriginal = "true"; });

    // 4. loadResources() completes and runs the real rebuild path that
    // discards and recreates these nodes.
    await expect(page.locator('[data-test-original="true"]')).toHaveCount(0);
    await expect(fileCard(page).locator("> summary")).toBeVisible();

    await expect(page.locator("details.event-group__more")).toHaveCount(0);
    const summary = fileCard(page).locator("> summary");
    await expect(summary).toBeVisible();
    await summary.click();
    await expect(fileCard(page)).toHaveAttribute("open", "");
  });

  test("the same flow works from the keyboard", async ({ page }) => {
    await servePortal(page);
    await page.goto("/speaker");

    // 5. Focus the summary directly and activate it the way a keyboard user
    //    would; a <summary> toggles on Enter.
    const summary = fileCard(page).locator("> summary");
    await summary.focus();
    await expect(summary).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(fileCard(page)).toHaveAttribute("open", "");

    const discussion = fileCard(page).locator("details.asset-discussion").first();
    await discussion.locator("> summary").focus();
    await page.keyboard.press("Enter");
    await expect(discussion).toHaveAttribute("open", "");
    await expect(discussion.locator("form.asset-comment-form textarea")).toBeVisible();
  });
});
