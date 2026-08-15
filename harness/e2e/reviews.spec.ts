import { expect, test } from "@playwright/test";

const reviewerSession = JSON.stringify({
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  csrf_token: "browser-test-csrf",
  email: "namohh.namaha+reviewer@example.com",
  display_name: "Rhea Reviewer",
  profile_complete: true,
  account_roles: ["reviewer"],
  active_role: "reviewer",
  default_role: "reviewer",
  organization_access: [],
  event_access: [{
    organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    event_name: "Example Event",
    permissions: [],
    assignments: ["reviewer"],
  }],
});

async function polyfillUuid(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    if (!crypto.randomUUID) {
      Object.defineProperty(crypto, "randomUUID", {
        value: () => "11111111-1111-4111-8111-111111111111",
      });
    }
  });
}

test.describe("reviewer workspace", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");
  test.beforeEach(async ({ page }) => polyfillUuid(page));

  test("an evaluator with no assignments gets a clean, useful empty state", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: [], total: 0, completed_count: 0, next_cursor: null }),
      }));

    await page.goto("/reviews");

    await expect(page.getByRole("heading", { name: "Assigned reviews", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "No reviews assigned" })).toBeVisible();
    await expect(page.getByText("New assignments will appear here, and we’ll notify you by email.")).toBeVisible();
    await expect(page.getByText("Show finalized")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();

    // Non-organizer portals do not render an empty Main navigation card.
    await expect(page.locator(".sb-sidebar__primary")).toHaveCount(0);
    await expect(page.locator(".sb-sidebar")).toHaveCount(0);
    await expect(page.locator(".sb-account__identity strong")).toHaveText("Rhea Reviewer · Reviewer");

    // The accessibility link is visually clipped until keyboard focus.
    const skipLink = page.getByRole("link", { name: "Skip to reviews" });
    expect((await skipLink.boundingBox())?.width).toBe(1);
    await skipLink.focus();
    expect((await skipLink.boundingBox())?.width).toBeGreaterThan(1);
  });

  test("shows a compact docket and opens one focused scorecard", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) => {
      if (route.request().url().endsWith("/evaluation")) return route.fallback();
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          total: 1,
          completed_count: 0,
          next_cursor: null,
          data: [{
            id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            round_name: "Initial review",
            proposal_title: "Building reliable platform tooling",
            proposal_abstract: "A practical session about reliable internal platforms and clear operational feedback.",
            speaker_name: "Hidden for blind review",
            rating_min: 1,
            rating_max: 5,
            recommendations: ["accept", "reject"],
            evaluator_guidance: "Score the proposal itself.",
            evaluation_state: "not_started",
            comment_required: false,
            rating: null,
            recommendation: null,
            internal_comment: "",
            criteria: [
              { key: "relevance", label: "Relevance", response_type: "score", required: true, weight: 40, options: [] },
              { key: "quality", label: "Quality", response_type: "score", required: true, weight: 35, options: [] },
              { key: "audience_value", label: "Audience value", response_type: "score", required: true, weight: 25, options: [] },
            ],
            criterion_responses: {},
            blind_review: true,
            review_closes_at_ms: null,
            answers: [{ label: "Track", value: "Platform & Infra" }],
            hidden_answer_count: 3,
          }],
        }),
      });
    });

    await page.goto("/reviews");

    await expect(page.getByRole("heading", { name: "Building reliable platform tooling" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Scorecard" })).toHaveCount(0);
    await expect(page.locator(".sb-sidebar")).toHaveCount(0);

    await page.getByRole("button", { name: "Open review" }).click();
    await expect(page.getByRole("group", { name: "Scorecard" })).toBeVisible();
    await page.getByRole("button", { name: "Finalize" }).click();
    await expect(page.getByText("Complete every required scorecard response with a valid value before finalizing.")).toBeVisible();
    await expect(page.getByText("Full proposal (1 answer)")).toBeVisible();
    await expect(page.getByText("Platform & Infra")).not.toBeVisible();
    await page.getByText("Full proposal (1 answer)").click();
    await expect(page.getByText("Platform & Infra")).toBeVisible();
    await page.getByRole("button", { name: "Back to assigned proposals" }).click();
    await expect(page.getByRole("button", { name: "Open review" })).toBeVisible();
  });

  test("keeps weighted scorecard precision in the preview and save payload", async ({ page }) => {
    const assignmentId = "abababab-abab-4bab-8bab-abababababab";
    let savedPayload: Record<string, unknown> | null = null;
    let evaluationState = "not_started";
    let savedResponses: Record<string, number> = {};
    let criteria = [
      { key: "originality", label: "Originality", response_type: "score", required: true, weight: 67, options: [] },
      { key: "relevance", label: "Relevance", response_type: "score", required: true, weight: 33, options: [] },
    ];
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith(`/${assignmentId}/evaluation`)) {
        savedPayload = route.request().postDataJSON();
        evaluationState = String(savedPayload?.state);
        savedResponses = savedPayload?.criterion_responses as Record<string, number>;
        return route.fulfill({ contentType: "application/json", body: "{}" });
      }
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          total: 1,
          completed_count: evaluationState === "final" ? 1 : 0,
          next_cursor: null,
          data: [{
            id: assignmentId,
            round_name: "Weighted review",
            proposal_title: "Precision without contradiction",
            proposal_abstract: "A scorecard should show the score it actually computes.",
            speaker_name: "Hidden for blind review",
            rating_min: 1,
            rating_max: 5,
            recommendations: ["accept", "reject"],
            evaluator_guidance: "",
            evaluation_state: evaluationState,
            comment_required: false,
            // The stored integer is deliberately lossy. The UI must derive its preview
            // from the criterion responses instead of falling back to this value.
            rating: evaluationState === "final" ? 3 : null,
            recommendation: evaluationState === "final" ? "accept" : null,
            internal_comment: "",
            criteria,
            criterion_responses: savedResponses,
            blind_review: true,
            review_closes_at_ms: null,
            answers: [],
            hidden_answer_count: 0,
          }],
        }),
      });
    });

    await page.goto("/reviews");
    await page.getByRole("button", { name: "Open review" }).click();
    await page.getByLabel("Originality", { exact: false }).fill("4");
    await expect(page.getByText("Partial weighted score preview:").locator("strong")).toHaveText("4.00");
    await page.getByLabel("Relevance", { exact: false }).fill("2");
    await expect(page.getByText("Weighted score preview:").locator("strong")).toHaveText("3.34");

    await page.getByLabel("Originality", { exact: false }).fill("5");
    await page.getByLabel("Relevance", { exact: false }).fill("1");
    await expect(page.getByText("Weighted score preview:").locator("strong")).toHaveText("3.68");

    // Python rounds 2.5 to the even integer 2 while JavaScript Math.round returns 3.
    // The preview must keep the exact weighted value instead of choosing either integer.
    criteria = criteria.map((criterion) => ({ ...criterion, weight: 50 }));
    await page.reload();
    await page.getByRole("button", { name: "Open review" }).click();
    await page.getByLabel("Originality", { exact: false }).fill("3");
    await expect(page.getByText("Partial weighted score preview:").locator("strong")).toHaveText("3.00");
    await page.getByLabel("Relevance", { exact: false }).fill("2");
    await expect(page.getByText("Weighted score preview:").locator("strong")).toHaveText("2.50");

    await page.getByLabel("Recommendation", { exact: false }).selectOption("accept");
    await page.getByRole("button", { name: "Finalize" }).click();
    await expect.poll(() => savedPayload).not.toBeNull();
    expect(savedPayload).toMatchObject({
      rating: null,
      criterion_responses: { originality: 3, relevance: 2 },
      state: "final",
    });
  });

  test("purpose-designated fields render once and mirror canonical responses", async ({ page }) => {
    const assignmentId = "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd";
    let savedPayload: Record<string, unknown> | null = null;
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) => {
      if (new URL(route.request().url()).pathname.endsWith(`/${assignmentId}/evaluation`)) {
        savedPayload = route.request().postDataJSON();
        return route.fulfill({ contentType: "application/json", body: "{}" });
      }
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          total: 1,
          completed_count: 0,
          next_cursor: null,
          data: [{
            id: assignmentId,
            round_name: "Purpose review",
            proposal_title: "One recommendation",
            proposal_abstract: "Canonical typed criteria replace legacy controls.",
            speaker_name: "Hidden for blind review",
            rating_min: 1,
            rating_max: 5,
            recommendations: ["Accept", "Maybe", "Reject"],
            evaluator_guidance: "",
            evaluation_state: "not_started",
            comment_required: true,
            rating: null,
            recommendation: null,
            internal_comment: "",
            criteria: [
              { key: "quality", label: "Quality", response_type: "score", required: true, weight: 100, options: [], purpose: null },
              { key: "recommendation", label: "Recommendation", response_type: "select", required: true, weight: null, options: ["Accept", "Maybe", "Reject"], purpose: "recommendation" },
              { key: "comments", label: "Comments", response_type: "text", required: true, weight: null, options: [], purpose: "comment" },
            ],
            criterion_responses: {},
            blind_review: true,
            review_closes_at_ms: null,
            answers: [],
            hidden_answer_count: 0,
          }],
        }),
      });
    });

    await page.goto("/reviews");
    await page.getByRole("button", { name: "Open review" }).click();
    const recommendation = page.locator('select[name="criterion_recommendation"]');
    const comments = page.locator('textarea[name="criterion_comments"]');
    await expect(recommendation).toHaveCount(1);
    await expect(comments).toHaveCount(1);
    await expect(page.locator('textarea[name="internal_comment"]')).toHaveCount(0);
    await page.getByLabel("Quality", { exact: false }).fill("4");
    await recommendation.selectOption("Maybe");
    await comments.fill("Useful but intentionally tentative.");
    await page.getByRole("button", { name: "Finalize" }).click();
    await expect.poll(() => savedPayload).not.toBeNull();
    expect(savedPayload).toMatchObject({
      recommendation: "Maybe",
      internal_comment: "Useful but intentionally tentative.",
      criterion_responses: {
        quality: 4,
        recommendation: "Maybe",
        comments: "Useful but intentionally tentative.",
      },
      state: "final",
    });
  });

  test("legacy recommendations are humanized without changing submitted values", async ({ page }) => {
    const assignmentId = "dededede-dede-4ede-8ede-dededededede";
    let savedPayload: Record<string, unknown> | null = null;
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) => {
      if (new URL(route.request().url()).pathname.endsWith(`/${assignmentId}/evaluation`)) {
        savedPayload = route.request().postDataJSON();
        return route.fulfill({ contentType: "application/json", body: "{}" });
      }
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({
        total: 1, completed_count: 0, next_cursor: null, data: [{
          id: assignmentId, round_name: "Legacy review", proposal_title: "Stable values",
          proposal_abstract: "Presentation must not rewrite stored values.",
          speaker_name: "Hidden", rating_min: 1, rating_max: 5,
          recommendations: ["strong_accept", "accept", "reject", "strong_reject"],
          evaluator_guidance: "", evaluation_state: "not_started", comment_required: false,
          rating: null, recommendation: null, internal_comment: "",
          criteria: [{ key: "fit", label: "Track fit", response_type: "select", required: false, weight: null, options: ["Platform", "AI"], purpose: null }],
          criterion_responses: {}, blind_review: true, review_closes_at_ms: null,
          answers: [], hidden_answer_count: 0,
        }],
      }) });
    });

    await page.goto("/reviews");
    await page.getByRole("button", { name: "Open review" }).click();
    const recommendation = page.locator('select[name="recommendation"]');
    await expect(recommendation).toHaveCount(1);
    await expect(recommendation.locator('option[value="strong_accept"]')).toHaveText("Strong accept");
    const trackFit = page.locator('select[name="criterion_fit"]');
    await expect(trackFit).toBeVisible();
    await recommendation.selectOption("strong_accept");
    await trackFit.selectOption("Platform");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect.poll(() => savedPayload).not.toBeNull();
    expect(savedPayload).toMatchObject({ recommendation: "strong_accept" });
  });

  test("treats a fully completed docket as an archive, not active work", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          total: 1,
          completed_count: 1,
          next_cursor: null,
          data: [{
            id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            round_name: "Initial review",
            proposal_title: "Taming 40-Minute CI",
            proposal_abstract: "This abstract belongs in the full review, not the docket.",
            speaker_name: "Hidden for blind review",
            rating_min: 1,
            rating_max: 5,
            recommendations: ["accept", "reject"],
            evaluator_guidance: "",
            evaluation_state: "final",
            comment_required: false,
            rating: 4,
            recommendation: "accept",
            internal_comment: "Strong proposal.",
            criteria: [],
            criterion_responses: {},
            blind_review: true,
            review_closes_at_ms: null,
            answers: [],
            hidden_answer_count: 0,
          }],
        }),
      }));

    await page.goto("/reviews");

    await expect(page.getByText("All reviews complete")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Taming 40-Minute CI" })).toHaveCount(0);
    await page.getByLabel("Show finalized").check();
    await expect(page.getByRole("heading", { name: "Taming 40-Minute CI" })).toBeVisible();
    await expect(page.getByText("This abstract belongs in the full review, not the docket.")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "View", exact: true })).toHaveClass(/secondary/);
  });

  test("finalizing the open review keeps it on screen and leaves the queue reachable", async ({ page }) => {
    // Regression: the detail pane used to render from `visibleAssignments`, which drops
    // finalized reviews while "Show finalized" is unchecked (the default). Finalizing the
    // review being read therefore filtered it out of its own pane, and because the list
    // above stays suppressed while a selection is set, the entire page went blank until a
    // reload. The reviewer also lost the "Review finalized" confirmation for an
    // irreversible write. Asserting the queue count alone is not enough -- the docket bar
    // rendered correctly throughout the bug -- so this asserts what is actually on screen.
    const states: Record<string, string> = { one: "not_started", two: "not_started" };
    const assignment = (key: string, id: string, title: string) => ({
      id,
      round_name: "Initial review",
      proposal_title: title,
      proposal_abstract: `Abstract for ${title}.`,
      speaker_name: "Hidden for blind review",
      rating_min: 1,
      rating_max: 5,
      recommendations: ["accept", "reject"],
      evaluator_guidance: "",
      evaluation_state: states[key],
      comment_required: false,
      rating: null,
      recommendation: null,
      internal_comment: "",
      criteria: [],
      criterion_responses: {},
      blind_review: true,
      review_closes_at_ms: null,
      answers: [],
      hidden_answer_count: 0,
    });
    const firstId = "11111111-1111-4111-8111-111111111111";
    const secondId = "22222222-2222-4222-8222-222222222222";
    let releaseFinalize!: () => void;
    let releaseRefresh!: () => void;
    const finalizeGate = new Promise<void>((resolve) => { releaseFinalize = resolve; });
    const refreshGate = new Promise<void>((resolve) => { releaseRefresh = resolve; });
    let committed = false;

    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", async (route) => {
      if (new URL(route.request().url()).pathname.endsWith(`/${firstId}/evaluation`)) {
        await finalizeGate;
        states.one = String(route.request().postDataJSON().state);
        committed = true;
        return route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true }) });
      }
      if (committed) await refreshGate;
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          total: 2,
          completed_count: Object.values(states).filter((value) => value === "final").length,
          next_cursor: null,
          data: [
            assignment("one", firstId, "Taming 40-Minute CI"),
            assignment("two", secondId, "Scaling Postgres"),
          ],
        }),
      });
    });

    await page.goto("/reviews");
    await page.getByRole("button", { name: "Open review" }).first().click();
    await expect(page.getByRole("heading", { name: "Taming 40-Minute CI" })).toBeVisible();

    await page.getByLabel("Rating", { exact: false }).fill("4");
    await page.getByLabel("Recommendation", { exact: false }).selectOption("accept");
    await page.getByRole("button", { name: "Finalize" }).click();

    // Feedback starts with the mutation, not after the queue refresh.
    await expect(page.getByRole("button", { name: "Finalizing…" })).toBeDisabled();
    await expect(page.getByText("Finalizing review…")).toBeVisible();
    releaseFinalize();

    // The committed state is visible while the deliberately slow refresh is still blocked.
    await expect(page.getByRole("heading", { name: "Taming 40-Minute CI" })).toBeVisible();
    await expect(page.getByText("Review finalized")).toBeVisible();
    releaseRefresh();
    await expect(page.getByText("1 remaining")).toBeVisible();

    // And the rest of the queue is one click away, without a reload.
    await page.getByRole("button", { name: "Back to assigned proposals" }).click();
    await expect(page.getByRole("heading", { name: "Scaling Postgres" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Taming 40-Minute CI" })).toHaveCount(0);
  });
});
