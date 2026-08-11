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
    if (route.request().url().endsWith("/withdraw")) {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers()["x-csrf-token"]).toBe("responsive-csrf");
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ ...portal.submissions[0], status: "withdrawn", editable: false, version: 2 }),
      });
      return;
    }
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
      await expect(page.getByRole("heading", { name: "Needs attention" })).toBeVisible();
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

  test("primary speaker can withdraw an unreviewed proposal", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await servePortal(page);
    await page.goto("/speaker");
    page.once("dialog", (dialog) => dialog.accept());

    await page.getByRole("button", { name: "Withdraw proposal" }).click();

    await expect(page.locator("#status")).toHaveText("Proposal withdrawn. It is now read-only.");
    await expect(page.locator("#submission-list")).toContainText("Withdrawn");
    await expect(page.locator("#submission-list")).toContainText("withdrawn and read-only");
    await expect(page.getByRole("button", { name: "Edit proposal" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Withdraw proposal" })).toHaveCount(0);
    await expect(page.locator(".session-upload-grid")).toHaveCount(0);
  });

  test("speaker can switch between event workspaces", async ({ page }) => {
    await servePortal(page);
    await page.goto("/speaker");

    await expect(page.locator(".event-index__item")).toHaveCount(2);
    await page.getByRole("button", { name: /Applied AI Conference 2027/ }).click();

    await expect(page.locator("#event-summary")).toContainText("Applied AI Conference 2027");
    await expect(page.locator("#submission-list")).toContainText("No proposals");
    await expect(page.locator("#notification-list")).toContainText("No updates from this event yet.");
  });

  test("a failed safety check retries the same upload intent", async ({ page }) => {
    await servePortal(page);
    let authorizations = 0;
    let uploads = 0;
    let completions = 0;
    await page.route("**/api/v1/speaker/events/event-responsive/upload-authorizations", async (route) => {
      authorizations += 1;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          intent_id: "intent-retry",
          upload_url: "/api/v1/uploads/intent-retry/content?token=local-test",
          method: "PUT",
          headers: { "content-type": "application/pdf" },
          expires_at_ms: Date.now() + 60_000,
        }),
      });
    });
    await page.route("**/api/v1/uploads/intent-retry/content?token=local-test", async (route) => {
      uploads += 1;
      await route.fulfill({ status: 204 });
    });
    await page.route("**/api/v1/speaker/events/event-responsive/upload-intents/intent-retry/complete", async (route) => {
      completions += 1;
      if (completions === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            error: { code: "service_unavailable", message: "Safety checks are temporarily unavailable." },
          }),
        });
        return;
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ intent_id: "intent-retry", state: "clean" }) });
    });

    await page.goto("https://sessionbuddy.test/speaker");
    await page.locator(".session-files summary").click();
    const uploadForm = page.locator('form[data-kind="supporting_document"]');
    await uploadForm.locator('input[type="file"]').setInputFiles({
      name: "briefing.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("fixture PDF"),
    });
    await uploadForm.getByLabel(/What changed/).fill("Updated moderator briefing.");
    await uploadForm.getByRole("button", { name: "Upload document" }).click();
    const uploadStatus = uploadForm.locator(".upload-status");
    await expect(uploadStatus).toHaveText(
      "File received. Safety checks are temporarily unavailable, so this file is not public or current yet. "
      + "Press “Upload document” again to retry. You do not need to choose or upload the file again.",
    );
    await expect(uploadStatus).not.toContainText("Something went wrong");

    await uploadForm.getByRole("button", { name: "Upload document" }).click();
    await expect.poll(() => completions).toBe(2);
    await expect(uploadForm.locator('input[type="file"]')).toHaveValue("");
    await expect(uploadForm.getByLabel(/What changed/)).toHaveValue("");
    expect({ authorizations, uploads, completions }).toEqual({ authorizations: 1, uploads: 1, completions: 2 });
  });

  test("an authorization failure does not claim the file was received", async ({ page }) => {
    await servePortal(page);
    await page.route("**/api/v1/speaker/events/event-responsive/upload-authorizations", async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "service_unavailable", message: "Upload authorization is temporarily unavailable." },
        }),
      });
    });

    await page.goto("https://sessionbuddy.test/speaker");
    await page.locator(".session-files summary").click();
    const uploadForm = page.locator('form[data-kind="supporting_document"]');
    await uploadForm.locator('input[type="file"]').setInputFiles({
      name: "briefing.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("fixture PDF"),
    });
    await uploadForm.getByLabel(/What changed/).fill("Updated moderator briefing.");
    await uploadForm.getByRole("button", { name: "Upload document" }).click();

    await expect(uploadForm.locator(".upload-status")).toHaveText("Something went wrong on our side. Try again.");
    await expect(uploadForm.locator(".upload-status")).not.toContainText("File received");
  });
});
