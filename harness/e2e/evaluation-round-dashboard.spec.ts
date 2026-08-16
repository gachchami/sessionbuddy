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

  test("does not offer an attached unassigned reviewer in the add control", async ({ page }) => {
    await openDashboard(page, results({
      status: "open",
      available_evaluators: [{ user_id: "reviewer-a", display_name: "Sam Whitfield" }],
    }));

    await expect(page.getByText("No proposals assigned")).toBeVisible();
    await expect(page.locator("#round-add-evaluator option[value='reviewer-a']")).toHaveCount(0);
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

    await page.getByText("Individual reviews (1)").click();
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
    await page.route("**/api/v1/admin/evaluation-rounds/round-a/submissions/submission-a/decision", async (route) => {
      decisionRequests += 1;
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
    await page.getByRole("button", { name: "Confirm accepted" }).click();
    await expect(page.getByRole("button", { name: "Recording…" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await expect(page.locator(".decision-confirmation")).toHaveAttribute("aria-busy", "true");
    await page.getByRole("button", { name: "Recording…" }).dispatchEvent("click");
    expect(decisionRequests).toBe(1);

    releaseDecision();
    await expect(page.getByText("Decision recorded as accepted. Speaker email queued."))
      .toBeVisible();
    releaseRefresh();
    await expect(page.getByText("final decision in effect")).toBeVisible();
    expect(decisionRequests).toBe(1);
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
