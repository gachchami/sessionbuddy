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

const SPEAKER_EMAIL = "priya@example.test";

const openCall = {
  form_id: "form-devflow",
  slug: "devflow-2027",
  accepting_submissions: true,
  availability_message: "Applications are open.",
  opens_at_ms: null,
  closes_at_ms: null,
  submission_limit: 2,
  submitted_count: 0,
  remaining_submissions: 2,
};

const publishedForm = {
  id: "form-devflow",
  event_id: "event-composer",
  version: 1,
  slug: "devflow-2027",
  welcome_text: "Tell us what you want to talk about.",
  fields: [
    { key: "speaker_name", type: "text", label: "Speaker name", required: true, help_text: "", placeholder: "", choices: [] },
    { key: "speaker_email", type: "email", label: "Email", required: true, help_text: "", placeholder: "", choices: [] },
    { key: "proposal_title", type: "text", label: "Proposal title", required: true, help_text: "", placeholder: "", choices: [] },
    { key: "proposal_abstract", type: "textarea", label: "Proposal abstract", required: true, help_text: "", placeholder: "", choices: [] },
    { key: "session_format", type: "select", label: "Session format", required: true, help_text: "", placeholder: "", choices: ["Talk", "Workshop"] },
    { key: "workshop_capacity", type: "text", label: "Workshop capacity", required: false, help_text: "Only asked for workshops.", placeholder: "", choices: [] },
  ],
  conditions: [
    { source_key: "session_format", operator: "equals", value: "Workshop", target_key: "workshop_capacity" },
  ],
  routing_rules: [],
  important_dates: [],
  co_speaker_limit: 1,
  submission_limit: 2,
  submissions_received: 0,
  accepting_submissions: true,
  availability_message: "Applications are open.",
  success_title: "Proposal received",
  success_message: "We sent a confirmation to your email address.",
  redirect_to_portal: true,
  accent_color: "#3159d9",
};

function portalPayload(overrides: Record<string, unknown> = {}) {
  return {
    event: {
      id: "event-composer",
      name: "DevFlow Conf 2027",
      starts_at_ms: Date.UTC(2027, 4, 3),
      ends_at_ms: Date.UTC(2027, 4, 4),
      time_zone: "America/New_York",
    },
    events: [{
      id: "event-composer",
      name: "DevFlow Conf 2027",
      starts_at_ms: Date.UTC(2027, 4, 3),
      ends_at_ms: Date.UTC(2027, 4, 4),
      time_zone: "America/New_York",
    }],
    event_speaker_id: "event-speaker-composer",
    public_profile_url: null,
    profile: {
      display_name: "Priya Raman",
      job_title: "Staff Engineer",
      company: "Example",
      location: "Bengaluru",
      biography: "Builds developer platforms.",
      links: [],
      version: 1,
    },
    tasks: [],
    submissions: [],
    notifications: [],
    open_call: openCall,
    completed_tasks: 0,
    total_tasks: 0,
    ...overrides,
  };
}

interface ComposerOptions {
  call?: Record<string, unknown> | null;
  form?: Record<string, unknown>;
  portal?: Record<string, unknown>;
  refreshCall?: Record<string, unknown> | null;
  /** Status codes returned by successive POSTs to the submissions endpoint. */
  submitStatuses?: number[];
  authStatus?: number;
  portalRefreshStatus?: number;
  uploadStates?: string[];
  uploadAuthorizationStatuses?: number[];
  uploadCompletionDelayMs?: number;
}

interface ComposerHarness {
  submissions: Array<{ body: Record<string, unknown>; idempotencyKey: string }>;
  uploadAuthorizations: number;
  uploadPuts: number;
  uploadCompletions: number;
}

