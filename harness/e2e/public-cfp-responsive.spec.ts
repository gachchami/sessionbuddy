import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const publicCfpTemplate = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/public_cfp.html"),
  "utf8",
);
const productCss = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/product.css"),
  "utf8",
);
const apiClient = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/api_client.js"),
  "utf8",
);
const publicCfpScript = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/public_cfp.js"),
  "utf8",
);
const registrationTemplate = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/auth_link_confirm.html"),
  "utf8",
);
const publicCfpHtml = publicCfpTemplate
  .replace(/<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/, `<style>${productCss}</style>`)
  .replace(/<script src="\/app-shell\/assets\/api-client\.js\?v=\d+" defer><\/script>/, "")
  .replace(/<script src="\/product\/assets\/public-cfp\.js\?v=\d+" defer><\/script>/, "")
  .replace("</body>", `<script>${apiClient}</script><script>${publicCfpScript}</script></body>`);

const pixel =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1600' height='900'%3E%3Crect width='1600' height='900' fill='%233159d9'/%3E%3C/svg%3E";

const publishedForm = {
  id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
  event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  event_name: "Responsive Conference 2026",
  event_starts_at_ms: Date.UTC(2026, 9, 12),
  event_ends_at_ms: Date.UTC(2026, 9, 14),
  event_time_zone: "America/New_York",
  event_location: "New York City",
  event_delivery_mode: "hybrid",
  event_website_url: "https://conference.example.test",
  accent_color: "#3159d9",
  logo_url: pixel,
  cover_image_url: pixel,
  version: 1,
  slug: "responsive-conference",
  welcome_text: "Share a practical session with our engineering community.",
  description_html: null,
  important_dates: [],
  fields: [
    { key: "speaker_name", label: "Speaker name", type: "text", required: true, choices: [], help_text: null, placeholder: null },
    { key: "speaker_email", label: "Email", type: "email", required: true, choices: [], help_text: null, placeholder: null },
    { key: "proposal_title", label: "Proposal title", type: "text", required: true, choices: [], help_text: null, placeholder: null },
    { key: "proposal_abstract", label: "Proposal abstract", type: "textarea", required: true, choices: [], help_text: null, placeholder: null },
  ],
  conditions: [],
  routing_rules: [],
  opens_at_ms: null,
  closes_at_ms: null,
  submission_limit: null,
  co_speaker_limit: 1,
  submissions_received: 0,
  accepting_submissions: true,
  availability_message: "Applications are open.",
  success_title: "Proposal received",
  success_message: "We sent a confirmation to your email address.",
  redirect_to_portal: true,
};

async function servePublicCfp(page: Page, authenticated: boolean, submissions: unknown[] = []) {
  await page.route("**/cfp/mobile/responsive-conference*", (route) => route.fulfill({
    contentType: "text/html",
    body: publicCfpHtml,
  }));
  await page.route("**/api/v1/forms/responsive-conference", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(publishedForm),
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill(authenticated
    ? {
        contentType: "application/json",
        body: JSON.stringify({
          authenticated: true,
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          email: "speaker@example.test",
          csrf_token: "responsive-csrf",
        }),
      }
    : { status: 401, contentType: "application/json", body: JSON.stringify({ error: { message: "Authentication required" } }) }));
  await page.route("**/api/v1/forms/responsive-conference/submissions/mine", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: submissions }),
  }));
  await page.route("**/api/v1/forms/responsive-conference/draft", (route) => route.fulfill({
    contentType: "application/json",
    body: "null",
  }));
}

async function expectWithinViewport(page: Page, selector: string, viewportWidth: number) {
  const box = await page.locator(selector).boundingBox();
  expect(box).toBeTruthy();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewportWidth + 1);
}

