import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

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

const portal = {
  event: {
    id: "event-responsive",
    name: "AI Engineering Summit 2026",
    starts_at_ms: Date.UTC(2026, 9, 12),
    ends_at_ms: Date.UTC(2026, 9, 14),
    time_zone: "America/New_York",
  },
  event_speaker_id: "event-speaker-responsive",
  public_profile_url: "/events/event-responsive/speakers?speaker=event-speaker-responsive",
  events: [
    {
      id: "event-responsive",
      name: "AI Engineering Summit 2026",
      starts_at_ms: Date.UTC(2026, 9, 12),
      ends_at_ms: Date.UTC(2026, 9, 14),
      time_zone: "America/New_York",
    },
    {
      id: "event-second",
      name: "Applied AI Conference 2027",
      starts_at_ms: Date.UTC(2027, 2, 3),
      ends_at_ms: Date.UTC(2027, 2, 4),
      time_zone: "America/Chicago",
    },
  ],
  profile: {
    display_name: "Alex Speaker",
    job_title: "AI Engineer",
    company: "Example",
    location: "New York",
    biography: "Builds production AI systems.",
    links: [],
    version: 1,
  },
  tasks: [],
  completed_tasks: 0,
  total_tasks: 0,
  notifications: [{
    id: "notification-responsive",
    subject: "Slides are due Friday",
    delivered_at_ms: Date.UTC(2026, 9, 10),
  }],
  submissions: [{
    id: "submission-responsive",
    speaker_name: "Alex Speaker",
    speaker_email: "alex@example.test",
    proposal_title: "A deliberately long session title that must remain inside a narrow phone viewport",
    proposal_abstract: "A practical session about production systems.",
    answers: {
      speaker_name: "Alex Speaker",
      speaker_email: "alex@example.test",
      proposal_title: "A deliberately long session title that must remain inside a narrow phone viewport",
      proposal_abstract: "A practical session about production systems.",
    },
    status: "submitted",
    editable: true,
    form_slug: "engineering-summit",
    version: 1,
  }],
};

async function servePortal(page: Page) {
  await page.route("**/speaker", (route) => route.fulfill({ contentType: "text/html", body: portalHtml }));
  await page.route("**/api/v1/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ csrf_token: "responsive-csrf" }),
  }));
  await page.route("**/api/v1/speaker/portal", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(portal),
  }));
  await page.route("**/api/v1/speaker/portal?event_id=event-second", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      ...portal,
      event: portal.events[1],
      event_speaker_id: "event-speaker-second",
      tasks: [],
      submissions: [],
      notifications: [],
    }),
  }));
  await page.route("**/api/v1/speaker/resources", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/speaker/events/event-responsive/assets", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/speaker/events/event-second/assets", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/forms/**/submissions/**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...portal.submissions[0],
        proposal_title: "Updated proposal title",
        answers: {
          ...portal.submissions[0].answers,
          proposal_title: "Updated proposal title",
        },
        version: 2,
      }),
    });
  });
}

test.describe("speaker portal responsive design", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  for (const width of [320, 390]) {
    test(`speaker production desk fits a ${width}px phone`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await servePortal(page);
      await page.goto("/speaker");

      await expect(page.locator("#status")).toHaveText("Speaker details are ready.");
      await expect(page.getByRole("heading", { name: "Actions" })).toBeVisible();
      await expect(page.locator(".portal-hero__title")).toContainText("Welcome, Alex Speaker");
      await expect(page.getByRole("link", { name: "Public profile" })).toBeVisible();
      await expect(page.locator(".session-files")).toHaveCount(1);
      await expect(page.locator("#notification-list")).toContainText("Slides are due Friday");
      await expect(page.locator("#profile")).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "Speaker portal" })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      expect(await page.locator(".portal-grid").evaluate((element) =>
        getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(1);
    });
  }

  test("proposal editing stays inside the speaker portal", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await servePortal(page);
    await page.goto("/speaker");
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");

    await page.getByRole("button", { name: "Edit proposal" }).click();
    await expect(page.locator(".proposal-editor")).toBeVisible();
    await page.getByLabel("Proposal title").fill("Updated proposal title");
    await page.getByRole("button", { name: "Save changes" }).click();

    await expect(page.locator("#status")).toHaveText("Proposal changes saved.");
    await expect(page).toHaveURL(/\/speaker$/);
    await expect(page.getByRole("heading", { name: "Updated proposal title" })).toBeVisible();
  });

  test("speaker can switch between event workspaces", async ({ page }) => {
    await servePortal(page);
    await page.goto("/speaker");

    await page.getByLabel("Choose event").click();
    await expect(page.locator("#event-options button")).toHaveCount(2);
    await page.getByRole("menuitemradio", { name: /Applied AI Conference 2027/ }).click();

    await expect(page.locator("#event-summary")).toContainText("Applied AI Conference 2027");
    await expect(page.locator("#submission-list")).toContainText("No proposals");
    await expect(page.locator("#notification-list")).toContainText("No updates from this event yet.");
  });
});