async function servePortal(page: Page, options: ComposerOptions = {}): Promise<ComposerHarness> {
  // Docker's Worker origin is plain HTTP and is not a browser secure context,
  // so Web Crypto is unavailable there. Production is HTTPS; this narrow shim
  // keeps the upload contract test deterministic without weakening product code.
  await page.addInitScript(() => {
    if (!globalThis.crypto.subtle) {
      Object.defineProperty(globalThis.crypto, "subtle", {
        configurable: true,
        value: { digest: async () => new Uint8Array(32).buffer },
      });
    }
  });
  const harness: ComposerHarness = { submissions: [], uploadAuthorizations: 0, uploadPuts: 0, uploadCompletions: 0 };
  const statuses = [...(options.submitStatuses ?? [])];
  const uploadStates = [...(options.uploadStates ?? ["staged"])]
  const uploadAuthorizationStatuses = [...(options.uploadAuthorizationStatuses ?? [])];
  let portalLoads = 0;

  await page.route("**/speaker", (route) => route.fulfill({ contentType: "text/html", body: portalHtml }));
  await page.route("**/api/v1/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ authenticated: true, user_id: "user-priya", session_id: "session-a", csrf_token: "composer-csrf" }),
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    status: options.authStatus ?? 200,
    contentType: "application/json",
    body: JSON.stringify({
      authenticated: true, user_id: "user-priya", email: SPEAKER_EMAIL,
      display_name: "Priya Raman", csrf_token: "composer-csrf", active_role: "speaker",
      default_email_sender_name: "SessionBuddy", default_email_address: "no-reply@example.test",
    }),
  }));
  await page.route("**/api/v1/speaker/portal*", (route) => {
    portalLoads += 1;
    const call = portalLoads > 1 && options.refreshCall !== undefined
      ? options.refreshCall
      : options.call;
    return route.fulfill({
    status: portalLoads > 2 ? options.portalRefreshStatus ?? 200 : 200,
    contentType: "application/json",
    body: JSON.stringify(portalPayload(
      { ...(options.portal ?? {}), ...(call === undefined ? {} : { open_call: call }) },
    )),
  }); });
  await page.route("**/api/v1/speaker/resources", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/speaker/events/event-composer/assets", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/forms/devflow-2027", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ ...publishedForm, ...(options.form ?? {}) }),
  }));
  await page.route("**/api/v1/forms/devflow-2027/submissions", async (route) => {
    const request = route.request();
    harness.submissions.push({
      body: JSON.parse(request.postData() ?? "{}"),
      idempotencyKey: request.headers()["idempotency-key"],
    });
    const status = statuses.shift() ?? 201;
    if (status !== 201) {
      await route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "unavailable", message: "The service is unavailable." } }),
      });
      return;
    }
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        id: "submission-new", status: "submitted", editable: true, version: 1,
        submitted_at_ms: Date.now(), co_speakers: [],
        speaker_name: "Priya Raman", speaker_email: SPEAKER_EMAIL,
        proposal_title: "Shipping developer platforms", proposal_abstract: "A practical session.",
        answers: {},
      }),
    });
  });
  await page.route("**/api/v1/cfp/forms/form-devflow/upload-authorizations", async (route) => {
    harness.uploadAuthorizations += 1;
    const status = uploadAuthorizationStatuses.shift() ?? 201;
    if (status !== 201) {
      await route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ error: { code: "unavailable", message: "The service is unavailable." } }) });
      return;
    }
    const stagedId = `staged-${harness.uploadAuthorizations}`;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({
      staged_id: stagedId, upload_url: `/_uploads/${stagedId}`,
      method: "PUT", headers: {}, expires_at_ms: Number.MAX_SAFE_INTEGER,
    }) });
  });
  await page.route("**/_uploads/*", async (route) => {
    harness.uploadPuts += 1;
    await route.fulfill({ status: 200, body: "" });
  });
  await page.route("**/api/v1/cfp/forms/form-devflow/upload-authorizations/*/complete", async (route) => {
    harness.uploadCompletions += 1;
    if (options.uploadCompletionDelayMs) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, options.uploadCompletionDelayMs));
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ state: uploadStates.shift() ?? "staged" }) });
  });
  return harness;
}

async function openComposer(page: Page) {
  await page.goto("/speaker");
  await expect(page.locator("#status")).toHaveText("Speaker details are ready.");
  await page.getByRole("button", { name: /Submit a proposal/ }).click();
  await expect(page.locator("#proposal-composer-form")).toBeVisible();
}

async function fillRequiredFields(page: Page) {
  await page.locator("#proposal-field-proposal_title").fill("Shipping developer platforms");
  await page.locator("#proposal-field-proposal_abstract").fill("A practical session about platform teams.");
  await page.locator("#proposal-field-session_format").selectOption("Talk");
}

