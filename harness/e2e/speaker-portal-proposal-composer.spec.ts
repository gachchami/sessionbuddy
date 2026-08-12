import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const source = (name: string) => readFileSync(resolve(staticRoot, name), "utf8");
const workspaceHtml = source("public_cfp.html")
  .replace(/<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/, `<style>${source("product.css")}</style>`)
  .replace(/<script src="\/app-shell\/assets\/api-client\.js\?v=\d+" defer><\/script>/, "")
  .replace(/<script src="\/product\/assets\/public-cfp\.js\?v=\d+" defer><\/script>/, "")
  .replace("</body>", `<script>${source("api_client.js")}</script><script>${source("public_cfp.js")}</script></body>`);

const form = {
  id: "form-devflow", event_id: "event-devflow", event_name: "DevFlow 2027",
  version: 1, slug: "devflow-2027", welcome_text: "Tell us about your session.",
  fields: [
    { key: "speaker_name", type: "text", label: "Speaker name", required: true, choices: [], help_text: "", placeholder: "" },
    { key: "speaker_email", type: "email", label: "Email", required: true, choices: [], help_text: "", placeholder: "" },
    { key: "proposal_title", type: "text", label: "Proposal title", required: true, choices: [], help_text: "", placeholder: "" },
    { key: "proposal_abstract", type: "textarea", label: "Proposal abstract", required: true, choices: [], help_text: "", placeholder: "" },
    { key: "format", type: "select", label: "Format", required: true, choices: ["Talk", "Workshop"], help_text: "", placeholder: "" },
  ],
  conditions: [], important_dates: [], co_speaker_limit: 1, accepting_submissions: true,
  participant_roles: [
    { value: "co_speaker", label: "Co-speaker" },
    { value: "co_author", label: "Co-author" },
    { value: "moderator", label: "Moderator" },
    { value: "panelist", label: "Panelist" },
    { value: "other", label: "Other participant" },
  ],
  availability_message: "Applications are open.", success_message: "We sent a confirmation.",
  redirect_to_portal: true,
};

const proposal = (id: string, title: string, version = 1) => ({
  id, editable: true, speaker_name: "Priya Raman", speaker_email: "priya@example.test",
  proposal_title: title, proposal_abstract: `${title} abstract`, status: "submitted", version,
  submitted_at_ms: Date.UTC(2027, 7, 3),
  answers: { speaker_name: "Priya Raman", speaker_email: "priya@example.test", proposal_title: title, proposal_abstract: `${title} abstract`, format: "Talk" },
  co_speakers: [],
});

interface WorkspaceOptions {
  form?: Record<string, unknown>;
  draft?: Record<string, unknown> | null;
  patchStatuses?: number[];
  uploadAuthorizationStatuses?: number[];
  uploadCompletionStates?: string[];
}

