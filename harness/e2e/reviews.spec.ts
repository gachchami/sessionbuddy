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

test.describe("reviewer workspace", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("an evaluator with no assignments gets a clean, useful empty state", async ({ page }) => {
    await page.route("**/api/v1/auth/session", (route) =>
      route.fulfill({ contentType: "application/json", body: reviewerSession }));
    await page.route("**/api/v1/evaluator/assignments**", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ data: [], total: 0, completed_count: 0, next_cursor: null }),
      }));

    await page.goto("/reviews");

    await expect(page.getByRole("heading", { name: "Reviews", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "No reviews assigned" })).toBeVisible();
    await expect(page.getByText("New assignments will appear here, and we’ll notify you by email.")).toBeVisible();
    await expect(page.getByText("Hide finalized")).toHaveCount(0);
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
    await page.route("**/api/v1/evaluator/assignments**", (route) =>
      route.fulfill({
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
              { key: "relevance", label: "Relevance", weight: 40 },
              { key: "quality", label: "Quality", weight: 35 },
              { key: "audience_value", label: "Audience value", weight: 25 },
            ],
            criterion_scores: {},
            blind_review: true,
            review_closes_at_ms: null,
            answers: [{ label: "Track", value: "Platform & Infra" }],
            hidden_answer_count: 3,
          }],
        }),
      }));

    await page.goto("/reviews");

    await expect(page.getByRole("heading", { name: "Building reliable platform tooling" })).toBeVisible();
    await expect(page.getByRole("group", { name: "Scorecard" })).toHaveCount(0);
    await expect(page.locator(".sb-sidebar")).toHaveCount(0);

    await page.getByRole("button", { name: "Open review" }).click();
    await expect(page.getByRole("group", { name: "Scorecard" })).toBeVisible();
    await expect(page.getByText("Full proposal (1 answer)")).toBeVisible();
    await expect(page.getByText("Platform & Infra")).not.toBeVisible();
    await page.getByText("Full proposal (1 answer)").click();
    await expect(page.getByText("Platform & Infra")).toBeVisible();
    await page.getByRole("button", { name: "Back to assigned proposals" }).click();
    await expect(page.getByRole("button", { name: "Open review" })).toBeVisible();
  });
});
