import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

// ABS-S2 regression: switching a scorecard criterion away from Score used to anchor the
// "weights must total 100" custom validity to the FIRST weight input in the form -- which
// updateType() had just hidden. reportValidity() then returned false on an unfocusable
// control, so the submit died with no bubble, no status text, and only a "not focusable"
// warning in the console. These tests pin the corrected behavior: the error lands on a
// visible Score weight, a scorecard with no Score rows gets a scorecard-level error, and
// Dropdown choice problems are named on their own row before the payload leaves the
// browser. The server model is unchanged and still receives weight: null for non-Score
// criteria (tests/evaluation/test_evaluation.py covers that side).
const root = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(root, "admin_submissions.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "admin_submissions.js"), "utf8")}</script></body>`);

const ALPHA = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa";
const BETA = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";
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

async function organizerPage(page: import("@playwright/test").Page) {
  const saves: Saved[] = [];
  await page.addInitScript(() => {
    if (!crypto.randomUUID) {
      Object.defineProperty(crypto, "randomUUID", {
        value: () => "11111111-1111-4111-8111-111111111111",
      });
    }
  });
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
  await page.route("**/api/v1/admin/events/event-a/evaluation-rounds", async (route) => {
    if (route.request().method() === "GET") return route.fulfill(json({ data: [] }));
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

  await page.goto("/admin/events/event-a/submissions");
  await expect(page.getByRole("checkbox", { name: "Include Taming 40-Minute CI" })).toBeVisible();
  return saves;
}

// A round that is valid in every respect except the scorecard, so the scorecard is the
// only thing that can block the submit.
async function roundReadyToOpen(page: import("@playwright/test").Page) {
  const saves = await organizerPage(page);
  await page.locator("#round-disclosure > summary").click();
  for (const name of ["Include Taming 40-Minute CI", "Include Scaling Postgres"]) {
    await page.getByRole("checkbox", { name }).check();
  }
  await page.getByLabel("Reviewer email").fill("sam.reviewer@example.com");
  await page.getByRole("button", { name: "Add reviewer" }).click();
  await expect(page.locator("#reviewer-lookup-status")).toContainText("Sam Whitfield added");
  return saves;
}

const criterionRow = (page: import("@playwright/test").Page, index: number) =>
  page.locator("#criteria .criterion-row").nth(index);

async function setCriterionType(page: import("@playwright/test").Page, index: number, value: "score" | "select" | "text") {
  await criterionRow(page, index).locator('select[name="criterion_type"]').selectOption(value);
}

const validationMessage = (locator: import("@playwright/test").Locator) =>
  locator.evaluate((element: HTMLInputElement) => element.validationMessage);

// The core of the original defect: an invalid control the organizer cannot see. Any
// control that is both blocking submission and hidden reproduces the silent failure.
async function hiddenInvalidControls(page: import("@playwright/test").Page) {
  return page.evaluate(() =>
    [...document.querySelectorAll<HTMLInputElement>("#round-form input, #round-form select, #round-form textarea")]
      .filter((element) => element.willValidate && !element.checkValidity() && element.offsetParent === null)
      .map((element) => element.name || element.id));
}

test.describe("scorecard criterion types", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("switching the FIRST row to Dropdown anchors the total error to a visible Score weight", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    // The default scorecard is Relevance 40 / Quality 35 / Audience value 25. Making
    // Relevance a Dropdown is the natural first edit -- and exactly the one that used to
    // attach the error to Relevance's now-hidden weight input and block silently.
    await setCriterionType(page, 0, "select");
    await criterionRow(page, 0).locator('input[name="criterion_options"]').fill("Talk, Workshop");

    await page.getByRole("button", { name: "Open evaluation round" }).click();

    expect(saves).toHaveLength(0);
    const anchor = criterionRow(page, 1).locator('input[name="criterion_weight"]');
    await expect(anchor).toBeVisible();
    await expect.poll(() => validationMessage(anchor)).toContain("currently total 60");
    await expect(page.locator("#scorecard-total")).toHaveText("Score weights total 60 of 100.");
    expect(await hiddenInvalidControls(page)).toEqual([]);
  });

  test("switching a LATER row keeps the error on the first visible Score weight", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    await setCriterionType(page, 1, "text");

    await page.getByRole("button", { name: "Open evaluation round" }).click();

    expect(saves).toHaveLength(0);
    const anchor = criterionRow(page, 0).locator('input[name="criterion_weight"]');
    await expect(anchor).toBeVisible();
    await expect.poll(() => validationMessage(anchor)).toContain("currently total 65");
    expect(await hiddenInvalidControls(page)).toEqual([]);
  });

  test("a scorecard with no Score rows gets a scorecard-level error, not a hidden one", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    await setCriterionType(page, 0, "select");
    await criterionRow(page, 0).locator('input[name="criterion_options"]').fill("Talk, Workshop");
    await setCriterionType(page, 1, "text");
    await setCriterionType(page, 2, "text");
    await expect(page.locator("#scorecard-total")).toContainText("No Score criteria");

    await page.getByRole("button", { name: "Open evaluation round" }).click();

    expect(saves).toHaveLength(0);
    await expect(page.locator("#round-status")).toContainText("Keep at least one Score criterion");
    await expect(page.locator("#round-status")).toBeFocused();
    expect(await hiddenInvalidControls(page)).toEqual([]);
  });

  test("a Dropdown with one choice is refused on its own row before it reaches the API", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    await setCriterionType(page, 0, "select");
    await criterionRow(page, 0).locator('input[name="criterion_options"]').fill("OnlyChoice");
    // Rebalance the remaining Score weights so the choice list is the only failure.
    await criterionRow(page, 1).locator('input[name="criterion_weight"]').fill("75");

    await page.getByRole("button", { name: "Open evaluation round" }).click();

    expect(saves).toHaveLength(0);
    const choices = criterionRow(page, 0).locator('input[name="criterion_options"]');
    await expect(choices).toBeVisible();
    await expect.poll(() => validationMessage(choices)).toContain("2–20 comma-separated choices");
    expect(await hiddenInvalidControls(page)).toEqual([]);
  });

  test("Score + Dropdown + Free text submits null weights for the non-Score criteria", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    await criterionRow(page, 0).locator('input[name="criterion_weight"]').fill("100");
    await setCriterionType(page, 1, "select");
    await criterionRow(page, 1).locator('input[name="criterion_options"]').fill("Excellent, Good");
    await setCriterionType(page, 2, "text");

    await page.getByRole("button", { name: "Open evaluation round" }).click();

    await expect(page.locator("#status")).toContainText("opened with");
    expect(saves).toHaveLength(1);
    expect(saves[0].body.criteria).toEqual([
      { key: "relevance", label: "Relevance", response_type: "score", required: true, weight: 100, options: [] },
      { key: "quality", label: "Quality", response_type: "select", required: true, weight: null, options: ["Excellent", "Good"] },
      { key: "audience_value", label: "Audience value", response_type: "text", required: true, weight: null, options: [] },
    ]);
  });

  // Native constraint validation gates the submit EVENT, not just the submission: once a
  // control carries a custom validity message the browser refuses to fire submit, so
  // validateRound() -- the only code that recomputes those messages -- never runs again.
  // The form-level input listener clears only event.target, which suffices when the
  // invalid control is the one the organizer edits to fix it. These two recoveries break
  // that assumption, and both used to leave the round permanently unsubmittable.
  test("correcting the total on a DIFFERENT row than the anchor recovers the submit", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    await setCriterionType(page, 0, "select");
    await criterionRow(page, 0).locator('input[name="criterion_options"]').fill("Talk, Workshop");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    // Anchored on row 1 (the first visible Score row); rows 1 and 2 total 60.
    const anchor = criterionRow(page, 1).locator('input[name="criterion_weight"]');
    await expect.poll(() => validationMessage(anchor)).toContain("currently total 60");

    // Fix it from row 2 -- never touching the row holding the message.
    await criterionRow(page, 2).locator('input[name="criterion_weight"]').fill("65");
    await expect(page.locator("#scorecard-total")).toHaveText("Score weights total 100 of 100.");
    await page.getByRole("button", { name: "Open evaluation round" }).click();

    await expect(page.locator("#status")).toContainText("opened with");
    expect(saves).toHaveLength(1);
  });

  test("switching an invalid Dropdown to Free text recovers the submit", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    await setCriterionType(page, 0, "select");
    await criterionRow(page, 0).locator('input[name="criterion_options"]').fill("OnlyChoice");
    await criterionRow(page, 1).locator('input[name="criterion_weight"]').fill("75");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    const choices = criterionRow(page, 0).locator('input[name="criterion_options"]');
    await expect.poll(() => validationMessage(choices)).toContain("2–20 comma-separated choices");

    // Abandoning the Dropdown is a legitimate way to resolve its choice list. The options
    // input goes hidden carrying the message -- the original defect, in a new form.
    await setCriterionType(page, 0, "text");
    await expect(choices).toBeHidden();
    expect(await hiddenInvalidControls(page)).toEqual([]);
    await page.getByRole("button", { name: "Open evaluation round" }).click();

    await expect(page.locator("#status")).toContainText("opened with");
    expect(saves).toHaveLength(1);
  });

  // Removing a row is the third way to correct a weight total without touching the row
  // that carries the message. Adding one already recovers, because appendCriterionRow()
  // reaches updateType() and so clears the scorecard; the remove handler has no such path.
  test("removing the excess criterion recovers the submit", async ({ page }) => {
    const saves = await roundReadyToOpen(page);
    await page.getByRole("button", { name: "Add criterion" }).click();
    const added = criterionRow(page, 3);
    await added.locator('input[name="criterion_label"]').fill("Novelty");
    await added.locator('input[name="criterion_weight"]').fill("20");
    await expect(page.locator("#scorecard-total")).toHaveText("Score weights total 120 of 100.");

    await page.getByRole("button", { name: "Open evaluation round" }).click();
    const anchor = criterionRow(page, 0).locator('input[name="criterion_weight"]');
    await expect.poll(() => validationMessage(anchor)).toContain("currently total 120");

    // Back to 100 by deleting the row -- the anchor row is never edited.
    await added.getByRole("button", { name: "Remove" }).click();
    await expect(page.locator("#scorecard-total")).toHaveText("Score weights total 100 of 100.");
    await page.getByRole("button", { name: "Open evaluation round" }).click();

    await expect(page.locator("#status")).toContainText("opened with");
    expect(saves).toHaveLength(1);
  });

  test("the displayed total tracks weight edits and type switches immediately", async ({ page }) => {
    await organizerPage(page);
    await page.locator("#round-disclosure > summary").click();
    const total = page.locator("#scorecard-total");
    await expect(total).toHaveText("Score weights total 100 of 100.");

    await criterionRow(page, 0).locator('input[name="criterion_weight"]').fill("10");
    await expect(total).toHaveText("Score weights total 70 of 100.");

    await setCriterionType(page, 1, "text");
    await expect(total).toHaveText("Score weights total 35 of 100.");

    await setCriterionType(page, 0, "select");
    await setCriterionType(page, 2, "text");
    await expect(total).toContainText("No Score criteria");
  });
});
