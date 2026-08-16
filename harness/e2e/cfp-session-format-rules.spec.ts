import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const staticDir = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(staticDir, "admin_programs.html"), "utf8")
  .replace(
    /<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/,
    `<style>${readFileSync(resolve(staticDir, "product.css"), "utf8")}</style>`,
  )
  .replace(/<link rel="stylesheet" href="\/app-shell\/assets\/app-shell\.css\?v=\d+">/, "")
  .replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, "")
  .replace(
    "</body>",
    `<script>${readFileSync(resolve(staticDir, "api_client.js"), "utf8")}</script>`
      + `<script>${readFileSync(resolve(staticDir, "admin_programs.js"), "utf8")}</script></body>`,
  );

async function serveBuilder(
  page: Page,
  publishedForm: Record<string, unknown> | null = null,
  tracks: string[] = [],
) {
  await page.route(`**/admin/events/${eventId}/cfp`, (route) => route.fulfill({
    contentType: "text/html",
    body: pageHtml,
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "organizer@example.test",
      csrf_token: "browser-test-csrf",
      organization_id: organizationId,
      event_id: eventId,
    }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      organization_id: organizationId,
      event_id: eventId,
      event_name: "Format Conference",
      event_starts_at_ms: Date.UTC(2030, 5, 1, 9),
      published_form: publishedForm,
    }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ id: eventId, status: "active", time_zone: "UTC" }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda/tracks`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: tracks.map((name, index) => ({ id: `track-${index}`, name })) }),
  }));
}

async function addCustomQuestion(
  page: Page,
  label: string,
  type: "text" | "textarea" | "select",
  options: { required?: boolean; choices?: string[] } = {},
) {
  await page.locator("#add-field").click();
  const card = page.locator('fieldset.question-card[data-index]').last();
  await card.getByRole("textbox", { name: /^Question/ }).fill(label);
  await card.getByLabel("Answer format").selectOption(type);
  if (options.required) await card.getByLabel("Required").check();
  if (options.choices) {
    await card.getByLabel("Answer choices").fill(options.choices.join(", "));
  }
  return card;
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`configures a conditional workshop field from valid session formats on ${viewport.name}`, async ({ page }) => {
    test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");
    await page.setViewportSize(viewport);
    await serveBuilder(page);
    await page.goto(`/admin/events/${eventId}/cfp`);

    await page.locator('.cfp-outline-item[data-selection="proposal"]').click();
    const formatCard = page.locator('fieldset.question-card[data-index]').filter({ hasText: "Session format" });
    await formatCard.locator("summary").click();
    await expect(formatCard.getByLabel("Session format label")).toHaveValue("Session format");
    await expect(formatCard.getByLabel("Question label")).toHaveCount(0);
    const formats = formatCard.locator('input[name="field_choices"][aria-label="Session format choices"]');
    await expect(formats).toHaveValue(/Workshop \(120 min\)/);

    await page.locator('.cfp-outline-item[data-selection="custom"]').click();
    await page.locator("#add-field").click();
    const custom = page.locator('fieldset.question-card[data-index]').filter({ hasText: "New question" }).last();
    await custom.getByRole("textbox", { name: /^Question/ }).fill("Workshop prerequisites");
    await custom.getByLabel("Answer format").selectOption("textarea");
    await expect(custom.locator(".question-advanced > div")).toBeHidden();
    await expect(custom.getByLabel("Question or event field")).toBeHidden();
    await custom.getByText("Display rules (optional)").click();
    const source = custom.getByLabel("Question or event field");
    const stableAnswer = custom.locator('select[name="condition_value"]');
    const answerHandle = await stableAnswer.elementHandle();
    expect(answerHandle).not.toBeNull();
    await expect(stableAnswer).toBeDisabled();
    // Match the evaluator's native-select contract: it supplies the visible
    // option text as the value rather than using Playwright's label matcher.
    await source.selectOption("Session format");
    expect(await source.locator("option:checked").getAttribute("data-source-key")).toBe("session_type");

    const answer = custom.locator('select[name="condition_value"]');
    await expect(answer).toHaveCount(1);
    expect(await answer.evaluate((element, original) => element === original, answerHandle)).toBe(true);
    await expect(answer).toBeFocused();
    await expect(answer.locator("option")).toHaveText([
      "Choose an answer",
      "Keynote (45 min)",
      "Talk (30 min)",
      "Lightning Talk (10 min)",
      "Workshop (120 min)",
      "Panel (45 min)",
    ]);
    await answer.selectOption({ label: "Workshop (120 min)" });
    await expect(answer).toHaveValue("Workshop (120 min)");

    await custom.getByLabel("Comparison").focus();
    await page.keyboard.press("Tab");
    await expect(answer).toBeFocused();

    const results = await new AxeBuilder({ page })
      .include(".question-advanced")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations.filter(({ impact }) => impact === "critical" || impact === "serious")).toEqual([]);

    await custom.getByRole("button", { name: "Done editing question" }).click();
    await expect(page.locator("#add-field")).toBeFocused();
    const savedCustom = page.locator('fieldset.question-card[data-index]')
      .filter({ hasText: "Workshop prerequisites" }).last();
    await expect(savedCustom.locator("details.question-editor")).not.toHaveAttribute("open", "");

    // Adding a question selects its full editor. Keep the primary add action
    // reachable there so long forms do not require returning to the outline.
    await expect(page.locator("#add-field")).toBeVisible();
    await page.locator("#add-field").click();
    const audience = page.locator('fieldset.question-card[data-index]').last();
    await audience.getByRole("textbox", { name: /^Question/ }).fill("Audience level");
    await audience.getByRole("button", { name: "Done editing question" }).click();
    await expect(page.locator("#add-field")).toBeVisible();
    await page.locator("#add-field").click();
    const takeaway = page.locator('fieldset.question-card[data-index]').last();
    await takeaway.getByRole("textbox", { name: /^Question/ }).fill("Key takeaway");
    const doneEditing = takeaway.getByRole("button", { name: "Done editing question" });
    await doneEditing.scrollIntoViewIfNeeded();
    const unobscured = await doneEditing.evaluate((element) => {
      const control = element.getBoundingClientRect();
      const actions = document.querySelector("#cfp-editor-actions")!.getBoundingClientRect();
      return control.bottom <= actions.top || control.top >= actions.bottom;
    });
    expect(unobscured, `expanded question controls must remain above the sticky action bar on ${viewport.name}`).toBe(true);
    await doneEditing.click();
    expect((await page.locator('input[name="field_label"]').evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value))).slice(-3)).toEqual([
      "Workshop prerequisites",
      "Audience level",
      "Key takeaway",
    ]);
    const previewToggle = page.locator("#preview-cfp");
    await expect(previewToggle).toHaveText("Preview form");
    await previewToggle.click();
    await expect(previewToggle).toHaveText("Back to editing");
    await expect(previewToggle).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: "Back to editing" })).toHaveCount(1);
    await previewToggle.click();
    await expect(previewToggle).toHaveText("Preview form");
    await expect(previewToggle).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test("builds and publishes the complete CFP-S1 evaluation form without retry loops", async ({ page }) => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.addInitScript(() => {
    if (!crypto.randomUUID) {
      Object.defineProperty(crypto, "randomUUID", {
        configurable: true,
        value: () => "ffffffff-ffff-4fff-8fff-ffffffffffff",
      });
    }
  });
  const tracks = ["AI Engineering", "Developer Experience", "Platform & Infra"];
  await serveBuilder(page, null, tracks);

  let publishedBody: Record<string, any> | null = null;
  await page.route(`**/api/v1/admin/events/${eventId}/cfp/publish`, async (route) => {
    publishedBody = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        event_id: eventId,
        version: 1,
        ...publishedBody,
      }),
    });
  });

  await page.goto(`/admin/events/${eventId}/cfp`);
  await page.locator("#cfp-description-editor").fill(
    "Share practical lessons with the DevFlow community.",
  );

  await page.locator('.cfp-outline-item[data-selection="proposal"]').click();
  const formatCard = page.locator('fieldset.question-card[data-index]').filter({ hasText: "Session format" });
  await formatCard.locator("summary").click();
  await formatCard.getByLabel("Session format choices").fill([
    "Keynote (45 min)",
    "Talk (30 min)",
    "Lightning Talk (10 min)",
    "Workshop (120 min)",
    "Panel (45 min)",
  ].join(", "));

  await page.locator('.cfp-outline-item[data-selection="custom"]').click();
  const takeaway = await addCustomQuestion(page, "Key takeaway", "textarea", { required: true });
  await takeaway.getByRole("button", { name: "Done editing question" }).click();

  const audience = await addCustomQuestion(page, "Audience level", "select", {
    required: true,
    choices: ["Beginner", "Intermediate", "Advanced"],
  });
  await audience.getByRole("button", { name: "Done editing question" }).click();

  const workshop = await addCustomQuestion(page, "Workshop prerequisites", "textarea", {
    required: true,
  });
  // The eval previously selected this hidden control repeatedly. A real user
  // must first expand the disclosure, after which both native selects must be
  // immediately actionable by their visible values.
  await workshop.getByText("Display rules (optional)").click();
  const source = workshop.getByLabel("Question or event field");
  await expect(source).toBeVisible();
  await source.selectOption("Session format");
  const conditionAnswer = workshop.locator('select[name="condition_value"]');
  await expect(conditionAnswer).toBeEnabled();
  await conditionAnswer.selectOption("Workshop (120 min)");
  await workshop.getByRole("button", { name: "Done editing question" }).click();

  await page.getByRole("button", { name: "Publish CFP" }).click();
  await expect(page.locator("#status")).toHaveText("CFP published.");

  expect(publishedBody).not.toBeNull();
  expect(publishedBody!.slug).toBe("format-conference");
  expect(publishedBody!.fields).toEqual(expect.arrayContaining([
    expect.objectContaining({
      key: "session_type",
      label: "Session format",
      required: true,
      choices: [
        "Keynote (45 min)",
        "Talk (30 min)",
        "Lightning Talk (10 min)",
        "Workshop (120 min)",
        "Panel (45 min)",
      ],
    }),
    expect.objectContaining({
      key: "track",
      label: "Track",
      required: true,
      choices: tracks,
    }),
    expect.objectContaining({ label: "Key takeaway", type: "textarea", required: true }),
    expect.objectContaining({
      label: "Audience level",
      type: "select",
      required: true,
      choices: ["Beginner", "Intermediate", "Advanced"],
    }),
    expect.objectContaining({ label: "Workshop prerequisites", type: "textarea", required: true }),
  ]));
  const workshopField = publishedBody!.fields.find(
    (field: Record<string, unknown>) => field.label === "Workshop prerequisites",
  );
  expect(publishedBody!.conditions).toContainEqual({
    source_key: "session_type",
    operator: "equals",
    value: "Workshop (120 min)",
    target_key: workshopField.key,
  });
});

test("keeps unresolved saved display rules visible and blocks publish until explicit clear", async ({ page }) => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");
  await page.setViewportSize({ width: 1280, height: 900 });
  await serveBuilder(page, {
    id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    event_id: eventId,
    version: 3,
    slug: "legacy-rules",
    welcome_text: "Propose a session.",
    description_html: "<p>Propose a session.</p>",
    important_dates: [],
    fields: [
      { key: "speaker_name", label: "Speaker name", type: "text", required: true, choices: [] },
      { key: "speaker_email", label: "Email", type: "email", required: true, choices: [] },
      { key: "proposal_title", label: "Proposal title", type: "text", required: true, choices: [] },
      { key: "proposal_abstract", label: "Proposal abstract", type: "textarea", required: true, choices: [] },
      { key: "session_type", label: "Session format", type: "select", required: true, choices: ["Talk (30 min)", "Workshop (120 min)"] },
      { key: "legacy_detail", label: "Legacy detail", type: "text", required: false, choices: [] },
      { key: "legacy_choice_detail", label: "Legacy choice detail", type: "text", required: false, choices: [] },
    ],
    conditions: [
      { source_key: "deleted_format", operator: "equals", value: "Workshop", target_key: "legacy_detail" },
      { source_key: "session_type", operator: "equals", value: "Panel (45 min)", target_key: "legacy_choice_detail" },
    ],
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
  });
  await page.goto(`/admin/events/${eventId}/cfp`);
  await page.locator('.cfp-outline-item[data-selection="custom"]').click();
  const legacy = page.getByRole("group", { name: "Custom field: legacy_detail", exact: true });
  await expect(legacy.getByRole("alert")).toContainText("no longer valid");
  const source = legacy.getByLabel("Question or event field");
  await expect(source).toHaveValue("deleted_format");
  await page.locator('#publish-cfp-action').click();
  expect(await source.evaluate((element) => !(element as HTMLSelectElement).checkValidity())).toBe(true);
  await source.selectOption("");
  await expect(legacy.getByRole("alert")).toBeHidden();
  await expect(legacy.locator('[name="condition_value"]')).toHaveValue("");

  const deletedChoice = page.getByRole("group", { name: "Custom field: legacy_choice_detail", exact: true });
  const answer = deletedChoice.locator('select[name="condition_value"]');
  await expect(answer).toHaveValue("Panel (45 min)");
  await expect(answer.locator("option:checked")).toContainText("Unavailable answer");
  await page.locator('#publish-cfp-action').click();
  expect(await answer.evaluate((element) => !(element as HTMLSelectElement).checkValidity())).toBe(true);
});
