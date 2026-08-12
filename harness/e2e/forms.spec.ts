import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const userId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const programId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const assignmentId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const responsiveBuilderEventId = "c1f89ba5-5013-4419-af7d-fd622af9803f";

const organizerSession = {
  authenticated: true,
  user_id: userId,
  email: "admin@example.com",
  display_name: "Admin User",
  profile_complete: true,
  csrf_token: "browser-test-csrf",
  account_roles: ["organizer"],
  active_role: "organizer",
  default_role: "organizer",
  organization_id: organizationId,
  event_id: eventId,
  organization_access: [{ organization_id: organizationId, organization_name: "Example Events", permissions: ["owner"] }],
  event_access: [{ organization_id: organizationId, event_id: eventId, event_name: "Conference 2030", permissions: ["owner"], assignments: [] }],
};

async function polyfillUuid(page: Page) {
  await page.addInitScript(() => {
    if (!crypto.randomUUID) {
      Object.defineProperty(crypto, "randomUUID", {
        configurable: true,
        value: () => "ffffffff-ffff-4fff-8fff-ffffffffffff",
      });
    }
    if (!crypto.subtle) {
      Object.defineProperty(crypto, "subtle", {
        configurable: true,
        value: { digest: async () => new Uint8Array(32).buffer },
      });
    }
  });
}