async function serveWorkspace(page: Page, options: WorkspaceOptions = {}) {
  await page.addInitScript(() => {
    if (!globalThis.crypto.subtle) {
      Object.defineProperty(globalThis.crypto, "subtle", {
        configurable: true,
        value: { digest: async () => new Uint8Array(32).buffer },
      });
    }
  });
  const submissions = [proposal("proposal-a", "First proposal"), proposal("proposal-b", "Second proposal")];
  const patchStatuses = [...(options.patchStatuses || [])];
  const uploadAuthorizationStatuses = [...(options.uploadAuthorizationStatuses || [])];
  const uploadCompletionStates = [...(options.uploadCompletionStates || [])];
  const patches: Array<{ body: Record<string, unknown>; idempotencyKey: string }> = [];
  let uploadAuthorizations = 0;
  let uploadPuts = 0;
  let uploadCompletions = 0;
  let draft: Record<string, unknown> | null = options.draft ?? null;
  await page.route(/\/speaker\/proposals\/devflow-2027(?:\/.*)?$/, (route) => route.fulfill({ contentType: "text/html", body: workspaceHtml }));
  await page.route("**/cfp/devflow/devflow-2027", (route) => route.fulfill({ contentType: "text/html", body: workspaceHtml }));
  await page.route("**/api/v1/forms/devflow-2027", (route) => route.fulfill({ json: { ...form, ...(options.form || {}) } }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: {
    authenticated: true, user_id: "priya", email: "priya@example.test", display_name: "Priya Raman", csrf_token: "csrf",
  } }));
  await page.route("**/api/v1/forms/devflow-2027/submissions/mine", (route) => route.fulfill({ json: { data: submissions } }));
  await page.route("**/api/v1/forms/devflow-2027/draft", async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { answers: Record<string, unknown>; version: number };
      draft = { id: "draft", answers: body.answers, version: body.version + 1, updated_at_ms: Date.now() };
    }
    await route.fulfill({ json: draft });
  });
  await page.route("**/api/v1/forms/devflow-2027/submissions/proposal-*", async (route) => {
    const body = route.request().postDataJSON() as { proposal_title: string; proposal_abstract: string; answers: Record<string, unknown>; version: number };
    patches.push({ body, idempotencyKey: route.request().headers()["idempotency-key"] });
    const status = patchStatuses.shift() || 200;
    if (status !== 200) {
      await route.fulfill({ status, json: { error: { code: "unavailable", message: "The service is unavailable." } } });
      return;
    }
    const id = route.request().url().split("/").pop() || "";
    const index = submissions.findIndex((item) => item.id === id);
    submissions[index] = { ...submissions[index], proposal_title: body.proposal_title, proposal_abstract: body.proposal_abstract, answers: body.answers, version: body.version + 1 };
    await route.fulfill({ json: submissions[index] });
  });
  await page.route("**/api/v1/forms/devflow-2027/submissions", async (route) => {
    const body = route.request().postDataJSON() as { proposal_title: string; proposal_abstract: string; answers: Record<string, unknown> };
    const created = proposal("proposal-c", body.proposal_title);
    created.proposal_abstract = body.proposal_abstract;
    created.answers = body.answers;
    submissions.push(created);
    await route.fulfill({ status: 201, json: created });
  });
  await page.route("**/api/v1/forms/devflow-2027/submissions/proposal-*/withdraw", async (route) => {
    const id = route.request().url().split("/").at(-2) || "";
    const index = submissions.findIndex((item) => item.id === id);
    submissions[index] = { ...submissions[index], status: "withdrawn", editable: false };
    await route.fulfill({ json: submissions[index] });
  });
  await page.route("**/api/v1/cfp/forms/form-devflow/upload-authorizations", async (route) => {
    uploadAuthorizations += 1;
    const status = uploadAuthorizationStatuses.shift() || 201;
    if (status !== 201) {
      await route.fulfill({ status, json: { error: { code: "unavailable", message: "Upload unavailable." } } });
      return;
    }
    const stagedId = `staged-${uploadAuthorizations}`;
    await route.fulfill({ status: 201, json: {
      staged_id: stagedId, upload_url: `/_workspace-uploads/${stagedId}`,
      method: "PUT", headers: {}, expires_at_ms: Number.MAX_SAFE_INTEGER,
    } });
  });
  await page.route("**/_workspace-uploads/*", async (route) => {
    uploadPuts += 1;
    await route.fulfill({ status: 200, body: "" });
  });
  await page.route("**/api/v1/cfp/forms/form-devflow/upload-authorizations/*/complete", async (route) => {
    uploadCompletions += 1;
    await route.fulfill({ json: { state: uploadCompletionStates.shift() || "staged" } });
  });
  return {
    submissions, patches,
    counts: () => ({ uploadAuthorizations, uploadPuts, uploadCompletions }),
  };
}

