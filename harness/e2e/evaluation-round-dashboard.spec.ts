import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const appRoot = resolve(staticRoot, "app");
const pageHtml = readFileSync(resolve(appRoot, "index.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace(
    "</head>",
    `<style>${readFileSync(resolve(appRoot, "assets/reviews.css"), "utf8")}</style></head>`,
  )
  .replace(
    "</body>",
    `<script>${readFileSync(resolve(staticRoot, "api_client.js"), "utf8")}</script>`
      + `<script>${readFileSync(resolve(staticRoot, "app_shell.js"), "utf8")}</script>`
      + "</body>",
  );

const organizerSession = {
  user_id: "organizer",
  organization_id: "org-a",
  event_id: "event-a",
  csrf_token: "browser-test-csrf",
  email: "organizer@example.com",
  display_name: "Alex Organizer",
  profile_complete: true,
  account_roles: ["organizer"],
  active_role: "organizer",
  default_role: "organizer",
  organization_access: [{
    organization_id: "org-a",
    organization_name: "Example Events",
    permissions: ["owner"],
  }],
  event_access: [],
};

const reviewer = {
  evaluator_user_id: "reviewer-a",
  display_name: "Sam Whitfield",
  assigned_count: 0,
  completed_count: 0,
  conflict_count: 0,
};

function results(overrides: Record<string, unknown> = {}) {
  return {
    round_id: "round-a",
    event_id: "event-a",
    round_name: "Initial review",
    status: "draft",
    assigned_count: 0,
    completed_count: 0,
    average_rating: null,
    criteria: [],
    submissions: [],
    submission_count: 0,
    next_cursor: null,
    evaluators: [reviewer],
    available_evaluators: [],
    conflicts: [],
    ...overrides,
  };
}

async function openDashboard(
  page: import("@playwright/test").Page,
  body: Record<string, unknown>,
) {
  // Registered first because Playwright gives the newest matching route precedence.
  await page.route("**/admin/evaluation-rounds/round-a", (route) =>
    route.fulfill({ contentType: "text/html", body: pageHtml }));
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(organizerSession) }));
  await page.route("**/api/v1/admin/evaluation-rounds/round-a/results**", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(body) }));
  await page.route("**/api/v1/admin/events/event-a/submissions/*/decision-message-preview", (route) => {
    const request = route.request().postDataJSON() as { speaker_subject?: string; speaker_message?: string };
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        resolved_subject: request.speaker_subject || "Example Conference: proposal accepted",
        resolved_body: request.speaker_message || "Congratulations — your session has been accepted. Open your speaker portal for next steps.",
        proposal_title: "Reliable acceptance feedback",
        recipient_available: true,
      }),
    });
  });
  await page.goto("/admin/evaluation-rounds/round-a");
  await page.addScriptTag({
    path: resolve(appRoot, "assets/reviews.js"),
    type: "module",
  });
  await expect(page.getByRole("heading", { name: "Initial review" })).toBeVisible();
}