async function mockSession(page: Page, session = organizerSession) {
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
  await page.route("**/api/v1/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
}

test.describe("form validation and workflow wiring", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("organization and account forms validate before writing", async ({ page }) => {
    await mockSession(page);
    let organizationName = "Example Events";
    let organizationWrites = 0;
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{ id: organizationId, name: organizationName, status: "active", version: 1 }] }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}`, async (route) => {
      organizationWrites += 1;
      organizationName = route.request().postDataJSON().name;
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: organizationId, name: organizationName, status: "active", version: 2 }) });
    });
    let profileWrites = 0;
    let profile = { email: "admin@example.com", display_name: "Admin User", first_name: "Admin", last_name: "User", job_title: null, company: null, time_zone: "UTC", description: null, website_url: null, linkedin_url: null, x_url: null, version: 1 };
    await page.route("**/api/v1/account/profile", async (route) => {
      if (route.request().method() === "PATCH") {
        profileWrites += 1;
        profile = { ...profile, ...route.request().postDataJSON(), version: 2 };
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(profile) });
    });
    await page.goto("/admin/organization");
    const organizationForm = page.locator("#organization-settings form").first();
    await organizationForm.getByLabel("Organization name").fill("");
    await organizationForm.getByRole("button", { name: "Save organization" }).click();
    expect(organizationWrites).toBe(0);
    await organizationForm.getByLabel("Organization name").fill("Updated Events");
    await organizationForm.getByRole("button", { name: "Save organization" }).click();
    await expect.poll(() => organizationWrites).toBe(1);

    await page.goto("/account");
    await page.getByLabel(/Time zone/).fill("Mars/Phobos");
    await page.getByRole("button", { name: "Save profile" }).click();
    expect(profileWrites).toBe(0);
    await page.getByLabel(/Time zone/).fill("Asia/Kolkata");
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect.poll(() => profileWrites).toBe(1);
    await expect(page.locator("#status")).toHaveText("Profile saved.");
  });

  test("CFP builder rejects invalid availability and publishes a valid form", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        organization_id: organizationId,
        event_id: eventId,
        event_name: "Conference 2030",
        event_starts_at_ms: 1_901_000_000_000,
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
    let publishWrites = 0;
    let publishedBody: Record<string, unknown> | null = null;
    await page.route(`**/api/v1/admin/events/${eventId}/cfp/publish`, async (route) => {
      publishWrites += 1;
      publishedBody = route.request().postDataJSON();
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ slug: "conference-2030" }) });
    });

    await page.goto(`/admin/events/${eventId}/cfp`);
    const form = page.locator("#publish-form");
    await form.locator("#cfp-description-editor").fill("Share your best conference proposal.");
    await page.getByRole("button", { name: "Availability" }).click();
    await form.getByLabel(/Opens/).fill("2030-03-20T10:00");
    await form.getByLabel(/Closes/).fill("2030-03-20T09:00");
    await form.getByRole("button", { name: "Publish CFP" }).click();
    expect(publishWrites).toBe(0);
    await form.getByLabel(/Closes/).fill("2030-03-21T09:00");
    await form.getByRole("button", { name: "Publish CFP" }).click();
    await expect(page.locator("#status")).toHaveText("Your CFP was published successfully.");
    expect(publishWrites).toBe(1);
    expect(publishedBody).toMatchObject({ slug: "conference-2030" });
  });

  test("CFP builder keeps every section usable at narrow mobile widths", async ({ page }) => {
    await polyfillUuid(page);
    const responsiveSession = {
      ...organizerSession,
      event_id: responsiveBuilderEventId,
      event_access: [{
        organization_id: organizationId,
        event_id: responsiveBuilderEventId,
        event_name: "Responsive Builder Conference",
        permissions: ["owner"],
        assignments: [],
      }],
    };
    await mockSession(page, responsiveSession);
    await page.route(`**/api/v1/admin/events/${responsiveBuilderEventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: responsiveBuilderEventId,
        organization_id: organizationId,
        name: "Responsive Builder Conference",
        status: "active",
        version: 1,
        time_zone: "UTC",
        delivery_mode: "virtual",
        starts_at_ms: Date.UTC(2030, 5, 1, 9),
        ends_at_ms: Date.UTC(2030, 5, 1, 18),
      }),
    }));
    await page.route(`**/api/v1/admin/events/${responsiveBuilderEventId}/agenda/tracks`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    }));
    await page.route(`**/api/v1/admin/events/${responsiveBuilderEventId}/cfp`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        organization_id: organizationId,
        event_id: responsiveBuilderEventId,
        event_name: "Responsive Builder Conference",
        event_starts_at_ms: Date.UTC(2030, 5, 1, 9),
        published_form: {
          id: programId,
          event_id: responsiveBuilderEventId,
          version: 1,
          slug: "responsive-builder-conference",
          welcome_text: "Share a practical proposal.",
          description_html: "<p>Share a practical proposal.</p>",
          important_dates: [],
          fields: [
            { key: "speaker_name", label: "Speaker name", type: "text", required: true, choices: [] },
            { key: "speaker_email", label: "Email", type: "email", required: true, choices: [] },
            { key: "proposal_title", label: "Proposal title", type: "text", required: true, choices: [] },
            { key: "proposal_abstract", label: "Proposal abstract", type: "textarea", required: true, choices: [] },
          ],
          conditions: [],
          routing_rules: [],
          opens_at_ms: null,
          closes_at_ms: null,
          submission_limit: null,
          co_speaker_limit: 1,
          success_title: "Proposal received",
          success_message: "We received your proposal.",
          confirmation_subject: "We received your proposal",
          confirmation_body: "Thank you for submitting.",
          redirect_to_portal: true,
        },
      }),
    }));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/admin/events/${responsiveBuilderEventId}/cfp`);
    await page.getByRole("button", { name: /^Custom questions/ }).click();
    await page.getByRole("button", { name: /Add custom question/ }).click();
    await expect(page.locator('.question-card:not([hidden]) input[name="field_label"]')).toBeVisible();
    const selectedCustomQuestions = page.getByRole("button", { name: /^Custom questions/ });
    await expect(selectedCustomQuestions).toHaveAttribute("aria-pressed", "true");
    expect(await selectedCustomQuestions.evaluate((element) => {
      const item = element.getBoundingClientRect();
      const nav = element.closest("nav")!.getBoundingClientRect();
      return item.left >= nav.left - 1 && item.right <= nav.right + 1;
    })).toBe(true);

    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await expect(page.locator("#publish-form")).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      const liveUrlLayout = await page.locator("#cfp-live-bar").evaluate((element) => {
        const address = element.querySelector(".cfp-live-bar__address")!.getBoundingClientRect();
        const actions = element.querySelector(".cfp-live-bar__actions")!.getBoundingClientRect();
        const bar = element.getBoundingClientRect();
        return {
          fits: bar.left >= 0 && bar.right <= window.innerWidth,
          actionsBelowAddress: actions.top >= address.bottom - 1,
        };
      });
      expect(liveUrlLayout, `live URL should stack cleanly at ${width}px`).toEqual({ fits: true, actionsBelowAddress: true });

      for (const name of ["Description", "Proposal details", "Custom questions", "Co-speakers", "Confirmation", "Availability"]) {
        const section = page.getByRole("button", { name: new RegExp(`^${name}`) });
        await section.click();
        await expect(section).toHaveAttribute("aria-pressed", "true");
        const alignment = await section.evaluate((element) => {
          const item = element.getBoundingClientRect();
          const nav = element.closest("nav")!.getBoundingClientRect();
          return { left: item.left >= nav.left - 1, right: item.right <= nav.right + 1 };
        });
        expect(alignment, `${name} should be fully visible at ${width}px`).toEqual({ left: true, right: true });
      }

      await page.getByRole("button", { name: /^Custom questions/ }).click();
      const customQuestion = page.locator(".question-card:not(.question-card--system)").last();
      await expect(customQuestion).toBeVisible();
      const customQuestionFits = await customQuestion.evaluate((element) => {
        const card = element.getBoundingClientRect();
        const editor = element.closest("#publish-form")!.getBoundingClientRect();
        return card.left >= editor.left - 1 && card.right <= editor.right + 1;
      });
      expect(customQuestionFits, `custom question should fit at ${width}px`).toBe(true);
      await expect(customQuestion.locator('input[name="field_key"]')).toBeHidden();
      await expect(customQuestion.locator('input[name="field_label"]')).toBeVisible();
      await customQuestion.getByLabel("Answer format").selectOption("text");
      await expect(customQuestion.getByLabel("Answer choices")).toBeHidden();
      await customQuestion.getByLabel("Answer format").selectOption("select");
      await expect(customQuestion.getByLabel("Answer choices")).toBeVisible();
    }

    await expect(page.locator("#publish-result")).toBeVisible();
    await expect(page.getByRole("button", { name: /^Availability/ })).toHaveCSS("border-left-width", "0px");
    const actionButtons = page.locator(".cfp-editor-actions button").filter({ visible: true });
    for (let index = 0; index < await actionButtons.count(); index += 1) {
      const height = await actionButtons.nth(index).evaluate((element) => element.getBoundingClientRect().height);
      expect(height).toBeGreaterThanOrEqual(40);
    }
  });

  test("review round and evaluator forms block incomplete payloads", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, time_zone: "UTC" }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ published_form: null }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/submissions`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        organization_id: organizationId,
        event_id: eventId,
        data: [{
          id: assignmentId,
          speaker_name: "Speaker",
          speaker_email: "speaker@example.com",
          proposal_title: "A proposal",
          proposal_abstract: "Abstract",
          status: "submitted",
          submitted_at_ms: 1_900_000_000_000,
          routed_category: null,
          routed_track: "Platform",
          routed_review_queue: "Technical",
          answers: { audience_level: "Intermediate" },
        }, {
          id: "33333333-3333-4333-8333-333333333333",
          speaker_name: "Round speaker",
          speaker_email: "round@example.com",
          proposal_title: "A proposal already in review",
          proposal_abstract: "Review is underway",
          status: "submitted",
          submitted_at_ms: 1_900_000_000_000,
          routed_category: null,
          routed_track: null,
          routed_review_queue: null,
          evaluation_round_id: "11111111-1111-4111-8111-111111111111",
          evaluation_round_name: "Initial review",
          answers: {},
        }, {
          id: "22222222-2222-4222-8222-222222222222",
          speaker_name: "Decided speaker",
          speaker_email: "decided@example.com",
          proposal_title: "An accepted proposal",
          proposal_abstract: "Decision completed",
          status: "accepted",
          submitted_at_ms: 1_900_000_000_000,
          routed_category: null,
          routed_track: null,
          routed_review_queue: null,
          answers: {},
        }],
      }),
    }));
    const reviewerLookups: string[] = [];
    await page.route(`**/api/v1/admin/events/${eventId}/evaluators?email=*`, (route) => {
      reviewerLookups.push(route.request().url());
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ user_id: userId, display_name: "Reviewer" }] }) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/evaluation-rounds/current`, (route) => route.fulfill({ contentType: "application/json", body: "null" }));
    let directRejection: Record<string, unknown> | null = null;
    await page.route(`**/api/v1/admin/events/${eventId}/submissions/${assignmentId}/reject`, async (route) => {
      directRejection = route.request().postDataJSON();
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ communication_queued: true }),
      });
    });
    let roundWrites = 0;
    await page.route(`**/api/v1/admin/events/${eventId}/evaluation-rounds`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) });
        return;
      }
      roundWrites += 1;
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: "11111111-1111-4111-8111-111111111111", name: "Initial review", assignment_count: 1, evaluator_count: 1 }) });
    });

    await page.goto(`/admin/events/${eventId}/submissions`);
    await expect(page.getByText(`Receipt ${assignmentId.slice(0, 8)}`, { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "View proposal" }).nth(1).click();
    const reviewedDetail = page.getByRole("dialog", { name: "Proposal details" });
    await expect(reviewedDetail.getByText("Evaluation round", { exact: true })).toBeVisible();
    await expect(reviewedDetail.getByText("Initial review", { exact: true })).toBeVisible();
    await expect(reviewedDetail.getByRole("button", { name: "Reject without review" })).toHaveCount(0);
    await expect(reviewedDetail.getByRole("link", { name: "Open Initial review to decide" })).toHaveAttribute(
      "href",
      "/admin/evaluation-rounds/11111111-1111-4111-8111-111111111111",
    );
    await reviewedDetail.getByRole("button", { name: "Close" }).click();
    await page.getByRole("button", { name: "View proposal" }).first().click();
    const detail = page.getByRole("dialog", { name: "Proposal details" });
    await expect(detail).toContainText("speaker@example.com");
    await expect(detail).toContainText("Platform");
    await expect(detail).toContainText("Audience level");
    await expect(detail).toContainText("Intermediate");
    await detail.getByRole("button", { name: "Reject without review" }).click();
    await expect(detail.getByLabel("Email Speaker")).toBeChecked();
    await detail.getByLabel("Internal reason").fill("Outside the program scope");
    await detail.getByLabel("Speaker message").fill("Thank you for submitting to our event.");
    await detail.getByRole("button", { name: "Confirm rejection" }).click();
    await expect.poll(() => directRejection).toMatchObject({
      decision: "rejected",
      send_email: true,
      speaker_message: "Thank you for submitting to our event.",
    });
    await expect(page.getByRole("heading", { name: "Proposal inbox" })).toBeVisible();
    await expect(page.getByText("0 selected", { exact: true })).toBeVisible();
    await expect(page.getByText("Already decided", { exact: true })).toBeVisible();
    await expect(page.getByText("In Initial review", { exact: true })).toBeVisible();
    await expect(page.locator('input[name="submission_ids"]')).toHaveCount(1);
    await page.getByRole("button", { name: "Select submitted" }).click();
    await expect(page.getByText("1 selected", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Configure evaluation round" }).click();
    await expect(page.locator("#round-disclosure")).toHaveAttribute("open", "");
    await expect(page.getByText("Accounts are never searchable or listed.")).toBeVisible();
    await page.getByLabel("Reviewer email").fill("reviewer@example.com");
    await page.getByRole("button", { name: "Add reviewer" }).click();
    await expect(page.getByLabel("Reviewer", { exact: true })).toBeChecked();
    expect(reviewerLookups).toHaveLength(1);
    expect(new URL(reviewerLookups[0]).searchParams.get("email")).toBe("reviewer@example.com");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const reviewerA11y = await new AxeBuilder({ page })
      .include("#round-form")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(reviewerA11y.violations.filter(({ impact }) => impact === "critical" || impact === "serious")).toEqual([]);
    await page.getByLabel("Minimum rating").fill("5");
    await page.getByLabel("Maximum rating").fill("5");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    expect(roundWrites).toBe(0);
    await page.getByLabel("Minimum rating").fill("1");
    await page.getByLabel("Maximum rating").fill("5");
    await page.getByLabel("Recommendations").fill("accept, accept");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    expect(roundWrites).toBe(0);
    await page.getByLabel("Recommendations").fill("accept, reject");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    await expect.poll(() => roundWrites).toBe(1);
    await expect(page.locator("#round-history")).toContainText("Initial review");
    await expect(page.locator("#round-history")).not.toContainText("No evaluation rounds yet.");

    const assignment = {
      id: assignmentId, round_id: "11111111-1111-4111-8111-111111111111", round_name: "Initial review",
      submission_id: assignmentId, proposal_title: "A proposal", proposal_abstract: "Abstract", speaker_name: "Speaker",
      rating_min: 1, rating_max: 5, recommendations: ["accept", "reject"], evaluator_guidance: "", evaluation_state: "not_started",
      criteria: [], criterion_scores: {}, blind_review: false, review_closes_at_ms: null,
      rating: null, recommendation: null, internal_comment: "", comment_required: false,
      answers: [], hidden_answer_count: 0,
    };
    await page.route("**/api/v1/evaluator/assignments*", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [assignment], next_cursor: null, total: 1, completed_count: 0 }) }));
    let evaluationWrites = 0;
    let conflictWrites = 0;
    await page.route(`**/api/v1/evaluator/assignments/${assignmentId}/evaluation`, async (route) => { evaluationWrites += 1; await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) }); });
    await page.route(`**/api/v1/evaluator/assignments/${assignmentId}/conflict`, async (route) => { conflictWrites += 1; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({}) }); });
    await page.goto("/reviews");
    await page.getByRole("button", { name: "Open review" }).click();
    await page.getByRole("button", { name: "Save draft" }).click();
    expect(evaluationWrites).toBe(0);
    await page.getByLabel("Rating").fill("4");
    await page.getByLabel("Recommendation").selectOption("accept");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect.poll(() => evaluationWrites).toBe(1);
    await page.getByText("Declare a conflict of interest").click();
    await page.getByRole("button", { name: "Remove my assignment" }).click();
    expect(conflictWrites).toBe(0);
    await page.getByLabel("Explanation").fill("Same employer");
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Remove my assignment" }).click();
    await expect.poll(() => conflictWrites).toBe(1);
  });

  test("a failed rejection leaves the confirm and cancel controls reachable", async ({ page }) => {
    // Regression: the alert paragraph sits directly above the confirm/cancel row inside
    // a dialog that scrolls. Focusing the alert parked its bottom edge flush with the
    // dialog's bottom edge, pushing both buttons below the fold -- enabled, but invisible
    // and not hit-testable, so the only way out of the modal was Escape or a reload.
    // toBeEnabled() passes with the bug present and Playwright's click() auto-scrolls, so
    // this asserts geometry: the buttons must sit inside the dialog's visible box and be
    // the topmost element at their own centre point.
    await polyfillUuid(page);
    await mockSession(page);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, time_zone: "UTC" }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ published_form: null }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/evaluation-rounds`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/submissions`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{
          id: assignmentId,
          speaker_name: "Speaker",
          speaker_email: "speaker@example.com",
          proposal_title: "A proposal",
          proposal_abstract: "Abstract",
          status: "submitted",
          submitted_at_ms: 1_900_000_000_000,
          routed_category: null,
          routed_track: "Platform",
          routed_review_queue: "Technical",
          answers: { audience_level: "Intermediate" },
        }],
        total: 1,
      }),
    }));
    // A conflict that is NOT the round guardrail, so it reaches the in-panel alert
    // instead of being prevented upstream by the round link.
    await page.route(`**/api/v1/admin/events/${eventId}/submissions/${assignmentId}/reject`, (route) => route.fulfill({
      status: 409,
      contentType: "application/json",
      headers: { "x-request-id": "req_regression" },
      body: JSON.stringify({
        error: { code: "conflict", message: "This proposal already has a decision recorded." },
        request_id: "req_regression",
      }),
    }));

    const reachable = () => page.evaluate(() => {
      const dialog = document.querySelector("#submission-detail") as HTMLDialogElement;
      const box = dialog.getBoundingClientRect();
      return Array.from(dialog.querySelectorAll(".reject-without-review__panel .actions button")).map((node) => {
        const rect = node.getBoundingClientRect();
        const topmost = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return {
          label: node.textContent,
          insideDialog: rect.top >= box.top && rect.bottom <= box.bottom,
          hitTestReachesButton: topmost === node || node.contains(topmost),
        };
      });
    });

    await page.goto(`/admin/events/${eventId}/submissions`);
    await page.getByRole("button", { name: "View proposal" }).first().click();
    const detail = page.getByRole("dialog", { name: "Proposal details" });
    await detail.getByRole("button", { name: "Reject without review" }).click();

    // The local validation error must not scroll away the field it is talking about.
    await detail.getByRole("button", { name: "Confirm rejection" }).click();
    await expect(detail.getByText("Add an internal reason before rejecting.")).toBeInViewport();
    await expect(detail.getByLabel("Internal reason")).toBeInViewport();

    await detail.getByLabel("Internal reason").fill("Outside the program scope");
    await detail.getByRole("button", { name: "Confirm rejection" }).click();
    await expect(detail.getByText("This proposal already has a decision recorded.")).toBeVisible();
    await expect(detail.getByRole("button", { name: "Confirm rejection" })).toBeEnabled();
    await expect(detail.getByRole("button", { name: "Cancel" })).toBeEnabled();
    expect(await reachable()).toEqual([
      { label: "Confirm rejection", insideDialog: true, hitTestReachesButton: true },
      { label: "Cancel", insideDialog: true, hitTestReachesButton: true },
    ]);

    // And the way out of the panel works without Escape or a reload.
    await detail.getByRole("button", { name: "Cancel" }).click();
    await expect(detail.getByRole("button", { name: "Reject without review" })).toBeVisible();
  });

  test("workspace resource, task, and token forms enforce their contracts", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    let resourceWrites = 0;
    let taskWrites = 0;
    let tokenWrites = 0;
    await page.route(`**/api/v1/admin/events/${eventId}/resources`, async (route) => {
      if (route.request().method() === "POST") resourceWrites += 1;
      await route.fulfill({ status: route.request().method() === "POST" ? 201 : 200, contentType: "application/json", body: JSON.stringify(route.request().method() === "POST" ? {} : { data: [] }) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, time_zone: "Asia/Kolkata" }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-targets`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ event_speaker_id: assignmentId, display_name: "Speaker", proposal_title: "A proposal", selection_status: "accepted" }] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-tasks`, async (route) => { taskWrites += 1; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({}) }); });
    await page.route(`**/api/v1/admin/events/${eventId}/integrations/accelevents/tokens`, async (route) => { tokenWrites += 1; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ token: "one-time-token" }) }); });

    await page.goto(`/admin/events/${eventId}/speaker-content`);
    await page.getByText("Add a resource", { exact: true }).click();
    const resource = page.locator("#resource-form");
    await resource.getByLabel("Title").fill("Speaker guide");
    await resource.getByLabel(/Approved embed URL/).fill("https://evil.example/document");
    await resource.getByRole("button", { name: "Publish resource" }).click();
    expect(resourceWrites).toBe(0);
    await resource.getByLabel(/Approved embed URL/).fill("https://docs.google.com/document/d/example");
    await resource.getByRole("button", { name: "Publish resource" }).click();
    await expect.poll(() => resourceWrites).toBe(1);

    await page.getByText("Assign a task", { exact: true }).click();
    const task = page.locator("#task-form");
    await task.getByLabel("Speaker").selectOption(assignmentId);
    await task.getByLabel("Task title").fill("Confirm requirements");
    await task.getByText("Required", { exact: true }).click();
    await task.getByRole("button", { name: "Assign task" }).click();
    expect(taskWrites).toBe(0);
    await task.getByLabel("Question label").fill("Dietary requirements");
    await task.getByRole("button", { name: "Assign task" }).click();
    await expect.poll(() => taskWrites).toBe(1);

    await page.goto(`/admin/events/${eventId}/workspace`);
    await page.getByText("Connect Accelevents", { exact: true }).click();
    await page.getByLabel("Token label").fill("Accelevents demo");
    await page.getByRole("button", { name: "Generate token" }).click();
    await expect(page.getByText("one-time-token")).toBeVisible();
    expect(tokenWrites).toBe(1);
  });

  test("onboarding filters refresh the server-backed snapshot", async ({ page }) => {
    await mockSession(page);
    let dashboardReads = 0;
    let lastQuery = "";
    await page.route(`**/api/v1/admin/events/${eventId}/onboarding*`, async (route) => {
      dashboardReads += 1;
      lastQuery = new URL(route.request().url()).search;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          event_name: "Conference",
          time_zone: "Asia/Kolkata",
          generated_at_ms: Date.now(),
          summary: { complete: 0, incomplete: 1, overdue: 1, due_soon: 0, submitted: 1, accepted: 1, rejected: 0, evaluations_finalized: 0, evaluations_total: 0 },
          data: [{ event_speaker_id: assignmentId, display_name: "Speaker", proposal_title: "A proposal", task_id: "77777777-7777-4777-8777-777777777777", task_type: "headshot", task_title: "Upload headshot", state: "overdue", due_at_ms: Date.now() - 1000, last_activity_at_ms: Date.now() - 2000 }],
          next_cursor: null,
        }),
      });
    });

    await page.goto(`/admin/events/${eventId}/onboarding`);
    await expect(page.getByRole("status").first()).toHaveText("1 speaker record shown.");
    await page.getByLabel("Task state").selectOption("overdue");
    await page.getByLabel("Task type").selectOption("headshot");
    await page.getByRole("button", { name: "Apply filters" }).click();
    await expect.poll(() => dashboardReads).toBeGreaterThanOrEqual(2);
    expect(lastQuery).toBe("?state=overdue&task_type=headshot");
    await expect(page).toHaveURL(/state=overdue&task_type=headshot/);
  });

  test("speaker custom task forms validate locally", async ({ page }) => {
    await polyfillUuid(page);
    const speakerSession = { ...organizerSession, email: "speaker@example.com", account_roles: ["speaker"], active_role: "speaker", default_role: "speaker", organization_access: [], event_access: [{ organization_id: organizationId, event_id: eventId, event_name: "Conference 2030", permissions: [], assignments: ["speaker"] }] };
    await mockSession(page, speakerSession);
    let taskWrites = 0;
    let taskComplete = false;
    let profile = { display_name: "Speaker", job_title: "", company: "", biography: "", location: "", links: [], version: 1 };
    const portal = () => ({
      event: { id: eventId, name: "Conference", starts_at_ms: Date.UTC(2030, 2, 20), ends_at_ms: Date.UTC(2030, 2, 21), time_zone: "Asia/Kolkata" },
      profile,
      tasks: taskComplete ? [] : [{ id: assignmentId, task_type: "custom", title: "Dietary needs", help_text: "Tell us", destination_path: "#tasks", state: "open", due_at_ms: null, completed_at_ms: null, form_fields: [{ key: "response", label: "Dietary requirements", type: "text", required: true, choices: [] }], response: {}, version: 1 }],
      submissions: [{ id: programId, proposal_title: "A proposal", status: "accepted" }], completed_tasks: taskComplete ? 1 : 0, total_tasks: 1,
    });
    await page.route("**/api/v1/speaker/portal", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(portal()) }));
    await page.route("**/api/v1/speaker/resources", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/speaker/events/${eventId}/assets`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/speaker/tasks/${assignmentId}/response`, async (route) => { taskWrites += 1; taskComplete = true; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: assignmentId, state: "completed", response: {}, version: 2 }) }); });

    await page.goto("/speaker");
    await expect(page.getByRole("heading", { name: "Needs attention" })).toBeVisible();

    const taskForm = page.locator(".task-list form");
    await taskForm.getByRole("button", { name: "Send response" }).click();
    expect(taskWrites).toBe(0);
    await taskForm.getByLabel("Dietary requirements").fill("Vegetarian");
    await taskForm.getByRole("button", { name: "Send response" }).click();
    await expect.poll(() => taskWrites).toBe(1);

  });

  test("fresh agenda setup leads directly to timezone-correct scheduling", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    const start = Date.UTC(2030, 2, 19, 18, 30);
    const end = Date.UTC(2030, 2, 20, 18, 30);
    let configured = false;
    let scheduled = false;
    let setupWrites = 0;
    let publishWrites = 0;
    let labelWrites = 0;
    let labelAssignmentWrites = 0;
    let labels: Array<Record<string, unknown>> = [];
    let assignedLabelIds: string[] = [];
    let savedCandidate: Record<string, unknown> | null = null;
    const assignedLabels = () => labels.filter((label) => assignedLabelIds.includes(String(label.id)));
    const model = () => ({
      event: { id: eventId, name: "Conference", time_zone: "Asia/Kolkata", starts_at_ms: start, ends_at_ms: end },
      revision: { id: "11111111-1111-4111-8111-111111111111", version: 1, state: "draft" },
      rooms: [{ id: "22222222-2222-4222-8222-222222222222", name: "Main stage" }],
      tracks: [{ id: "33333333-3333-4333-8333-333333333333", name: "General" }],
      labels,
      items: scheduled ? [{ id: "44444444-4444-4444-8444-444444444444", session_id: assignmentId, title: "A proposal", abstract: "Abstract", content_status: "approved", content_version: 1, label_version: labelAssignmentWrites + 1, labels: assignedLabels(), label_ids: assignedLabelIds, start_at_ms: savedCandidate?.start_at_ms, end_at_ms: savedCandidate?.end_at_ms, room_id: "22222222-2222-4222-8222-222222222222", room_name: "Main stage", track_id: null, track_name: null, version: 1 }] : [],
      unscheduled_sessions: scheduled ? [] : [{ session_id: assignmentId, title: "A proposal", abstract: "Abstract", content_status: "approved", content_version: 1, label_version: labelAssignmentWrites + 1, labels: assignedLabels(), label_ids: assignedLabelIds }],
    });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda`, async (route) => {
      if (!configured) { await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { message: "Not found" } }) }); return; }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(model()) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/setup`, async (route) => { setupWrites += 1; configured = true; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(model()) }); });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/preview`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ valid: true, conflicts: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/items`, async (route) => { savedCandidate = route.request().postDataJSON(); scheduled = true; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: "44444444-4444-4444-8444-444444444444", version: 1 }) }); });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/publish`, async (route) => { publishWrites += 1; await route.fulfill({ contentType: "application/json", body: JSON.stringify({ state: "published" }) }); });
    await page.route(`**/api/v1/admin/events/${eventId}/labels`, async (route) => {
      labelWrites += 1;
      const body = route.request().postDataJSON();
      const label = { id: "55555555-5555-4555-8555-555555555555", name: body.name, color: String(body.color).toUpperCase(), status: "active", version: 1, can_manage: true };
      labels = [label];
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(label) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/sessions/${assignmentId}/labels`, async (route) => {
      labelAssignmentWrites += 1;
      assignedLabelIds = route.request().postDataJSON().label_ids;
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ session_id: assignmentId, version: labelAssignmentWrites + 1, labels: assignedLabels() }) });
    });

    await page.goto(`/admin/events/${eventId}/agenda`);
    await expect(page.getByRole("heading", { name: "Set up the schedule" })).toBeVisible();
    await page.getByRole("button", { name: "Create agenda" }).click();
    expect(setupWrites).toBe(0);
    await page.getByLabel("Rooms").fill("Main stage");
    await page.getByLabel(/Tracks/).fill("General");
    await page.getByRole("button", { name: "Create agenda" }).click();
    await expect(page.getByRole("button", { name: "Schedule A proposal", exact: true })).toBeVisible();
    expect(setupWrites).toBe(1);
    await page.getByText("Schedule tools", { exact: true }).click();
    const labelForm = page.locator("#label-form");
    await labelForm.getByLabel("Label name").fill("Beginner");
    await labelForm.getByLabel("Color", { exact: true }).fill("#19724b");
    await labelForm.getByRole("button", { name: "Add label" }).click();
    await expect(page.getByText("Beginner", { exact: true }).first()).toBeVisible();
    expect(labelWrites).toBe(1);
    await page.getByRole("button", { name: "Schedule A proposal", exact: true }).click();
    const editor = page.getByRole("dialog", { name: "Schedule A proposal" });
    await editor.getByLabel("Beginner").check();
    await editor.getByLabel("Starts").fill("2030-03-20T09:00");
    await editor.getByLabel("Ends").fill("2030-03-20T10:00");
    await editor.getByLabel("Room").selectOption("22222222-2222-4222-8222-222222222222");
    await expect(editor.getByText("No room or speaker conflicts found.")).toBeVisible();
    await editor.getByRole("button", { name: "Save to draft" }).click();
    await expect(page.locator("#status")).toHaveText("Session scheduled successfully.");
    expect(labelAssignmentWrites).toBe(1);
    await expect(page.locator(".session-card .label-chip", { hasText: "Beginner" })).toBeVisible();
    expect(savedCandidate).toMatchObject({
      start_at_ms: Date.UTC(2030, 2, 20, 3, 30),
      end_at_ms: Date.UTC(2030, 2, 20, 4, 30),
      room_id: "22222222-2222-4222-8222-222222222222",
    });
    await page.getByRole("button", { name: "Publish agenda" }).click();
    const publishDialog = page.getByRole("dialog", { name: "Publish this agenda?" });
    await expect(publishDialog).toBeVisible();
    await expect(publishDialog.getByText("1 scheduled session will become publicly visible.")).toBeVisible();
    await publishDialog.getByRole("button", { name: "Cancel" }).click();
    expect(publishWrites).toBe(0);
    await page.getByRole("button", { name: "Publish agenda" }).click();
    await publishDialog.getByRole("button", { name: "Publish agenda" }).click();
    await expect(page.locator("#status")).toHaveText("Agenda published. Calendar updates were queued for speakers.");
    expect(publishWrites).toBe(1);
  });
});
