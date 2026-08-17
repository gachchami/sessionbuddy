import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

// The organizer page is served from the checked-in assets and every call is stubbed, so
// these tests are about one thing: does the round the API receives describe the round the
// organizer built on screen. The string assertions in
// tests/release_readiness/test_evaluation_round_selection_ui.py can only prove the code
// still contains a listener; they cannot prove the payload survives the form, which is
// exactly what went wrong when a saved round arrived with zero assignments.
const root = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(root, "admin_submissions.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "admin_submissions.js"), "utf8")}</script></body>`);

const ALPHA = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa";
const BETA = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
// In the draft, but never rendered: the proposal table pages at 100.
const OFF_PAGE = "eeeeeeee-2222-4222-8222-eeeeeeeeeeee";
const REVIEWER = "dddddddd-3333-4333-8333-dddddddddddd";

const submission = (id: string, title: string) => ({
  id,
  proposal_title: title,
  proposal_abstract: "Abstract",
  speaker_name: "Speaker",
  speaker_email: "speaker@example.com",
  status: "submitted",
  submitted_at_ms: 1_700_000_000_000,
  version: 1,
  answers_json: "{}",
  form_schema_json: "{}",
  evaluation_round_id: null,
  evaluation_round_name: null,
});

const json = (body: unknown) => ({ contentType: "application/json", body: JSON.stringify(body) });

type Saved = { method: string; body: Record<string, unknown> };

async function organizerPage(page: import("@playwright/test").Page, options: { draft?: boolean; open?: boolean; failLedgerRefresh?: boolean } = {}) {
  const saves: Saved[] = [];
  let draftOpened = false;
  let openRoundVersion = 11;
  let ledgerReads = 0;
  await page.addInitScript(() => {
    if (!crypto.randomUUID) {
      Object.defineProperty(crypto, "randomUUID", {
        value: () => "11111111-1111-4111-8111-111111111111",
      });
    }
  });
  // Registered first on purpose: Playwright matches the most recently added route, and
  // "**/admin/events/event-a/submissions" would otherwise swallow the API call too.
  await page.route("**/admin/events/event-a/submissions", (route) =>
    route.fulfill({ contentType: "text/html", body: pageHtml }));
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill(json({ csrf_token: "browser-test-csrf", user_id: "organizer" })));
  await page.route("**/api/v1/admin/events/event-a", (route) =>
    route.fulfill(json({ id: "event-a", name: "DevFlow", time_zone: "Asia/Kolkata" })));
  await page.route("**/api/v1/admin/events/event-a/cfp", (route) =>
    route.fulfill(json({ published_form: null })));
  await page.route("**/api/v1/admin/events/event-a/submissions**", (route) =>
    route.fulfill(json({
      data: [submission(ALPHA, "Taming 40-Minute CI"), submission(BETA, "Scaling Postgres")],
      next_cursor: null,
      total: 2,
    })));
  await page.route("**/api/v1/admin/events/event-a/evaluators**", (route) =>
    route.fulfill(json({ data: [{ user_id: REVIEWER, display_name: "Sam Whitfield" }], pending: [] })));
  await page.route("**/api/v1/admin/events/event-a/evaluation-rounds/*/draft", async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill(json({
        name: "Prepared review", rating_min: 1, rating_max: 5,
        recommendations: ["accept", "reject"], evaluator_guidance: "", comment_required: false,
        criteria: [{ key: "relevance", label: "Relevance", response_type: "score", required: true, weight: 100, options: [] }],
        blind_review: true, review_opens_at_ms: null, review_closes_at_ms: null,
        // One proposal this page can show, one it cannot.
        submission_ids: [ALPHA, OFF_PAGE], evaluator_user_ids: [REVIEWER],
        assignments: [
          { submission_id: ALPHA, evaluator_user_id: REVIEWER },
          { submission_id: OFF_PAGE, evaluator_user_id: REVIEWER },
        ],
        assignment_strategy: "balanced", status: "draft", version: 7,
      }));
    }
    const body = route.request().postDataJSON();
    saves.push({ method: "PUT", body });
    return route.fulfill(json({
      id: "round-draft", event_id: "event-a", name: body.name, status: "draft",
      version: 8,
      assignment_count: body.assignments.length, evaluator_count: body.evaluator_user_ids.length,
      proposals: [],
    }));
  });
  await page.route("**/api/v1/admin/events/event-a/evaluation-rounds", async (route) => {
    if (route.request().method() === "GET") {
      ledgerReads += 1;
      if (options.failLedgerRefresh && ledgerReads > 1) {
        return route.fulfill({ status: 503, ...json({ error: { code: "unavailable", message: "Try again." } }) });
      }
      return route.fulfill(json({
        data: options.open
          ? [{ id: "round-open", event_id: "event-a", name: "Open review", status: "open", version: openRoundVersion, assignment_count: 1, evaluator_count: 1, proposals: [] }]
          : options.draft
          ? [{ id: "round-draft", event_id: "event-a", name: "Prepared review", status: draftOpened ? "open" : "draft", version: draftOpened ? 8 : 7, assignment_count: 2, evaluator_count: 1, proposals: [] }]
          : [],
      }));
    }
    const body = route.request().postDataJSON();
    saves.push({ method: "POST", body });
    return route.fulfill({
      status: 201,
      ...json({
        id: "round-new", event_id: "event-a", name: body.name, status: body.status,
        assignment_count: body.assignments.length, evaluator_count: body.evaluator_user_ids.length,
        proposals: [],
      }),
    });
  });
  await page.route("**/api/v1/admin/evaluation-rounds/round-draft/open", async (route) => {
    const body = route.request().postDataJSON();
    saves.push({
      method: "OPEN",
      body: { ...body, content_type: await route.request().headerValue("content-type") },
    });
    draftOpened = true;
    return route.fulfill(json({
      id: "round-draft", event_id: "event-a", name: "Prepared review", status: "open",
      version: 8, assignment_count: 2, evaluator_count: 1, proposals: [],
    }));
  });
  await page.route("**/api/v1/admin/evaluation-rounds/round-open/submissions", async (route) => {
    const body = route.request().postDataJSON();
    saves.push({ method: "ADD", body });
    openRoundVersion += 1;
    return route.fulfill(json({
      round_id: "round-open",
      submission_count: 1,
      assignment_count: 1,
    }));
  });

  await page.goto("/admin/events/event-a/submissions");
  await expect(page.getByRole("checkbox", { name: "Include Taming 40-Minute CI" })).toBeVisible();
  return saves;
}