test.describe("speaker portal proposal composer", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("submits a proposal without leaving the portal", async ({ page }) => {
    const harness = await servePortal(page);
    await openComposer(page);
    await fillRequiredFields(page);

    await page.getByRole("button", { name: "Submit proposal" }).click();

    await expect(page.locator("#status")).toContainText("Proposal submitted. Receipt submission-new.");
    await expect(page.locator("#status")).toBeVisible();
    await expect(page.locator("#status")).toHaveClass(/success/);
    await expect(page).toHaveURL(/\/speaker$/);
    await expect(page.locator("#proposal-composer")).toBeHidden();
    expect(harness.submissions).toHaveLength(1);
    expect(harness.submissions[0].body.proposal_title).toBe("Shipping developer platforms");
    expect(harness.submissions[0].body.speaker_email).toBe(SPEAKER_EMAIL);
    expect(harness.submissions[0].body.answers).toMatchObject({ session_format: "Talk" });
  });

  test("keeps submission errors beside the form and focuses them", async ({ page }) => {
    await servePortal(page, { submitStatuses: [422] });
    await openComposer(page);
    await fillRequiredFields(page);

    await page.getByRole("button", { name: "Submit proposal" }).click();

    const summary = page.locator("#proposal-composer-error");
    await expect(summary).toBeVisible();
    await expect(summary).toContainText("service is unavailable");
    await expect(summary).toBeFocused();
  });

  test("highlights and focuses the first incomplete field", async ({ page }) => {
    await servePortal(page);
    await openComposer(page);

    await page.getByRole("button", { name: "Submit proposal" }).click();

    await expect(page.locator("#proposal-composer-error")).toContainText("Complete the highlighted field");
    await expect(page.locator("#proposal-field-proposal_title")).toHaveAttribute("aria-invalid", "true");
    await expect(page.locator("#proposal-field-proposal_title")).toBeFocused();
  });

  test("requires confirmation before the proposal trigger discards a draft", async ({ page }) => {
    await servePortal(page);
    await openComposer(page);
    await page.locator("#proposal-field-proposal_title").fill("A draft worth keeping");

    page.once("dialog", async (dialog) => {
      expect(dialog.message()).toContain("Discard this proposal draft?");
      await dialog.dismiss();
    });
    await page.getByRole("button", { name: /Submit a proposal/ }).click();
    await expect(page.locator("#proposal-composer-form")).toBeVisible();
    await expect(page.locator("#proposal-field-proposal_title")).toHaveValue("A draft worth keeping");
  });

  test("requires confirmation before switching events", async ({ page }) => {
    await servePortal(page, {
      portal: {
        events: [
          portalPayload().event,
          { id: "event-two", name: "Platform Days", starts_at_ms: Date.UTC(2027, 6, 1), ends_at_ms: Date.UTC(2027, 6, 2), time_zone: "UTC" },
        ],
      },
    });
    await openComposer(page);
    await page.locator("#proposal-field-proposal_title").fill("A draft worth keeping");

    page.once("dialog", (dialog) => dialog.dismiss());
    await page.getByRole("button", { name: /Platform Days/ }).click();

    await expect(page.locator("#proposal-composer-form")).toBeVisible();
    await expect(page.locator("#proposal-field-proposal_title")).toHaveValue("A draft worth keeping");
  });

  test("restores non-file draft fields after a reload", async ({ page }) => {
    await servePortal(page);
    await openComposer(page);
    await page.locator("#proposal-field-proposal_title").fill("Recovered proposal");
    page.once("dialog", (dialog) => dialog.accept());

    await page.reload();
    await page.getByRole("button", { name: /Submit a proposal/ }).click();

    await expect(page.locator("#proposal-field-proposal_title")).toHaveValue("Recovered proposal");
  });

  test("binds the proposal email to the signed-in account", async ({ page }) => {
    await servePortal(page);
    await page.goto("/speaker");
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");
    await page.evaluate(() => sessionStorage.setItem(
      "sessionbuddy:proposal-draft:event-composer:form-devflow",
      JSON.stringify({ values: { speaker_email: "stale-draft@example.test" }, coSpeakers: [] }),
    ));
    await page.getByRole("button", { name: /Submit a proposal/ }).click();

    const email = page.locator("#proposal-field-speaker_email");
    await expect(email).toHaveValue(SPEAKER_EMAIL);
    await expect(email).toHaveAttribute("readonly", "");
    await expect(page.locator("#proposal-composer")).toContainText(`Signed in as ${SPEAKER_EMAIL}.`);
  });

  test("conditional fields appear only when their condition holds", async ({ page }) => {
    await servePortal(page);
    await openComposer(page);

    const capacity = page.locator('[data-field-key="workshop_capacity"]');
    await expect(capacity).toBeHidden();

    await page.locator("#proposal-field-session_format").selectOption("Workshop");
    await expect(capacity).toBeVisible();

    await page.locator("#proposal-field-session_format").selectOption("Talk");
    await expect(capacity).toBeHidden();
    await expect(page.locator("#proposal-field-workshop_capacity")).toBeDisabled();
  });

  test("a retry after a lost response reuses the idempotency key", async ({ page }) => {
    // The server stores the proposal and replays it for a repeated key; a fresh
    // key on retry would create a duplicate proposal.
    const harness = await servePortal(page, { submitStatuses: [503, 201] });
    await openComposer(page);
    await fillRequiredFields(page);

    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#proposal-composer-error")).toBeVisible();
    await expect(page.locator("#proposal-composer-form")).toBeVisible();

    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#status")).toContainText("Proposal submitted.");

    expect(harness.submissions).toHaveLength(2);
    expect(harness.submissions[1].idempotencyKey).toBe(harness.submissions[0].idempotencyKey);
  });

  test("editing the proposal after a failure mints a new idempotency key", async ({ page }) => {
    // A changed payload under the original key is a 409 server-side, so the
    // client must move to a new key once the proposal differs.
    const harness = await servePortal(page, { submitStatuses: [503, 201] });
    await openComposer(page);
    await fillRequiredFields(page);

    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#proposal-composer-error")).toBeVisible();
    await expect(page.locator("#proposal-composer-form")).toBeVisible();

    await page.locator("#proposal-field-proposal_title").fill("A different proposal title");
    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#status")).toContainText("Proposal submitted.");

    expect(harness.submissions).toHaveLength(2);
    expect(harness.submissions[1].idempotencyKey).not.toBe(harness.submissions[0].idempotencyKey);
  });

  test("co-speakers are sent, and self-listing is refused before submitting", async ({ page }) => {
    const harness = await servePortal(page);
    await openComposer(page);
    await fillRequiredFields(page);

    await page.getByRole("button", { name: "Add co-speaker" }).click();
    await page.locator("[data-co-speaker-name]").fill("Sam Co");
    await page.locator("[data-co-speaker-email]").fill(SPEAKER_EMAIL);
    await page.getByRole("button", { name: "Submit proposal" }).click();

    await expect(page.locator("#proposal-composer-error")).toContainText("already listed on this proposal");
    await expect(page.locator("[data-co-speaker-email]")).toBeFocused();
    expect(harness.submissions).toHaveLength(0);

    await page.locator("[data-co-speaker-email]").fill("sam@example.test");
    await page.getByRole("button", { name: "Submit proposal" }).click();

    await expect(page.locator("#status")).toContainText("Proposal submitted.");
    expect(harness.submissions).toHaveLength(1);
    expect(harness.submissions[0].body.co_speakers).toEqual([
      { display_name: "Sam Co", email: "sam@example.test", role: "co_speaker" },
    ]);
  });

  test("the co-speaker limit is enforced in the composer", async ({ page }) => {
    await servePortal(page);
    await openComposer(page);

    await page.getByRole("button", { name: "Add co-speaker" }).click();
    await page.getByRole("button", { name: "Add co-speaker" }).click();

    await expect(page.locator(".co-speaker-row")).toHaveCount(1);
    await expect(page.locator("#proposal-composer-error")).toContainText("up to 1 co-speaker");
  });

  test("an exhausted submission limit explains itself instead of offering the form", async ({ page }) => {
    await servePortal(page, {
      call: {
        ...openCall,
        accepting_submissions: false,
        availability_message: "You have reached the proposal limit for this Call for Proposals.",
        submitted_count: 2,
        remaining_submissions: 0,
      },
    });
    await page.goto("/speaker");
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");

    await expect(page.getByRole("button", { name: /Submit a proposal/ })).toBeHidden();
    await expect(page.locator("#call-availability")).toContainText("reached the proposal limit");
  });

  test("a call with no published form offers nothing", async ({ page }) => {
    await servePortal(page, { call: null });
    await page.goto("/speaker");
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");

    await expect(page.getByRole("button", { name: /Submit a proposal/ })).toBeHidden();
    await expect(page.locator("#call-availability")).toBeHidden();
  });

  test("a call that closed since the portal loaded stops before the form opens", async ({ page }) => {
    await servePortal(page, {
      refreshCall: {
        ...openCall, accepting_submissions: false,
        availability_message: "Applications are closed.",
      },
      portal: { tasks: [{
        id: "task-profile", task_type: "profile", title: "Confirm profile", help_text: "",
        state: "open", version: 1, due_at_ms: null, form_fields: [],
      }] },
    });
    await page.route("**/api/v1/speaker/profile", (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify({ ok: true }),
    }));
    await page.goto("/speaker");
    await expect(page.locator("#status")).toHaveText("Speaker details are ready.");

    await page.getByRole("button", { name: /Submit a proposal/ }).click();

    await expect(page.locator("#proposal-composer")).toBeHidden();
    await expect(page.locator("#status")).toContainText("Applications are closed.");

    // Saving an unrelated task refreshes the portal from its stale open-call
    // snapshot. That incidental render must not resurrect the trigger.
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect(page.locator("#status")).toHaveText("Profile saved.");
    await expect(page.getByRole("button", { name: /Submit a proposal/ })).toBeHidden();
  });

  test("an exhausted per-speaker limit is rechecked before uploads or composing", async ({ page }) => {
    const harness = await servePortal(page, {
      refreshCall: {
        ...openCall, accepting_submissions: false, submitted_count: 2, remaining_submissions: 0,
        availability_message: "You have reached the proposal limit for this Call for Proposals.",
      },
    });
    await page.goto("/speaker");
    await page.getByRole("button", { name: /Submit a proposal/ }).click();

    await expect(page.locator("#proposal-composer")).toBeHidden();
    await expect(page.locator("#status")).toContainText("reached the proposal limit");
    expect(harness.uploadAuthorizations).toBe(0);
  });

  test("the remaining allowance is shown on the trigger", async ({ page }) => {
    await servePortal(page, {
      call: { ...openCall, submitted_count: 1, remaining_submissions: 1 },
    });
    await page.goto("/speaker");

    await expect(page.getByRole("button", { name: "Submit a proposal (1 left)" })).toBeVisible();
  });

  test("renders and serializes the complete dynamic field matrix", async ({ page }) => {
    const matrix = [
      ...publishedForm.fields,
      { key: "topics", type: "multiselect", label: "Topics", required: true, help_text: "Pick every relevant topic.", placeholder: "", choices: ["AI", "Web", "Data"] },
      { key: "recording_ok", type: "checkbox", label: "Recording permission", required: true, help_text: "Required for publication.", placeholder: "", choices: [] },
      { key: "portfolio", type: "url", label: "Portfolio", required: false, help_text: "", placeholder: "https://example.com/work", choices: [] },
      { key: "phone", type: "phone", label: "Phone", required: false, help_text: "", placeholder: "+1 555 0100", choices: [] },
      { key: "supporting", type: "file", label: "Supporting PDF", required: false, help_text: "PDF only.", placeholder: "", choices: [] },
      { key: "portrait", type: "image", label: "Portrait", required: false, help_text: "JPEG, PNG, or WebP.", placeholder: "", choices: [] },
      { key: "non_workshop_note", type: "text", label: "Non-workshop note", required: true, help_text: "Explain the format.", placeholder: "Why this format?", choices: [] },
    ];
    const harness = await servePortal(page, { form: {
      fields: matrix,
      conditions: [
        ...publishedForm.conditions,
        { source_key: "session_format", operator: "not_equals", value: "Workshop", target_key: "non_workshop_note" },
      ],
    } });
    await openComposer(page);

    const topics = page.locator("#proposal-field-topics");
    await expect(topics).toHaveAttribute("multiple", "");
    await expect(topics).toHaveAttribute("required", "");
    await expect(page.locator('[data-field-key="topics"]')).toContainText("Pick every relevant topic.");
    await expect(page.locator("#proposal-field-portfolio")).toHaveAttribute("type", "url");
    await expect(page.locator("#proposal-field-portfolio")).toHaveAttribute("placeholder", "https://example.com/work");
    await expect(page.locator("#proposal-field-phone")).toHaveAttribute("type", "tel");
    await expect(page.locator("#proposal-field-supporting")).toHaveAttribute("accept", "application/pdf");
    await expect(page.locator("#proposal-field-portrait")).toHaveAttribute("accept", "image/jpeg,image/png,image/webp");

    await fillRequiredFields(page);
    await topics.selectOption(["AI", "Data"]);
    await page.locator("#proposal-field-recording_ok").check();
    await page.locator("#proposal-field-portfolio").fill("https://example.com/work");
    await page.locator("#proposal-field-phone").fill("+1 555 0100");
    await page.locator("#proposal-field-non_workshop_note").fill("This is a talk.");
    await page.getByRole("button", { name: "Submit proposal" }).click();

    expect(harness.submissions[0].body.answers).toMatchObject({
      topics: ["AI", "Data"], recording_ok: true, portfolio: "https://example.com/work",
      phone: "+1 555 0100", non_workshop_note: "This is a talk.",
    });
    await expect(page.locator("#status")).toContainText("Proposal submitted.");
  });

  test("not-equals conditions update required state together with visibility", async ({ page }) => {
    await servePortal(page, { form: {
      fields: [...publishedForm.fields, { key: "talk_note", type: "text", label: "Talk note", required: true, help_text: "", placeholder: "Required for talks", choices: [] }],
      conditions: [{ source_key: "session_format", operator: "not_equals", value: "Workshop", target_key: "talk_note" }],
    } });
    await openComposer(page);
    const wrapper = page.locator('[data-field-key="talk_note"]');
    const input = page.locator("#proposal-field-talk_note");
    await expect(wrapper).toBeVisible();
    await expect(input).toHaveAttribute("required", "");
    await page.locator("#proposal-field-session_format").selectOption("Workshop");
    await expect(wrapper).toBeHidden();
    await expect(input).toBeDisabled();
    await expect(input).not.toHaveAttribute("required", "");
  });

  test("stages an upload once and retains its reference across submission retry", async ({ page }) => {
    const harness = await servePortal(page, {
      form: { fields: [...publishedForm.fields, { key: "supporting", type: "file", label: "Supporting PDF", required: true, help_text: "", placeholder: "", choices: [] }] },
      submitStatuses: [503, 201], uploadStates: ["staged"],
    });
    await openComposer(page);
    await fillRequiredFields(page);
    await page.locator("#proposal-field-supporting").setInputFiles({ name: "proposal.pdf", mimeType: "application/pdf", buffer: Buffer.from("pdf") });
    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#proposal-composer-error")).toContainText("Something went wrong on our side");
    expect(harness.uploadAuthorizations).toBe(1);
    expect(harness.uploadPuts).toBe(1);
    expect(harness.uploadCompletions).toBe(1);
    expect(harness.submissions).toHaveLength(1);
    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#status")).toContainText("Proposal submitted.");
    expect(harness.uploadAuthorizations).toBe(1);
    expect(harness.uploadPuts).toBe(1);
    expect(harness.submissions[1].body.answers).toMatchObject({ supporting: "staged:staged-1" });
  });

  test("keeps an earlier staged file when a later upload fails", async ({ page }) => {
    const harness = await servePortal(page, {
      form: { fields: [
        ...publishedForm.fields,
        { key: "supporting", type: "file", label: "Supporting PDF", required: true, help_text: "", placeholder: "", choices: [] },
        { key: "diagram", type: "image", label: "Diagram", required: true, help_text: "", placeholder: "", choices: [] },
      ] },
      uploadAuthorizationStatuses: [201, 503, 201],
      uploadStates: ["staged", "staged"],
    });
    await openComposer(page);
    await fillRequiredFields(page);
    await page.locator("#proposal-field-supporting").setInputFiles({ name: "proposal.pdf", mimeType: "application/pdf", buffer: Buffer.from("pdf") });
    await page.locator("#proposal-field-diagram").setInputFiles({ name: "diagram.png", mimeType: "image/png", buffer: Buffer.from("png") });

    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#proposal-composer-error")).toBeVisible();
    expect(harness.uploadAuthorizations).toBe(2);
    expect(harness.uploadPuts).toBe(1);

    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#status")).toContainText("Proposal submitted.");
    expect(harness.uploadAuthorizations).toBe(3);
    expect(harness.uploadPuts).toBe(2);
    expect(harness.uploadCompletions).toBe(2);
    expect(harness.submissions[0].body.answers).toMatchObject({
      supporting: "staged:staged-1",
      diagram: "staged:staged-3",
    });
  });

  test("keeps a staged reference when the composer DOM is replaced mid-submit", async ({ page }) => {
    const harness = await servePortal(page, {
      form: { fields: [...publishedForm.fields, { key: "supporting", type: "file", label: "Supporting PDF", required: true, help_text: "", placeholder: "", choices: [] }] },
      uploadStates: ["staged"],
      uploadCompletionDelayMs: 150,
    });
    await openComposer(page);
    await fillRequiredFields(page);
    await page.locator("#proposal-field-supporting").setInputFiles({ name: "proposal.pdf", mimeType: "application/pdf", buffer: Buffer.from("pdf") });
    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect.poll(() => harness.uploadPuts).toBe(1);

    await page.locator("#proposal-composer").evaluate((composer) => composer.replaceChildren());
    await expect.poll(() => harness.submissions.length).toBe(1);

    expect(harness.uploadAuthorizations).toBe(1);
    expect(harness.uploadPuts).toBe(1);
    expect(harness.uploadCompletions).toBe(1);
    expect(harness.submissions[0].body.answers).toMatchObject({ supporting: "staged:staged-1" });
  });

  test("warns before unloading a dirty composer", async ({ page }) => {
    await servePortal(page);
    await openComposer(page);
    await page.locator("#proposal-field-proposal_title").fill("Unsaved proposal");

    expect(await page.evaluate(() => {
      const event = new Event("beforeunload", { cancelable: true });
      return { dispatched: window.dispatchEvent(event), prevented: event.defaultPrevented };
    })).toEqual({ dispatched: false, prevented: true });
  });

  test("rejects a failed safety scan before submitting", async ({ page }) => {
    const harness = await servePortal(page, {
      form: { fields: [...publishedForm.fields, { key: "supporting", type: "file", label: "Supporting PDF", required: true, help_text: "", placeholder: "", choices: [] }] },
      uploadStates: ["rejected"],
    });
    await openComposer(page);
    await fillRequiredFields(page);
    await page.locator("#proposal-field-supporting").setInputFiles({ name: "proposal.pdf", mimeType: "application/pdf", buffer: Buffer.from("pdf") });
    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#proposal-composer-error")).toContainText("did not pass the safety check");
    expect(harness.submissions).toHaveLength(0);
  });

  for (const status of [401, 403, 409, 422, 429]) {
    test(`keeps the proposal available after a ${status} API response`, async ({ page }) => {
      const harness = await servePortal(page, { submitStatuses: [status] });
      await openComposer(page);
      await fillRequiredFields(page);
      await page.getByRole("button", { name: "Submit proposal" }).click();
      await expect(page.locator("#proposal-composer-error")).toBeVisible();
      await expect(page.locator("#proposal-composer-form")).toBeVisible();
      await expect(page.locator("#proposal-field-proposal_title")).toHaveValue("Shipping developer platforms");
      expect(harness.submissions).toHaveLength(1);
    });
  }

  test("allows the account email to be supplied when account-session lookup fails", async ({ page }) => {
    await servePortal(page, { authStatus: 500 });
    await openComposer(page);
    const email = page.locator("#proposal-field-speaker_email");
    await expect(email).toBeEditable();
    await expect(email).toHaveValue("");
  });

  test("does not misreport a committed proposal when portal refresh fails", async ({ page }) => {
    const harness = await servePortal(page, { portalRefreshStatus: 500 });
    await openComposer(page);
    await fillRequiredFields(page);
    await page.getByRole("button", { name: "Submit proposal" }).click();
    await expect(page.locator("#status")).toContainText("Proposal submitted (receipt submission-new)");
    await expect(page.locator("#status")).toContainText("could not be refreshed");
    expect(harness.submissions).toHaveLength(1);
  });

  test("the composer is accessible", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await servePortal(page);
    await openComposer(page);
    await page.getByRole("button", { name: "Add co-speaker" }).click();

    const results = await new AxeBuilder({ page }).include("#submissions").analyze();
    expect(results.violations).toEqual([]);
    await expect(page.locator("#submissions")).toHaveCSS("grid-column-start", "1");
    await expect(page.locator("#submissions")).toHaveCSS("grid-column-end", "-1");
    await expect(page.locator(".proposal-composer label").first()).toHaveCSS("font-size", "14px");
    await expect(page.getByRole("button", { name: "Submit proposal" })).toHaveCSS("background-color", "rgb(87, 50, 198)");
  });

  for (const width of [320, 390]) {
    test(`the composer fits a ${width}px phone`, async ({ page }) => {
      await page.setViewportSize({ width, height: 844 });
      await servePortal(page);
      await openComposer(page);
      await page.getByRole("button", { name: "Add co-speaker" }).click();

      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      expect(await page.locator(".co-speaker-row").evaluate((element) =>
        getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(1);
    });
  }
});
