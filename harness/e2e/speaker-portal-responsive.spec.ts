import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

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
    category: "reminder",
    delivered_at_ms: Date.UTC(2026, 9, 10),
    body_text: "Please upload your slides. The original email is not required.",
    links: ["https://sessionbuddy.test/speaker", "javascript:alert(1)"],
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
    submitted_at_ms: Date.UTC(2026, 8, 1),
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
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ email: "alex@example.test", display_name: "Alex Speaker", csrf_token: "responsive-csrf" }),
  }));
  await page.route("**/api/v1/speaker/proposal-drafts", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
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
      const activeEvent = page.locator('.event-group[data-event-id="event-responsive"]');
      await expect(activeEvent.getByRole("heading", { name: "Your proposals" })).toBeVisible();
      await expect(activeEvent.getByRole("heading", { name: "Needs attention" })).toHaveCount(0);
      await expect(activeEvent.getByRole("heading", { name: "Updates" })).toBeVisible();
      await expect(activeEvent.getByRole("heading", { name: "Files" })).toHaveCount(0);
      await expect(activeEvent.getByRole("heading", { name: "Activity" })).toHaveCount(0);
      await expect(activeEvent.locator(".portal-data-table.is-empty")).toHaveCount(0);
      await expect(page.locator(".portal-hero__title")).toContainText("Welcome, Alex Speaker");
      await expect(page.getByRole("link", { name: "Public profile" })).toBeVisible();
      await expect(page.locator(".session-files")).toHaveCount(0);
      await expect(page.locator(".notification-list")).toContainText("Slides are due Friday");
      await expect(page.locator(".notification-category")).toHaveText("Reminder");
      await expect(page.locator(".notification-new")).toHaveText("New");
      await page.locator(".notification-list summary").click();
      await expect(page.locator(".notification-new")).toHaveCount(0);
      await expect(page.locator(".notification-list")).toContainText("The original email is not required.");
      await expect(page.locator('.notification-list a[href="https://sessionbuddy.test/speaker"]')).toHaveCount(1);
      await expect(page.locator('.notification-list a[href^="javascript:"]')).toHaveCount(0);
      await expect(page.locator("#profile")).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "Speaker portal" })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await expect(page.locator(".proposal-summary-row")).toHaveCount(1);
    });
  }

  test("delivered message recovery is accessible", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await servePortal(page);
    await page.goto("/speaker");
    await page.locator(".notification-list summary").click();

    await expect(page.locator(".notification-list time")).toHaveAttribute("title", /Event time \(America\/New_York\)/);
    const results = await new AxeBuilder({ page }).include("#submissions").analyze();
    expect(results.violations).toEqual([]);
  });

  test("proposal management links to the dedicated workspace", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await servePortal(page);
    await page.goto("/speaker");
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");

    const proposal = page.getByRole("link", { name: /A deliberately long session title/ });
    await expect(proposal).toHaveAttribute("href", /\/speaker\/proposals\/.+\/.+/);
    await expect(page.locator(".proposal-editor")).toHaveCount(0);
  });

  test("saved drafts remain visible alongside submitted proposals", async ({ page }) => {
    await servePortal(page);
    await page.route("**/api/v1/speaker/proposal-drafts", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{
        id: "draft-alongside-submission",
        form_id: "form-second",
        event_id: "event-second",
        event_name: "Applied AI Conference 2027",
        form_slug: "applied-ai-2027",
        proposal_title: "A second proposal in progress",
        updated_at_ms: Date.UTC(2026, 7, 17, 11, 0),
        edit_path: "/cfp/events/applied-ai-2027",
      }] }),
    }));
    await page.goto("/speaker");

    await expect(page.locator("#portal")).toBeVisible();
    await expect(page.getByRole("link", { name: /A deliberately long session title/ })).toBeVisible();
    await expect(page.getByRole("link", {
      name: "Continue editing A second proposal in progress for Applied AI Conference 2027",
    })).toBeVisible();
  });

  test("the empty workspace shows saved drafts without advertising an open-calls directory", async ({ page }) => {
    await servePortal(page);
    await page.route("**/api/v1/speaker/proposal-drafts", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{
        id: "draft-responsive",
        form_id: "form-responsive",
        event_id: "event-responsive",
        event_name: "AI Engineering Summit 2026",
        form_slug: "engineering-summit",
        proposal_title: "A saved proposal draft",
        updated_at_ms: Date.UTC(2026, 7, 17, 10, 30),
        edit_path: "/cfp/eventr/engineering-summit",
      }] }),
    }));
    await page.route("**/api/v1/speaker/portal", (route) => route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "not_found", message: "No speaker workspace yet." } }),
    }));
    await page.goto("/speaker");

    await expect(page.locator("#empty-state")).toBeVisible();
    await expect(page.locator("#empty-state")).toContainText("Event organizers share each call for proposals directly");
    const draft = page.getByRole("link", { name: "Continue editing A saved proposal draft for AI Engineering Summit 2026" });
    await expect(draft).toBeVisible();
    await expect(draft).toHaveAttribute("href", "/cfp/eventr/engineering-summit");
    await expect(page.locator("#status")).toHaveText("1 saved proposal draft.");
    await expect(page.getByRole("link", { name: "Explore open calls" })).toHaveCount(0);
  });

  test("every event the speaker belongs to is grouped on one page", async ({ page }) => {
    await servePortal(page);
    await page.goto("/speaker");

    await expect(page.locator(".event-group")).toHaveCount(2);
    await expect(page.locator("#portal-summary")).toHaveText("Manage sessions, tasks, and resources across 2 events.");
    await expect(page.getByRole("heading", { name: "AI Engineering Summit 2026" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Applied AI Conference 2027" })).toBeVisible();

    const second = page.locator('.event-group[data-event-id="event-second"]');
    await expect(second.locator(".event-group__empty")).toHaveText("No proposals or actions for this event.");
    await expect(second.locator(".submission-list")).toHaveCount(0);
    await expect(second.locator(".notification-list")).toHaveCount(0);
    await expect(page.locator("#summary-total")).toHaveText("1");
  });

  test("a failed safety check retries the same upload intent", async ({ page }) => {
    await servePortal(page);
    await page.route("**/api/v1/speaker/portal*", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...portal,
        events: [portal.event],
        tasks: [{
          id: "task-supporting-document",
          event_id: "event-responsive",
          task_type: "supporting_document",
          title: "Upload moderator briefing",
          help_text: "Share the latest PDF.",
          state: "open",
          version: 1,
          due_at_ms: null,
          form_fields: [],
        }],
      }),
    }));
    let authorizations = 0;
    let authorizationPayload: Record<string, unknown> | null = null;
    let uploads = 0;
    let completions = 0;
    await page.route("**/api/v1/speaker/events/event-responsive/upload-authorizations", async (route) => {
      authorizations += 1;
      authorizationPayload = route.request().postDataJSON();
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
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");
    const uploadForm = page.locator('form[data-kind="supporting_document"]');
    await uploadForm.locator('input[type="file"]').setInputFiles({
      name: "briefing.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("fixture PDF"),
    });
    await uploadForm.getByRole("button", { name: "Upload document" }).click();
    const uploadStatus = uploadForm.locator(".upload-status");
    await expect.poll(() => completions).toBe(1);
    await expect(uploadStatus).toHaveText(
      "File received. Safety checks are temporarily unavailable, so this file is not public or current yet. "
      + "Press “Upload document” again to retry. You do not need to choose or upload the file again.",
    );
    await expect(uploadStatus).not.toContainText("Something went wrong");

    await uploadForm.getByRole("button", { name: "Upload document" }).click();
    await expect.poll(() => completions).toBe(2);
    await expect(uploadForm.locator('input[type="file"]')).toHaveValue("");
    expect(authorizationPayload).toMatchObject({ version_comment: "" });
    expect({ authorizations, uploads, completions }).toEqual({ authorizations: 1, uploads: 1, completions: 2 });
  });

  test("an authorization failure does not claim the file was received", async ({ page }) => {
    await servePortal(page);
    let authorizations = 0;
    await page.route("**/api/v1/speaker/portal*", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...portal,
        events: [portal.event],
        tasks: [{
          id: "task-supporting-document",
          event_id: "event-responsive",
          task_type: "supporting_document",
          title: "Upload moderator briefing",
          help_text: "Share the latest PDF.",
          state: "open",
          version: 1,
          due_at_ms: null,
          form_fields: [],
        }],
      }),
    }));
    await page.route("**/api/v1/speaker/events/event-responsive/upload-authorizations", async (route) => {
      authorizations += 1;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "service_unavailable", message: "Upload authorization is temporarily unavailable." },
        }),
      });
    });

    await page.goto("https://sessionbuddy.test/speaker");
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");
    const uploadForm = page.locator('form[data-kind="supporting_document"]');
    await uploadForm.locator('input[type="file"]').setInputFiles({
      name: "briefing.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("fixture PDF"),
    });
    await uploadForm.getByLabel(/Upload note/).fill("Moderator briefing.");
    await uploadForm.getByRole("button", { name: "Upload document" }).click();

    await expect.poll(() => authorizations).toBe(1);
    await expect(uploadForm.locator(".upload-status")).toHaveText("Something went wrong on our side. Try again.");
    await expect(uploadForm.locator(".upload-status")).not.toContainText("File received");
  });

  test("profile and headshot tasks lead to their supported completion flows", async ({ page }) => {
    await servePortal(page);
    const taskPortal = {
      ...portal,
      tasks: [
        { id: "task-profile", task_type: "profile", title: "Confirm profile", help_text: "", state: "open", version: 1, due_at_ms: null, form_fields: [] },
        { id: "task-headshot", task_type: "headshot", title: "Upload headshot", help_text: "", state: "open", version: 1, due_at_ms: null, form_fields: [] },
      ],
    };
    await page.route("**/api/v1/speaker/portal*", (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify(taskPortal),
    }));
    await page.goto("/speaker");
    await expect(page.getByRole("link", { name: "Edit profile" })).toHaveAttribute("href", "/account");
    await expect(page.getByRole("button", { name: "Upload headshot" })).toBeVisible();
    await expect(page.locator("#speaker-profile-tools")).toHaveCount(0);
  });
});
