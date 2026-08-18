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

const headshotRules = {
  enabled: true,
  allowed_content_types: ["image/jpeg", "image/png", "image/webp"],
  max_file_bytes: 5 * 1024 * 1024,
};
const supportingDocumentRules = {
  enabled: true,
  allowed_content_types: ["application/pdf"],
  max_file_bytes: 20 * 1024 * 1024,
};

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
  asset_upload_rules: {
    headshot: headshotRules,
    supporting_document: supportingDocumentRules,
  },
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
    // Sep 1 in the browser's UTC clock is still Aug 31 in the event's New York zone.
    submitted_at_ms: Date.UTC(2026, 8, 1, 2),
    editable: true,
    form_slug: "engineering-summit",
    version: 1,
  }],
};

async function servePortal(page: Page, portalResponse = portal) {
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
    body: JSON.stringify(portalResponse),
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
  test.use({ timezoneId: "UTC", locale: "en-US" });

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
      await expect(page.locator(".proposal-summary-row .state-badge")).toHaveText("Proposal: Submitted");
      await expect(page.locator(".summary-tile__label", { hasText: /^Awaiting decision$/ })).toHaveCount(1);
      const proposalMetadata = page.locator(".proposal-summary-row__identity small");
      await expect(proposalMetadata).toContainText(
        "Submitted Aug 31, 2026, 10:00 PM · Event time (America/New_York)",
      );
      expect(
        await page.locator(".proposal-summary-row__link").evaluate(
          (row) => row.scrollWidth <= row.clientWidth,
        ),
      ).toBe(true);
    });
  }

  test("delivered message recovery is accessible", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await servePortal(page);
    await page.goto("/speaker");
    await page.locator(".notification-list summary").click();

    await expect(page.locator(".proposal-summary-row__identity small")).toContainText(
      "Submitted Aug 31, 2026, 10:00 PM · Event time (America/New_York)",
    );
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

  test("additional participants see their role without a dead owner-only link", async ({ page }) => {
    const coAuthorPortal = {
      ...portal,
      submissions: [{
        ...portal.submissions[0],
        status: "accepted",
        editable: false,
        is_primary_submitter: false,
        participant_role: "co_author",
      }],
    };
    await servePortal(page, coAuthorPortal);
    await page.goto("/speaker");

    const proposal = page.locator(".proposal-summary-row");
    await expect(proposal).toContainText("Your role: Co-author");
    await expect(proposal.getByRole("link", { name: /A deliberately long session title/ })).toHaveCount(0);
    await expect(proposal.locator(".proposal-summary-row__arrow")).toHaveCount(0);
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
    await expect(page.locator("#saved-proposal-draft-list time")).toHaveText(
      "Saved Aug 17, 2026, 11:00 AM · Your local time",
    );
  });

  test("the empty workspace keeps saved drafts alongside open-call discovery", async ({ page }) => {
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
    await expect(page.locator("#empty-state")).toContainText(
      "Browse calls below to find one that is open or opening soon.",
    );
    const draft = page.getByRole("link", { name: "Continue editing A saved proposal draft for AI Engineering Summit 2026" });
    await expect(draft).toBeVisible();
    await expect(draft).toHaveAttribute("href", "/cfp/eventr/engineering-summit");
    await expect(page.locator("#status")).toHaveText("1 saved proposal draft.");
  });

  test("every event the speaker belongs to is grouped on one page", async ({ page }) => {
    await servePortal(page);
    await page.goto("/speaker");

    await expect(page.locator(".event-group")).toHaveCount(2);
    await expect(page.locator("#portal-summary")).toHaveText("Sessions, tasks, and resources across 2 events.");
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
          upload_rules: supportingDocumentRules,
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
          upload_rules: supportingDocumentRules,
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
        { id: "task-headshot", task_type: "headshot", title: "Upload headshot", help_text: "", state: "open", version: 1, due_at_ms: null, form_fields: [], upload_rules: headshotRules },
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

  test("a malformed upload task explains the problem without rendering a dead control", async ({ page }) => {
    await servePortal(page);
    await page.route("**/api/v1/speaker/portal*", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...portal,
        events: [portal.event],
        tasks: [{
          id: "task-headshot-malformed",
          event_id: "event-responsive",
          task_type: "headshot",
          title: "Upload headshot",
          help_text: "",
          state: "open",
          version: 1,
          due_at_ms: null,
          form_fields: [],
          upload_rules: {},
        }],
      }),
    }));

    await page.goto("/speaker");
    await expect(page.getByText(
      "This upload request isn’t configured. Ask an organizer to update it.",
    )).toBeVisible();
    await expect(page.getByRole("button", { name: "Upload headshot" })).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
  });

  const customTask = { id: "task-custom", task_type: "custom", title: "Confirm travel details", help_text: "", state: "open", version: 1, due_at_ms: null, form_fields: [] };
  const secondEventPortal = { ...portal, event: portal.events[1], event_speaker_id: "event-speaker-second", tasks: [], submissions: [], notifications: [] };
  const isSecondEvent = (url: string) => url.includes("event_id=event-second");
  const attention = (page: Page) => page.locator('[data-event-id="event-responsive"]').getByText("Needs attention");

  test("Mark complete reflects the committed completion before the portal refresh finishes", async ({ page }) => {
    await servePortal(page);
    let portalReads = 0;
    let releaseRefresh: () => void = () => {};
    const refreshReleased = new Promise<void>((resolve) => { releaseRefresh = resolve; });
    await page.route("**/api/v1/speaker/portal*", async (route) => {
      if (isSecondEvent(route.request().url())) { await route.fulfill({ contentType: "application/json", body: JSON.stringify(secondEventPortal) }); return; }
      portalReads += 1;
      if (portalReads > 1) await refreshReleased;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ ...portal, tasks: portalReads > 1 ? [{ ...customTask, state: "completed", version: 2 }] : [customTask] }),
      });
    });
    await page.route("**/api/v1/speaker/tasks/task-custom/response", async (route) => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers()["x-csrf-token"]).toBe("responsive-csrf");
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ id: "task-custom", response: {}, version: 2 }),
      });
    });
    await page.goto("/speaker");
    const markComplete = page.getByRole("button", { name: "Mark complete" });
    await expect(markComplete).toBeEnabled();
    await markComplete.click();

    // The POST succeeded; the follow-up portal read is still stalled.
    await expect(attention(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Mark complete" })).toHaveCount(0);
    await expect(page.locator("button:disabled", { hasText: "Mark complete" })).toHaveCount(0);
    await expect(page.locator("#status")).toHaveText("Task completed.");
    await expect.poll(() => portalReads).toBe(2);

    releaseRefresh();
    await expect.poll(() => portalReads).toBe(2);
    await expect(attention(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Mark complete" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Task history" })).toBeVisible();
    await expect(page.getByText("Completed (1)")).toBeVisible();
    await expect(page.locator(".task-complete")).toContainText("Confirm travel details");
  });

  test("an all-complete event keeps its task history reachable", async ({ page }) => {
    await servePortal(page, {
      ...portal,
      tasks: [{ ...customTask, state: "completed", completed_at_ms: Date.UTC(2026, 8, 2) }],
      notifications: [],
    });
    await page.goto("/speaker");
    const event = page.locator('[data-event-id="event-responsive"]');
    await expect(event.getByRole("heading", { name: "Needs attention" })).toHaveCount(0);
    await expect(event.getByRole("heading", { name: "Task history" })).toBeVisible();
    await expect(event.getByText("Completed (1)")).toBeVisible();
    await expect(event.locator(".task-complete")).toContainText("Confirm travel details");
  });

  test("completing a task in a non-active event invalidates that event's onboarding channel", async ({ page }) => {
    await servePortal(page);
    let secondReads = 0;
    let releaseRefresh: () => void = () => {};
    const refreshReleased = new Promise<void>((resolve) => { releaseRefresh = resolve; });
    const secondTask = { ...customTask, id: "task-second", title: "Confirm hotel details" };
    await page.route("**/api/v1/speaker/portal*", async (route) => {
      if (!isSecondEvent(route.request().url())) {
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...portal, tasks: [] }) });
        return;
      }
      secondReads += 1;
      // The reconciliation read for the second event stalls, so state.portal keeps
      // describing the active event while the broadcast must already have gone out.
      if (secondReads > 1) await refreshReleased;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(secondReads > 1
          ? { ...secondEventPortal, profile: { ...portal.profile, display_name: "Alex Reconciled" }, tasks: [{ ...secondTask, state: "completed", version: 2 }] }
          : { ...secondEventPortal, tasks: [secondTask] }),
      });
    });
    await page.route("**/api/v1/speaker/tasks/task-second/response", async (route) => {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "task-second", response: {}, version: 2 }) });
    });
    await page.addInitScript(() => {
      const received: Record<string, number> = {};
      (window as unknown as { __onboardingInvalidations: Record<string, number> }).__onboardingInvalidations = received;
      for (const eventId of ["event-responsive", "event-second"]) {
        const channel = new BroadcastChannel(`sessionbuddy:onboarding:${eventId}`);
        channel.addEventListener("message", (message) => {
          if (message.data?.type === "snapshot-invalidated") received[eventId] = (received[eventId] || 0) + 1;
        });
      }
    });
    await page.goto("/speaker");

    const second = page.locator('.event-group[data-event-id="event-second"]');
    const markComplete = second.getByRole("button", { name: "Mark complete" });
    await expect(markComplete).toBeEnabled();
    await markComplete.click();
    await expect(page.locator("#status")).toHaveText("Task completed.");
    await expect.poll(() => secondReads).toBe(2);

    // Refresh is still stalled: the completed task's own event must have been notified,
    // and the still-active first event must not have been.
    const invalidations = () => page.evaluate(() => (window as unknown as { __onboardingInvalidations: Record<string, number> }).__onboardingInvalidations);
    await expect.poll(async () => (await invalidations())["event-second"] || 0).toBeGreaterThanOrEqual(1);
    expect((await invalidations())["event-responsive"] || 0).toBe(0);

    releaseRefresh();
    // The reconciled payload carries a distinctive name, proving the refresh rendered
    // before the broadcast counts are checked; the button was already gone optimistically.
    await expect(page.locator("#welcome-name")).toHaveText("Alex Reconciled");
    await expect(second.getByRole("button", { name: "Mark complete" })).toHaveCount(0);
    // Reconciliation is a read, not a mutation: it must not broadcast again or cross channels.
    expect((await invalidations())["event-second"] || 0).toBe(1);
    expect((await invalidations())["event-responsive"] || 0).toBe(0);
  });

  test("a failed portal refresh after completion keeps the task completed with a warning", async ({ page }) => {
    await servePortal(page);
    let portalReads = 0;
    await page.route("**/api/v1/speaker/portal*", async (route) => {
      if (isSecondEvent(route.request().url())) { await route.fulfill({ contentType: "application/json", body: JSON.stringify(secondEventPortal) }); return; }
      portalReads += 1;
      if (portalReads > 1) { await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "unavailable" }) }); return; }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...portal, tasks: [customTask] }) });
    });
    await page.route("**/api/v1/speaker/tasks/task-custom/response", (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify({ id: "task-custom", response: {}, version: 2 }),
    }));
    await page.goto("/speaker");
    await page.getByRole("button", { name: "Mark complete" }).click();
    await expect.poll(() => portalReads).toBe(2);
    await expect(attention(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Mark complete" })).toHaveCount(0);
    await expect(page.locator("#status")).toContainText("Task completed.");
    await expect(page.locator("#status")).toContainText("could not refresh");
  });

  test("a failed completion POST leaves Mark complete enabled for retry", async ({ page }) => {
    await servePortal(page);
    await page.route("**/api/v1/speaker/portal*", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(isSecondEvent(route.request().url()) ? secondEventPortal : { ...portal, tasks: [customTask] }),
    }));
    await page.route("**/api/v1/speaker/tasks/task-custom/response", (route) => route.fulfill({
      status: 500, contentType: "application/json", body: JSON.stringify({ detail: "boom" }),
    }));
    await page.goto("/speaker");
    const markComplete = page.getByRole("button", { name: "Mark complete" });
    await markComplete.click();
    await expect(markComplete).toBeEnabled();
    await expect(attention(page)).toHaveCount(1);
    await expect(page.locator("#status")).toHaveClass(/error/);
  });
});