test.describe("speaker proposal workspace", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("keeps exact proposal editors isolated by URL without a second proposal index", async ({ page }) => {
    await serveWorkspace(page);
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    await expect(page.getByRole("navigation", { name: "Your proposals" })).toBeHidden();
    await expect(page.getByLabel("Proposal abstract").locator("+ .character-counter")).toHaveText("23 of 5,000 characters");
    await page.getByLabel("Proposal title").fill("First proposal revised");
    await page.getByRole("button", { name: "Review proposal" }).click();
    await expect(page.locator("#status")).toHaveText("Review your changes, then select Save changes.");
    await expect(page.getByRole("button", { name: "Save changes" })).toBeVisible();
    await page.getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.locator("#status")).toContainText("Changes saved");
    await page.goto("/speaker/proposals/devflow-2027/proposal-b");
    await expect(page.getByLabel("Proposal title")).toHaveValue("Second proposal");
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    await expect(page.getByLabel("Proposal title")).toHaveValue("First proposal revised");
  });

  test("ignores an identity-only browser draft on a fresh proposal", async ({ page }) => {
    await serveWorkspace(page);
    await page.route("**/api/v1/forms/devflow-2027/draft", (route) => route.fulfill({
      status: 404,
      json: { error: { code: "not_found", message: "No draft exists." } },
    }));
    await page.addInitScript(() => {
      localStorage.setItem("sessionbuddy:cfp:devflow-2027:draft:new", JSON.stringify({
        schemaVersion: 1,
        formVersion: 1,
        answers: { speaker_name: "Priya Raman", speaker_email: "priya@example.test" },
        coSpeakers: [],
        submissionId: null,
        fileNames: [],
        readyToSubmit: false,
        ownerEmail: "priya@example.test",
        savedAt: Date.now(),
      }));
    });
    await page.goto("/cfp/devflow/devflow-2027");
    await expect(page.locator("#status")).toHaveText("Start a new proposal below.");
    await expect(page.locator("#status")).not.toContainText("restored from this browser");
    expect(await page.evaluate(() => localStorage.getItem("sessionbuddy:cfp:devflow-2027:draft:new"))).toBeNull();
  });

  test("warns about an existing title inline without blocking submission", async ({ page }) => {
    await serveWorkspace(page);
    await page.goto("/cfp/devflow/devflow-2027");
    await page.getByLabel("Proposal title").fill("First proposal");
    await page.getByLabel("Proposal abstract").focus();
    const warning = page.locator("#duplicate-title-warning");
    await expect(warning).toContainText("receipt proposal");
    await expect(warning).toContainText("You can still use this title.");
    await expect(warning.getByRole("link", { name: "Open that proposal" })).toHaveAttribute(
      "href", "/speaker/proposals/devflow-2027/proposal-a"
    );
    await expect(page.getByRole("button", { name: "Review proposal" })).toBeEnabled();
  });

  test("attributes an identical persisted draft to the server", async ({ page }) => {
    const draftAnswers = {
      speaker_name: "Priya Raman",
      speaker_email: "priya@example.test",
      proposal_title: "Server-saved proposal",
      proposal_abstract: "This draft is safely stored on the server.",
      format: "Talk",
    };
    await serveWorkspace(page, { draft: {
      id: "draft-a", answers: draftAnswers, version: 2, updated_at_ms: Date.now(),
    } });
    await page.addInitScript((answers) => {
      localStorage.setItem("sessionbuddy:cfp:devflow-2027:draft:new", JSON.stringify({
        schemaVersion: 1,
        formVersion: 1,
        answers,
        coSpeakers: [],
        submissionId: null,
        fileNames: [],
        readyToSubmit: false,
        ownerEmail: "priya@example.test",
        savedAt: Date.now(),
      }));
    }, draftAnswers);
    await page.goto("/cfp/devflow/devflow-2027");
    await expect(page.locator("#status")).toHaveText("Your saved draft has been restored.");
    await expect(page.locator("#status")).not.toContainText("from this browser");
    await expect(page.getByLabel("Proposal title")).toHaveValue("Server-saved proposal");
  });

  test("keeps withdrawal on the exact proposal page", async ({ page }) => {
    await serveWorkspace(page);
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Withdraw proposal" }).click();
    await expect(page.locator("#status")).toHaveText("Proposal withdrawn. It is now read-only.");
    await expect(page.getByRole("button", { name: "Withdraw proposal" })).toBeHidden();
    await expect(page.getByLabel("Proposal title")).toBeDisabled();
  });

  test("keeps conditional requirements and participant roles anchored to visible controls", async ({ page }) => {
    const harness = await serveWorkspace(page, { form: {
      co_speaker_limit: 2,
      fields: [
        ...form.fields,
        { key: "workshop_prerequisites", type: "textarea", label: "Workshop prerequisites", required: true, choices: [], help_text: "", placeholder: "" },
      ],
      conditions: [{ source_key: "format", operator: "equals", value: "Workshop", target_key: "workshop_prerequisites" }],
    } });
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    await expect(page.getByLabel("Workshop prerequisites")).toBeHidden();
    await page.getByLabel("Format").selectOption("Workshop");
    await expect(page.getByLabel("Workshop prerequisites")).toBeVisible();
    await page.getByRole("button", { name: "Review proposal" }).click();
    await expect(page.getByLabel("Workshop prerequisites")).toBeFocused();

    await page.getByLabel("Workshop prerequisites").fill("Bring a laptop.");
    await page.getByRole("button", { name: "Add participant" }).click();
    await page.getByRole("button", { name: "Add participant" }).click();
    const rows = page.locator(".co-speaker-row");
    await rows.nth(0).getByLabel("Name").fill("First collaborator");
    await rows.nth(0).getByLabel("Email").fill("collaborator@example.test");
    await rows.nth(0).getByLabel("Role").selectOption("moderator");
    await rows.nth(1).getByLabel("Name").fill("Second collaborator");
    await rows.nth(1).getByLabel("Email").fill("collaborator@example.test");
    await page.getByRole("button", { name: "Review proposal" }).click();
    await expect(rows.nth(1).getByLabel("Email")).toBeFocused();
    await expect(rows.nth(1).getByLabel("Email")).toHaveJSProperty(
      "validationMessage", "Each additional participant must use a different email."
    );
    await rows.nth(1).getByLabel("Email").fill("second@example.test");
    await page.getByRole("button", { name: "Review proposal" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    expect((harness.patches.at(-1)?.body.co_speakers as Array<{ role: string }>)[0].role).toBe("moderator");
  });

  test("keeps an unchanged failed save retry on one idempotency key", async ({ page }) => {
    const harness = await serveWorkspace(page, { patchStatuses: [503, 200] });
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    await page.getByLabel("Proposal title").fill("Retry-safe proposal");
    await page.getByRole("button", { name: "Review proposal" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("#status")).toHaveClass(/error/);
    await expect(page.getByLabel("Proposal title")).toHaveValue("Retry-safe proposal");
    await expect(page.locator("#status")).toBeFocused();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("#status")).toHaveText("Proposal updated successfully.");
    expect(harness.patches).toHaveLength(2);
    expect(harness.patches[0].idempotencyKey).toBe(harness.patches[1].idempotencyKey);
    expect(harness.patches[1].body.proposal_title).toBe("Retry-safe proposal");
  });

  test("retains a staged upload when a later file fails and only retries that file", async ({ page }) => {
    const uploadFields = [
      { key: "supporting", type: "file", label: "Supporting PDF", required: true, choices: [], help_text: "", placeholder: "" },
      { key: "diagram", type: "image", label: "Diagram", required: true, choices: [], help_text: "", placeholder: "" },
    ];
    const harness = await serveWorkspace(page, {
      form: { fields: [...form.fields, ...uploadFields] },
      uploadAuthorizationStatuses: [201, 503, 201],
      uploadCompletionStates: ["staged", "staged"],
    });
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    await page.getByLabel("Supporting PDF").setInputFiles({ name: "proposal.pdf", mimeType: "application/pdf", buffer: Buffer.from("pdf") });
    await page.getByLabel("Diagram").setInputFiles({ name: "diagram.png", mimeType: "image/png", buffer: Buffer.from("png") });
    await page.getByRole("button", { name: "Review proposal" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("#status")).toHaveClass(/error/);
    expect(harness.counts()).toEqual({ uploadAuthorizations: 2, uploadPuts: 1, uploadCompletions: 1 });
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("#status")).toHaveText("Proposal updated successfully.");
    expect(harness.counts()).toEqual({ uploadAuthorizations: 3, uploadPuts: 2, uploadCompletions: 2 });
    expect(harness.patches.at(-1)?.body.answers).toMatchObject({
      supporting: "staged:staged-1", diagram: "staged:staged-3",
    });
  });

  test("stops a rejected upload before proposal mutation", async ({ page }) => {
    const harness = await serveWorkspace(page, {
      form: { fields: [...form.fields, { key: "supporting", type: "file", label: "Supporting PDF", required: true, choices: [], help_text: "", placeholder: "" }] },
      uploadCompletionStates: ["rejected"],
    });
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    await page.getByLabel("Supporting PDF").setInputFiles({ name: "proposal.pdf", mimeType: "application/pdf", buffer: Buffer.from("pdf") });
    await page.getByRole("button", { name: "Review proposal" }).click();
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("#status")).toContainText("did not pass the safety check");
    expect(harness.patches).toHaveLength(0);
  });

  test("is accessible and fits on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await serveWorkspace(page);
    await page.goto("/speaker/proposals/devflow-2027/proposal-a");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    const results = await new AxeBuilder({ page }).include("#proposal-card").analyze();
    expect(results.violations.filter((item) => ["critical", "serious"].includes(item.impact || ""))).toEqual([]);
  });
});