async function addReviewer(page: import("@playwright/test").Page) {
  await page.locator("#round-disclosure > summary").click();
  await page.getByLabel("Reviewer email").fill("sam.reviewer@example.com");
  await page.getByRole("button", { name: "Add reviewer" }).click();
  await expect(page.locator("#reviewer-lookup-status")).toContainText("Sam Whitfield added");
}

test.describe("evaluation round assignment matrix", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  for (const order of ["proposals first", "reviewer first"] as const) {
    test(`the saved round carries the selection the organizer made, ${order}`, async ({ page }) => {
      const saves = await organizerPage(page);
      const proposals = ["Include Taming 40-Minute CI", "Include Scaling Postgres"];

      if (order === "proposals first") {
        for (const name of proposals) await page.getByRole("checkbox", { name }).check();
        await addReviewer(page);
      } else {
        // The order that used to lose everything: the reviewer's proposal checkboxes are
        // built from the proposal selection, so a reviewer added first had an empty matrix
        // and roundAssignments() returned [] -- which the API stores as "nobody reviews
        // anything" rather than treating as a request to generate one.
        await addReviewer(page);
        for (const name of proposals) await page.getByRole("checkbox", { name }).check();
      }

      await page.getByRole("button", { name: "Open evaluation round" }).click();

      await expect(page.locator("#status")).toContainText("2 assignments");
      expect(saves).toHaveLength(1);
      expect(saves[0].method).toBe("POST");
      expect(saves[0].body.submission_ids).toEqual([ALPHA, BETA]);
      expect(saves[0].body.evaluator_user_ids).toEqual([REVIEWER]);
      expect(saves[0].body.assignments).toEqual([
        { submission_id: ALPHA, evaluator_user_id: REVIEWER },
        { submission_id: BETA, evaluator_user_id: REVIEWER },
      ]);
    });
  }

  test("a round with nothing assigned is refused before it reaches the API", async ({ page }) => {
    const saves = await organizerPage(page);
    for (const name of ["Include Taming 40-Minute CI", "Include Scaling Postgres"]) {
      await page.getByRole("checkbox", { name }).check();
    }
    await addReviewer(page);
    // Unticking the matrix is the surviving way to build a round nobody can review. As a
    // draft it used to save silently, and the round then reported no reviewers and no
    // proposals anywhere the organizer could see it.
    for (const box of await page.locator("[data-pair-evaluator]").all()) await box.uncheck();
    await page.getByLabel("When this round starts").selectOption("draft");

    // No round is open, so the submit button keeps its "Open evaluation round" label even
    // for a draft save; the round_status select is what decides.
    await page.locator("#open-round").click();

    await expect(page.locator("#round-status")).toContainText("Assign at least one proposal to a reviewer");
    await expect(page.locator("#round-status")).toBeFocused();
    expect(saves).toHaveLength(0);
  });

  test("editing a draft keeps the proposals this page cannot show", async ({ page }) => {
    const saves = await organizerPage(page, { draft: true });

    await page.getByRole("button", { name: "Edit draft" }).click();
    await expect(page.locator("#selected-count")).toHaveText("2 selected");
    // Named, not silently carried: the organizer can see that the draft holds a proposal
    // this page has no row for.
    await expect(page.getByText("already in this draft, not on this page")).toBeVisible();

    await page.getByRole("button", { name: "Save draft changes" }).click();

    expect(saves).toHaveLength(1);
    expect(saves[0].method).toBe("PUT");
    expect(saves[0].body.expected_version).toBe(7);
    // Rebuilt from the rendered checkboxes alone, this payload used to arrive holding only
    // ALPHA -- and the round diff then deactivated the missing membership and revoked its
    // assignment, on a form the organizer had opened and saved without touching.
    expect(saves[0].body.submission_ids).toContain(OFF_PAGE);
    expect(saves[0].body.assignments).toContainEqual({ submission_id: OFF_PAGE, evaluator_user_id: REVIEWER });
  });

  test("Start review sends the guarded JSON contract and refreshes the ledger", async ({ page }) => {
    const saves = await organizerPage(page, { draft: true });

    await page.getByRole("button", { name: "Start review" }).click();

    await expect(page.locator("#status")).toContainText("is now open");
    const opened = saves.find((save) => save.method === "OPEN");
    expect(opened?.body).toMatchObject({
      expected_version: 7,
      content_type: "application/json",
    });
    await expect(page.getByRole("button", { name: "Start review" })).toHaveCount(0);
  });

  test("two sequential proposal additions use the refreshed round version", async ({ page }) => {
    const saves = await organizerPage(page, { open: true });
    const alpha = page.getByRole("checkbox", { name: "Include Taming 40-Minute CI" });
    const beta = page.getByRole("checkbox", { name: "Include Scaling Postgres" });

    await alpha.check();
    await page.getByRole("button", { name: "Add 1 selected proposal" }).click();
    await expect.poll(() => saves.filter((save) => save.method === "ADD").length).toBe(1);
    await alpha.uncheck();
    await beta.check();
    await page.getByRole("button", { name: "Add 1 selected proposal" }).click();
    await expect.poll(() => saves.filter((save) => save.method === "ADD").length).toBe(2);

    const additions = saves.filter((save) => save.method === "ADD");
    expect(additions.map((save) => save.body.expected_version)).toEqual([11, 12]);
    expect(additions.map((save) => save.body.submission_ids)).toEqual([[ALPHA], [BETA]]);
  });

  test("a committed addition with a failed refresh keeps its stale action disabled", async ({ page }) => {
    await organizerPage(page, { open: true, failLedgerRefresh: true });
    await page.getByRole("checkbox", { name: "Include Taming 40-Minute CI" }).check();
    const add = page.getByRole("button", { name: "Add 1 selected proposal" });
    await add.click();

    await expect(page.locator("#status")).toContainText("1 proposal added");
    await expect(page.locator("#status")).toContainText("Reload the page before adding more proposals");
    await expect(add).toBeDisabled();
    await page.getByRole("checkbox", { name: "Include Scaling Postgres" }).check();
    await expect(page.getByRole("button", { name: "Add 2 selected proposals" })).toBeDisabled();
    await expect(page.locator("#status")).not.toHaveClass(/error/);
  });

  test("round ledger exports confirm the filename and keep one quiet fallback line", async ({ page }) => {
    await organizerPage(page, { draft: true });
    await page.route("**/api/v1/admin/evaluation-rounds/round-draft/export.csv", (route) =>
      route.fulfill({
        contentType: "text/csv; charset=utf-8",
        headers: {
          "content-disposition": "attachment; filename=\"prepared-review-results.csv\"",
          "x-export-row-count": "2",
        },
        body: "submission_id,proposal_title\na,First\nb,Second\n",
      }));

    const ledger = page.locator(".round-ledger__row");
    await expect(ledger.getByRole("link", { name: "Export CSV" })).toBeVisible();
    await expect(ledger.getByRole("link", { name: "Export review details" })).toBeVisible();
    const fallback = ledger.locator(".round-ledger__fallbacks");
    await expect(fallback).toContainText("Download blocked?");
    await expect(fallback.getByRole("link", { name: "Direct results download" }))
      .toHaveAttribute("target", "_blank");

    const downloadPromise = page.waitForEvent("download");
    await ledger.getByRole("link", { name: "Export CSV" }).click();
    expect((await downloadPromise).suggestedFilename()).toBe("prepared-review-results.csv");
    await expect(page.locator("#status")).toContainText(
      "Download started: prepared-review-results.csv — 2 records.",
    );
  });
});