test.describe("evaluation round dashboard", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("explains an attached reviewer with no draft assignments", async ({ page }) => {
    await openDashboard(page, results());

    await expect(page.getByText("Sam Whitfield")).toBeVisible();
    await expect(page.getByText("No proposals assigned")).toBeVisible();
    await expect(page.getByText("No reviews assigned yet.")).toBeVisible();
    await expect(page.getByRole("progressbar", { name: "Finalized reviews" })).toHaveCount(0);

    const draftNote = page.getByRole("note");
    await expect(draftNote).toContainText("Reviewers cannot see assignments");
    await expect(draftNote.getByRole("link", { name: "Return to the proposal inbox" }))
      .toHaveAttribute("href", "/admin/events/event-a/submissions");
  });

  test("renders a useful true empty state when no reviewers are attached", async ({ page }) => {
    await openDashboard(page, results({ evaluators: [] }));

    await expect(page.getByText("No reviewers attached to this round yet.")).toBeVisible();
    await expect(page.getByText("No proposals assigned")).toHaveCount(0);
  });

  test("assigns one open-round proposal to an already attached reviewer", async ({ page }) => {
    let assignmentPayload: Record<string, unknown> | null = null;
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/evaluators", async (route) => {
      assignmentPayload = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          round_id: "round-a",
          evaluator_user_id: "reviewer-a",
          assignment_count: 1,
        }),
      });
    });
    await openDashboard(page, results({
      status: "open",
      assigned_count: 1,
      completed_count: 1,
      available_evaluators: [{ user_id: "reviewer-a", display_name: "Sam Whitfield" }],
      submissions: [{
        submission_id: "submission-a",
        speaker_name: "Taylor Speaker",
        proposal_title: "One exact proposal",
        assigned_count: 0,
        completed_count: 0,
        average_rating: null,
        decision: null,
        decision_round_id: null,
        internal_reason: "",
        correction_reason: "",
        reviews: [],
      }],
      submission_count: 1,
    }));

    await expect(page.getByText("No proposals assigned")).toBeVisible();
    await expect(page.getByText("No individual reviews yet.")).toBeVisible();
    await expect(page.getByText("Individual reviews (0)")).toHaveCount(0);
    await page.getByText("Assign reviewer", { exact: true }).click();
    await expect(page.getByRole("note")).toContainText(
      "returns this round to Review in progress",
    );
    await page.getByRole("combobox", { name: "Reviewer for One exact proposal" })
      .selectOption("reviewer-a");
    await page.getByRole("button", { name: "Assign to proposal" }).click();
    expect(assignmentPayload).toEqual({
      evaluator_user_id: "reviewer-a",
      submission_ids: ["submission-a"],
    });
    await expect(page.getByRole("status")).toContainText(
      "Reviewer assigned to “One exact proposal”.",
    );
    await expect(page.getByRole("button", { name: "Remove" })).toBeVisible();
  });

  test("keeps open-round progress and reviewer actions", async ({ page }) => {
    await openDashboard(page, results({
      status: "open",
      assigned_count: 2,
      evaluators: [{ ...reviewer, assigned_count: 2 }],
    }));

    const progress = page.getByRole("progressbar", { name: "Finalized reviews" });
    await expect(progress).toHaveAttribute("aria-valuetext", "0 of 2 reviews finalized");
    await expect(page.getByRole("button", { name: "Send reminder" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove" })).toBeVisible();
    await expect(page.getByRole("note")).toHaveCount(0);
  });

  test("surfaces post-close replacement work and keeps reminders available", async ({ page }) => {
    await openDashboard(page, results({
      status: "closed",
      assigned_count: 1,
      evaluators: [{
        ...reviewer,
        assigned_count: 1,
        late_assignment_count: 1,
        late_completed_count: 0,
      }],
      submissions: [{
        submission_id: "submission-a",
        speaker_name: "Riley Speaker",
        proposal_title: "Reliable late reviews",
        assigned_count: 1,
        completed_count: 0,
        average_rating: null,
        decision: "accepted",
        decision_round_id: "round-a",
        internal_reason: "Strong fit",
        correction_reason: "",
        reviews: [],
      }],
      submission_count: 1,
    }));

    await expect(page.getByRole("button", { name: "Send reminder" })).toBeVisible();
    await expect(page.getByRole("note")).toContainText(
      "replacement review was assigned after this round closed",
    );
    await expect(page.getByRole("note")).toContainText(
      "recorded decisions do not change automatically",
    );
  });

  test("names, confirms, and safely fails round exports", async ({ page }) => {
    let releaseExport!: () => void;
    const exportGate = new Promise<void>((resolve) => { releaseExport = resolve; });
    let exportRequests = 0;
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/export.csv", async (route) => {
      exportRequests += 1;
      await exportGate;
      return route.fulfill({
        contentType: "text/csv; charset=utf-8",
        headers: {
          "content-disposition": "attachment; filename=\"fallback.csv\"; filename*=UTF-8''example-event-initial-review-results-round.csv",
          "x-export-row-count": "42",
        },
        body: "submission_id,proposal_title\nsubmission-a,Example\n",
      });
    });
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/reviews.csv", (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        headers: { "x-conflict-type": "export-limit" },
        body: JSON.stringify({
          error: { code: "conflict", message: "This round exceeds the single-file export limit." },
          request_id: "export-request",
        }),
      }));
    await openDashboard(page, results({ event_name: "Example Event", status: "closed" }));

    const resultsLink = page.getByRole("link", { name: "Results CSV" });
    await resultsLink.focus();
    const downloadPromise = page.waitForEvent("download");
    await resultsLink.click();
    await expect(page.getByRole("link", { name: "Preparing CSV…" })).toHaveAttribute("aria-disabled", "true");
    await page.getByRole("link", { name: "Preparing CSV…" }).dispatchEvent("click");
    expect(exportRequests).toBe(1);
    releaseExport();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("example-event-initial-review-results-round.csv");
    await expect(page.getByRole("status")).toContainText("Download started: example-event-initial-review-results-round.csv — 42 records.");
    await expect(resultsLink).toBeFocused();
    await expect(page.getByRole("link", { name: "Open results directly" })).toHaveAttribute("target", "_blank");

    let downloads = 0;
    page.on("download", () => { downloads += 1; });
    await page.getByRole("link", { name: "Review details CSV" }).click();
    await expect(page.getByRole("status")).toContainText("This round exceeds the single-file export limit.");
    expect(page.url()).toContain("/admin/evaluation-rounds/round-a");
    expect(downloads).toBe(0);
    await expect(page.getByRole("link", { name: "Review details CSV" })).toHaveAttribute("aria-disabled", "false");
  });

  for (const status of [403, 404]) {
    test(`re-enters the guarded round document after an export ${status}`, async ({ page }) => {
      await openDashboard(page, results({ event_name: "Example Event", status: "closed" }));
      await page.route("**/api/v1/admin/evaluation-rounds/round-a/export.csv", (route) =>
        route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "access_changed", message: "Access changed." } }),
        }));

      const navigation = page.waitForNavigation({ waitUntil: "domcontentloaded" });
      await page.getByRole("link", { name: "Results CSV" }).click();

      await navigation;
      expect(await page.evaluate((code) => {
        const api = (window as any).SessionBuddyApi;
        const scope = api.recoveryScope.round("round-a");
        return sessionStorage.getItem(api.documentRecoveryKey(code, scope));
      }, String(status))).not.toBeNull();
    });
  }

  test("redirects a signed-out export to sign-in", async ({ page }) => {
    await openDashboard(page, results({ event_name: "Example Event", status: "closed" }));
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/export.csv", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "authentication_required", message: "Sign in." } }),
      }));

    const signInNavigation = page.waitForRequest((request) => {
      const target = new URL(request.url());
      return request.isNavigationRequest()
        && target.pathname === "/sign-in"
        && target.searchParams.get("redirect") === "/admin/evaluation-rounds/round-a";
    });
    await page.getByRole("link", { name: "Results CSV" }).click();

    await signInNavigation;
  });

  test("keeps a repeated document-recovery failure inline during the throttle window", async ({ page }) => {
    await openDashboard(page, results({ event_name: "Example Event", status: "closed" }));
    await page.evaluate(() => {
      const api = (window as any).SessionBuddyApi;
      const scope = api.recoveryScope.round("round-a");
      sessionStorage.setItem(api.documentRecoveryKey("404", scope), String(Date.now()));
    });
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/export.csv", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "not_found", message: "The round is no longer available." } }),
      }));

    await page.getByRole("link", { name: "Results CSV" }).click();

    await expect(page).toHaveURL(/\/admin\/evaluation-rounds\/round-a$/);
    await expect(page.getByRole("status")).toContainText("The round is no longer available.");
    await expect(page.getByRole("status")).toBeFocused();
  });

  test("shows every typed criterion response in rubric order", async ({ page }) => {
    await openDashboard(page, results({
      status: "closed",
      assigned_count: 1,
      completed_count: 1,
      criteria: [
        { key: "quality", label: "Quality", response_type: "score", required: true, weight: 100, options: [], purpose: null },
        { key: "recommendation", label: "Recommendation", response_type: "select", required: true, weight: null, options: ["Accept", "Maybe", "Reject"], purpose: "recommendation" },
        { key: "comments", label: "Comments", response_type: "text", required: true, weight: null, options: [], purpose: "comment" },
      ],
      submissions: [{
        submission_id: "submission-a",
        speaker_name: "Taylor Speaker",
        proposal_title: "Readable review content",
        assigned_count: 1,
        completed_count: 1,
        average_rating: 4,
        decision: null,
        decision_round_id: null,
        internal_reason: "",
        correction_reason: "",
        reviews: [{
          evaluator_user_id: "reviewer-a",
          evaluator_name: "Sam Whitfield",
          state: "final",
          rating: 4,
          weighted_score: 4,
          recommendation: "Maybe",
          internal_comment: "",
          criterion_responses: {
            quality: 4,
            recommendation: "Maybe",
            comments: "The full reviewer assessment remains visible.",
          },
        }],
      }],
      submission_count: 1,
    }));

    const disclosure = page.locator(".individual-reviews");
    const summary = disclosure.locator("summary");
    const box = await summary.boundingBox();
    expect(box).not.toBeNull();
    // Exercise the empty row body, not the summary text or disclosure glyph.
    await summary.click({ position: { x: box!.width * 0.75, y: box!.height / 2 } });
    await expect(disclosure).toHaveAttribute("open", "");
    await expect(page.getByText("No decision", { exact: true })).toBeVisible();
    const responses = page.locator(".review-responses");
    await expect(responses.locator("dt")).toHaveText(["Quality", "Recommendation", "Comments"]);
    await expect(responses).toContainText("The full reviewer assessment remains visible.");
  });

  test("shows decision progress immediately and confirms before the results refresh", async ({ page }) => {
    const submission = {
      submission_id: "submission-a",
      speaker_name: "Taylor Speaker",
      proposal_title: "Reliable acceptance feedback",
      assigned_count: 1,
      completed_count: 1,
      average_rating: 4,
      decision: null,
      decision_round_id: null,
      internal_reason: "",
      correction_reason: "",
      reviews: [],
    };
    let releaseDecision!: () => void;
    let releaseRefresh!: () => void;
    const decisionGate = new Promise<void>((resolve) => { releaseDecision = resolve; });
    const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
    let decisionRequests = 0;
    let decisionPayload: Record<string, unknown> | null = null;
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/submissions/submission-a/decision", async (route) => {
      decisionRequests += 1;
      decisionPayload = route.request().postDataJSON();
      await decisionGate;
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ communication_queued: true }),
      });
    });
    await openDashboard(page, results({
      status: "closed",
      assigned_count: 1,
      completed_count: 1,
      submissions: [submission],
      submission_count: 1,
    }));
    await page.unroute("**/api/v1/admin/evaluation-rounds/round-a/results**");
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/results**", async (route) => {
      await refreshGate;
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(results({
          status: "closed",
          assigned_count: 1,
          completed_count: 1,
          submissions: [{
            ...submission,
            decision: "accepted",
            decision_round_id: "round-a",
          }],
          submission_count: 1,
        })),
      });
    });

    await page.getByRole("button", { name: "Accept" }).click();
    await expect(page.getByText("Default subject: Example Conference: proposal accepted")).toBeVisible();
    await page.getByLabel("Custom subject").fill("Your proposal update");
    await page.getByLabel("Custom message").fill("We would be delighted to welcome your session.");
    await expect(page.getByLabel("Decision email preview")).toContainText("Subject: Your proposal update");
    await page.getByRole("button", { name: "Confirm accepted" }).click();
    await expect(page.getByRole("button", { name: "Recording…" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await expect(page.locator(".decision-confirmation")).toHaveAttribute("aria-busy", "true");
    await page.getByRole("button", { name: "Recording…" }).dispatchEvent("click");
    expect(decisionRequests).toBe(1);
    expect(decisionPayload).toMatchObject({
      speaker_subject: "Your proposal update",
      speaker_message: "We would be delighted to welcome your session.",
    });

    releaseDecision();
    await expect(page.getByText("Decision recorded as accepted. Speaker email queued."))
      .toBeVisible();
    releaseRefresh();
    await expect(page.getByText("final decision in effect")).toBeVisible();
    expect(decisionRequests).toBe(1);
    await page.getByRole("button", { name: "Correct to rejected" }).click();
    await expect(page.getByLabel("Email the speaker about this correction")).toBeChecked();
    await expect(page.getByLabel("Custom subject")).toBeVisible();
  });

  test("reuses the decision idempotency key when an operator retries", async ({ page }) => {
    const keys: string[] = [];
    let attempts = 0;
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/submissions/submission-a/decision", async (route) => {
      attempts += 1;
      keys.push(route.request().headers()["idempotency-key"]);
      if (attempts === 1) {
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { message: "Temporarily unavailable." } }),
        });
      }
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ communication_queued: false }),
      });
    });
    await openDashboard(page, results({
      status: "closed",
      assigned_count: 1,
      completed_count: 1,
      submissions: [{
        submission_id: "submission-a",
        speaker_name: "Taylor Speaker",
        proposal_title: "Retry-safe acceptance",
        assigned_count: 1,
        completed_count: 1,
        average_rating: 4,
        decision: null,
        decision_round_id: null,
        internal_reason: "",
        correction_reason: "",
        reviews: [],
      }],
      submission_count: 1,
    }));

    await page.getByRole("button", { name: "Accept" }).click();
    await page.getByRole("button", { name: "Confirm accepted" }).click();
    await expect(page.getByRole("button", { name: "Confirm accepted" })).toBeEnabled();
    await page.getByRole("button", { name: "Confirm accepted" }).click();
    await expect(page.getByText("Decision recorded as accepted. No email sent."))
      .toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });
});
