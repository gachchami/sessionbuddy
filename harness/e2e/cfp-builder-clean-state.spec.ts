import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * CFP-S1 eval, 2026-08-21 (observation 4): the Availability tab "became
 * persistently unclickable after repeated autosave/publish cycles". The tab was
 * never broken. A freshly loaded published form announced "Unpublished
 * changes", "Nothing changes publicly until you update the live CFP" and
 * "Unsaved live changes are backed up in this browser" while nothing had been
 * touched, so the organizer pressed "Update live CFP" to clear a change that
 * did not exist; every save collapses the builder to the summary, which is
 * what made the next click on the outline time out. The loop ran nine times.
 *
 * This spec loads a published form the way the worker serves it, with the
 * real shell, and checks that the clean state says it is clean, that the
 * state flips only on a real edit, and that the outline is operable again
 * right after re-entering the editor.
 */

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const userId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const staticDir = resolve(__dirname, "../../src/sessionbuddy/static");
const read = (name: string) => readFileSync(resolve(staticDir, name), "utf8");

const pageHtml = read("admin_programs.html")
  .replace(/<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/, `<style>${read("product.css")}</style>`)
  .replace(/<link rel="stylesheet" href="\/app-shell\/assets\/app-shell\.css\?v=\d+">/, `<style>${read("app_shell.css")}</style>`)
  .replace(/<script[^>]+src="[^"]+"[^>]*><\/script>/g, "")
  .replace(
    "</body>",
    `<script>${read("api_client.js")}</script>`
      + `<script>${read("app_shell.js")}</script>`
      + `<script>${read("public_event_masthead.js")}</script>`
      + `<script>${read("admin_programs.js")}</script></body>`,
  );

const session = {
  authenticated: true,
  user_id: userId,
  email: "organizer@example.test",
  display_name: "Ola Organizer",
  profile_complete: true,
  csrf_token: "browser-test-csrf",
  active_role: "organizer",
  account_roles: ["organizer"],
  organization_id: organizationId,
  organization_name: "Clean State Org",
  organization_access: [{ organization_id: organizationId, permissions: ["owner"] }],
  event_access: [{ event_id: eventId, assignments: [] }],
  event_id: eventId,
};

function publishedForm(version: number, overrides: Record<string, unknown> = {}) {
  return {
    id: "form-1",
    event_id: eventId,
    version,
    slug: "clean-state-conference",
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
    closes_at_ms: Date.UTC(2030, 3, 30, 23, 59),
    submission_limit: null,
    co_speaker_limit: 1,
    success_title: "Proposal received",
    success_message: "We sent a confirmation to your email address.",
    redirect_to_portal: true,
    confirmation_subject: "We received your proposal",
    confirmation_body: "Thank you for submitting.",
    availability_state: "open",
    availability_boundary_at_ms: Date.UTC(2030, 3, 30, 23, 59),
    ...overrides,
  };
}