test.describe("public CFP responsive design", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  for (const width of [320, 390]) {
    test(`registration gate and event header fit a ${width}px phone`, async ({ page }) => {
      const pageErrors: string[] = [];
      page.on("pageerror", (error) => pageErrors.push(error.message));
      await page.setViewportSize({ width, height: 844 });
      await servePublicCfp(page, false);
      await page.goto("/cfp/mobile/responsive-conference");

      await expect(page.locator("#status")).not.toHaveText("Loading…");
      expect(pageErrors).toEqual([]);
      await expect(page.getByRole("heading", { name: "Responsive Conference 2026" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
      await expect(page.locator("#event-logo")).toBeVisible();
      await expect(page.locator("#event-cover")).toBeVisible();
      await expect(page.locator("#proposal-card")).toBeHidden();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await expectWithinViewport(page, "#event-public-header", width);
      await expectWithinViewport(page, "#sign-in-card", width);
    });
  }

  test("verified speaker form becomes a single usable column on mobile", async ({ page }) => {
    const width = 390;
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.setViewportSize({ width, height: 844 });
    await servePublicCfp(page, true);
    await page.goto("/cfp/mobile/responsive-conference");

    await expect(page.locator("#status")).not.toHaveText("Loading…");
    expect(pageErrors).toEqual([]);
    await expect(page.locator("#proposal-card")).toBeVisible();
    await expect(page.locator("#sign-in-card")).toBeHidden();
    const columns = await page.locator("#dynamic-fields").evaluate(
      (element) => getComputedStyle(element).gridTemplateColumns.split(" ").length,
    );
    expect(columns).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expectWithinViewport(page, "#proposal-card", width);
    await expectWithinViewport(page, "#review-proposal", width);
  });

  test("long proposal titles and a long signed-in email never widen a phone layout", async ({ page }) => {
    const width = 390;
    await page.setViewportSize({ width, height: 844 });
    await servePublicCfp(page, true, [{
      id: "11111111-1111-4111-8111-111111111111",
      editable: true,
      speaker_name: "Speaker",
      speaker_email: "namohh.namaha+speaker123456789@gmail.com",
      proposal_title: "OpenAI, Anthropic, or agent frameworks: choose the right AI stack without overflowing a narrow pane",
      proposal_abstract: "Abstract",
      answers: {},
      co_speakers: [],
      version: 1,
    }]);
    await page.goto("/cfp/mobile/responsive-conference?submission_id=11111111-1111-4111-8111-111111111111");

    await expect(page.locator("#status")).not.toHaveText("Loading…");
    await expect(page.locator(".existing-submissions")).toHaveCount(0);
    const columns = await page.locator("#dynamic-fields").evaluate(
      (element) => getComputedStyle(element).gridTemplateColumns.split(" ").length,
    );
    expect(columns).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expectWithinViewport(page, "#proposal-card", width);
  });

  test("mobile review keeps the confirmation actions reachable", async ({ page }) => {
    const width = 390;
    await page.setViewportSize({ width, height: 600 });
    await servePublicCfp(page, true);
    await page.goto("/cfp/mobile/responsive-conference");
    await page.getByLabel("Speaker name").fill("Speaker");
    await page.getByLabel("Proposal title").fill("A mobile proposal");
    await page.getByLabel(/Proposal abstract/).fill("A concise abstract.");
    await page.getByRole("button", { name: "Review proposal" }).click();

    await expect(page.getByRole("button", { name: "Confirm submission" })).toBeVisible();
    await page.getByRole("button", { name: "Confirm submission" }).scrollIntoViewIfNeeded();
    await expectWithinViewport(page, "#submit-proposal", width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });

  test("proposal fields enforce API limits and recover an in-progress browser draft", async ({ page }) => {
    await servePublicCfp(page, true);
    await page.goto("/cfp/mobile/responsive-conference");

    const name = page.getByLabel("Speaker name");
    const email = page.locator('[name="speaker_email"]');
    const title = page.getByLabel("Proposal title");
    await expect(name).toHaveAttribute("maxlength", "200");
    await expect(email).toHaveAttribute("maxlength", "320");
    await expect(title).toHaveAttribute("maxlength", "200");
    await name.fill("Draft Speaker");
    await title.fill("Recovered proposal");
    await page.waitForTimeout(600);

    const recovery = await page.evaluate(() => {
      const key = "sessionbuddy:cfp:responsive-conference:draft";
      const saved = JSON.parse(localStorage.getItem(key) || "null");
      const unload = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(unload);
      return { title: saved?.answers?.proposal_title, unloadPrevented: unload.defaultPrevented };
    });
    expect(recovery).toEqual({ title: "Recovered proposal", unloadPrevented: true });
  });

  test("speaker registration is a focused single-column phone form", async ({ page }) => {
    const width = 390;
    const registrationFields = `
      <div class="auth-verified-email"><span>Verified email</span><strong>namohh.namaha+speaker123456789@gmail.com</strong></div>
      <div class="form-grid auth-registration-grid">
        <label>First name<input name="first_name" required></label>
        <label>Last name<input name="last_name" required></label>
        <label>Job title <span class="optional">Optional</span><input name="job_title"></label>
        <label>Company or team <span class="optional">Optional</span><input name="company"></label>
      </div>
      <label>Choose a password<input name="password" type="password" minlength="15" required></label>
      <label>Confirm password<input name="password_confirmation" type="password" minlength="15" required></label>`;
    const registrationHtml = registrationTemplate
      .replace(/<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/, `<style>${productCss}</style>`)
      .replace("__CONFIRM_PAGE_TITLE__", "Finish registration")
      .replace("__CONFIRM_HEADING__", "Create your speaker account")
      .replace("__CONFIRM_INTRO__", "Your email is verified. Add your details to finish registration.")
      .replace("__CONFIRM_ERROR__", "")
      .replace("__CONFIRM_ACTION__", "/auth/verify")
      .replace("__REGISTRATION_FIELDS__", registrationFields)
      .replace("__CONFIRM_BUTTON__", "Create account and continue");
    await page.setViewportSize({ width, height: 844 });
    await page.route("**/auth/verify", (route) => route.fulfill({ contentType: "text/html", body: registrationHtml }));
    await page.goto("/auth/verify");

    await expect(page.getByRole("heading", { name: "Create your speaker account" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Create account and continue" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expectWithinViewport(page, ".auth-registration-card", width);
  });
});