async function serveBuilder(page: Page) {
  let live = publishedForm(1);
  let draft: ReturnType<typeof publishedForm> | null = null;
  let updates = 0;
  await page.route("**/api/v1/**", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
  await page.route(`**/admin/events/${eventId}/cfp`, (route) => route.fulfill({ contentType: "text/html", body: pageHtml }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => {
    if (route.request().method() === "PATCH") {
      updates += 1;
      const body = route.request().postDataJSON();
      live = publishedForm(live.version + 1, { success_title: body.success_title });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(live) });
    }
    return route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ organization_id: organizationId, event_id: eventId, event_name: "Clean State Conference", event_starts_at_ms: Date.UTC(2030, 5, 1, 9), published_form: live, draft_form: draft }),
    });
  });
  await page.route(`**/api/v1/admin/events/${eventId}/cfp/draft`, (route) => {
    const body = route.request().postDataJSON();
    draft = publishedForm((draft?.version || 0) + 1, { ...body, id: "draft-1", status: "draft" });
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(draft) });
  });
  await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: eventId, status: "active", time_zone: "UTC" }) }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda/tracks`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
  return { updateCount: () => updates, draft: () => draft };
}

test("an organizer draft is saved to the account and restored over the live form", async ({ page }) => {
  const server = await serveBuilder(page);
  await page.goto(`/admin/events/${eventId}/cfp`);
  await page.getByRole("button", { name: "Confirmation", exact: true }).click();
  await page.locator("#publish-form").getByLabel("Success heading").fill("Continue on another device");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.locator("#status")).toHaveText("Draft saved. You can continue on another device.");
  expect(server.draft()?.success_title).toBe("Continue on another device");

  await page.reload();
  await expect(page.locator("#cfp-autosave-state")).toHaveText("Draft restored from your account");
  await page.getByRole("button", { name: "Confirmation", exact: true }).click();
  await expect(page.locator("#publish-form").getByLabel("Success heading")).toHaveValue("Continue on another device");
});

test("unfinished custom configuration survives an account draft", async ({ page }) => {
  const server = await serveBuilder(page);
  await page.goto(`/admin/events/${eventId}/cfp`);
  await page.locator('.cfp-outline-item[data-selection="custom"]').click();
  await page.getByRole("button", { name: "+ Add custom question", exact: true }).click();
  const question = page.locator(".question-card[data-index]").last();
  await question.getByLabel("Answer format").selectOption("select");
  await question.getByLabel("Answer choices").fill("Only choice, Only choice");
  // Routing is not part of the compact outline; invoke the shipped control so
  // the draft still covers a half-configured rule without changing navigation.
  await page.locator("#add-routing").evaluate((button: HTMLButtonElement) => button.click());

  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.locator("#status")).toHaveText("Draft saved. You can continue on another device.");
  expect(server.draft()?.builder_state).toBeTruthy();

  await page.reload();
  await page.locator('.cfp-outline-item[data-selection="custom"]').click();
  await expect(page.locator(".question-card[data-index]").last().getByLabel("Answer choices"))
    .toHaveValue("Only choice, Only choice");
  await expect(page.locator("#routing-rules .routing-rule")).toHaveCount(1);
});

test("a published CFP opens clean and says so, and the outline is usable after every save", async ({ page }) => {
  const server = await serveBuilder(page);
  await page.goto(`/admin/events/${eventId}/cfp`);
  await expect(page.locator(".sb-topbar")).toBeVisible();

  // Nothing has been edited: the editor must not claim otherwise.
  const actionLabel = page.locator("#publish-action-label");
  await expect(actionLabel).toHaveText("Live");
  await expect(page.locator("#publish-result")).toHaveText("Your live form is up to date.");
  await expect(page.locator("#cfp-autosave-state")).not.toContainText("Unsaved");

  const availability = page.getByRole("button", { name: "Availability" });
  await availability.click();
  await expect(page.locator("#cfp-availability")).toBeVisible();

  // A real edit is the only thing that flips the label.
  await page.getByRole("button", { name: "Confirmation" }).click();
  await page.locator("#publish-form").getByLabel("Success heading").fill("Thanks, we have it");
  await expect(actionLabel).toHaveText("Unsaved changes");
  await expect(page.locator("#publish-result")).toHaveText("Nothing changes publicly until you update the live CFP.");

  // The sequence the eval agent ran: save, then go to Availability.
  await page.getByRole("button", { name: "Update live CFP" }).click();
  await expect(page.locator("#status")).toHaveText("Changes saved.");
  expect(server.updateCount()).toBe(1);
  // Saving returns to the summary by design, so the outline is hidden until
  // the editor is reopened -- and once it is, the tab has to work first time.
  await expect(page.locator("#publish-settings")).toBeHidden();
  await page.locator("#edit-cfp").click();
  await expect(actionLabel).toHaveText("Live");
  await availability.click({ timeout: 2000 });
  await expect(page.locator("#cfp-availability")).toBeVisible();

  // Leaving and coming back must not resurrect a phantom change either.
  // Wait beyond the debounce: before the timer was cancelled on save, it
  // recreated the just-deleted draft with the new live version and made the
  // reload dirty. Mobile timing exposed this intermittently in the full gate.
  await page.waitForTimeout(600);
  await expect.poll(() => page.evaluate(() =>
    Object.keys(sessionStorage).filter((key) => key.startsWith("sessionbuddy:cfp-draft:")),
  )).toEqual([]);
  await page.reload();
  await expect(page.locator(".sb-topbar")).toBeVisible();
  await expect(actionLabel).toHaveText("Live");
  await availability.click({ timeout: 2000 });
  await expect(page.locator("#cfp-availability")).toBeVisible();
});

test("the builder keeps publishing guidance, confirmation copy, preview, and reordering in one workspace", async ({ page }) => {
  await serveBuilder(page);
  await page.goto(`/admin/events/${eventId}/cfp`);

  await expect(page.locator("#cfp-readiness")).toContainText("Ready to publish");
  await page.getByRole("button", { name: "Confirmation", exact: true }).click();
  await expect(page.getByRole("group", { name: "On-screen confirmation" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Confirmation email" })).toBeVisible();

  const successHeading = page.locator("#publish-form").getByLabel("Success heading");
  await successHeading.fill("Your proposal is in");
  await page.getByRole("button", { name: "Preview form", exact: true }).click();
  await expect(page.locator("#cfp-selection-preview")).toContainText("Applicant view · preview only");
  await expect(page.locator("#cfp-selection-preview").getByRole("heading", { name: "Your proposal is in" })).toBeVisible();

  await page.getByRole("button", { name: "Back to editing", exact: true }).click();
  await page.getByRole("button", { name: /Proposal details/ }).click();
  await page.getByRole("button", { name: "Reorder questions" }).click();
  const formatOrder = page.locator(".field-order-row").filter({ hasText: "Session format" });
  const titleOrder = page.locator(".field-order-row").filter({ hasText: "Proposal title" });
  await formatOrder.dragTo(titleOrder, { targetPosition: { x: 40, y: 2 } });
  await expect(page.locator("#cfp-reorder-feedback")).toContainText("Session format moved.");
  await expect(page.locator(".field-order-row").first()).toContainText("Session format");
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await formatOrder.getByRole("button", { name: "Move Session format later" }).click();
  await expect(page.locator("#cfp-reorder-feedback")).toContainText("Session format moved later.");
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.locator("#cfp-reorder-feedback")).toContainText("Session format returned to its previous position.");
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toBeHidden();

  await page.getByRole("button", { name: /Custom questions/ }).click();
  await expect(page.locator("#field-order-list .field-order-row")).toHaveCount(0);
  await expect(page.locator("#field-order-panel")).not.toContainText("Standard proposal field");
  await page.getByRole("button", { name: "Add custom question" }).click();
  let customCards = page.locator("fieldset.question-card").filter({ has: page.locator('[name="field_key"]') }).filter({ hasNotText: "System field" });
  await expect(customCards).toHaveCount(1);
  await page.getByRole("button", { name: "Add a question after New question" }).click();
  await expect(customCards).toHaveCount(2);
  await page.getByRole("button", { name: "Remove New question" }).click();
  await expect(customCards).toHaveCount(1);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(customCards).toHaveCount(2);

  await page.getByRole("button", { name: "Availability", exact: true }).click();
  await expect(page.getByRole("link", { name: "Change in event settings" })).toHaveAttribute(
    "href",
    `/admin/events/${eventId}/settings#date-time`,
  );
  const opening = page.getByLabel("Opening time");
  const closing = page.getByLabel("Closing time");
  const immediately = page.getByRole("button", { name: "Opens immediately" });
  await expect(immediately).toBeDisabled();
  if ((page.viewportSize()?.width || 0) >= 768) {
    const [openingBox, closingBox] = await Promise.all([opening.boundingBox(), closing.boundingBox()]);
    expect(openingBox).not.toBeNull();
    expect(closingBox).not.toBeNull();
    expect(Math.abs(openingBox!.x - closingBox!.x)).toBeLessThan(1);
    expect(Math.abs(openingBox!.width - closingBox!.width)).toBeLessThan(1);
    expect(closingBox!.y).toBeGreaterThan(openingBox!.y);
  }
  await opening.fill("2029-01-02T09:30");
  await expect(page.getByRole("button", { name: "Open immediately instead" })).toBeEnabled();
  await page.getByRole("button", { name: "Open immediately instead" }).click();
  await expect(opening).toHaveValue("");
});
