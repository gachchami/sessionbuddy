import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
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
const publicEventMasthead = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/public_event_masthead.js"),
  "utf8",
);
const registrationTemplate = readFileSync(
  resolve(__dirname, "../../src/sessionbuddy/static/auth_link_confirm.html"),
  "utf8",
);
const publicCfpHtml = publicCfpTemplate
  .replace(/<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/, `<style>${productCss}</style>`)
  .replace(/<script src="\/app-shell\/assets\/api-client\.js\?v=\d+" defer><\/script>/, "")
  .replace(/<script src="\/public\/assets\/event-masthead\.js\?v=[^"]+" defer><\/script>/, "")
  .replace(/<script src="\/product\/assets\/public-cfp\.js\?v=\d+" defer><\/script>/, "")
  .replace("</body>", `<script>${apiClient}</script><script>${publicEventMasthead}</script><script>${publicCfpScript}</script></body>`);

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
  availability_state: "open",
  availability_boundary_at_ms: null,
  availability_boundary_kind: null,
  success_title: "Proposal received",
  success_message: "We sent a confirmation to your email address.",
  redirect_to_portal: true,
};

async function servePublicCfp(
  page: Page,
  authenticated: boolean,
  submissions: unknown[] = [],
  serverDraft: unknown = null,
  form: typeof publishedForm = publishedForm,
) {
  await page.route("**/cfp/mobile/responsive-conference*", (route) => route.fulfill({
    contentType: "text/html",
    body: publicCfpHtml,
  }));
  await page.route("**/speaker/proposals/responsive-conference/*", (route) => route.fulfill({
    contentType: "text/html",
    body: publicCfpHtml,
  }));
  await page.route("**/api/v1/forms/responsive-conference", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(form),
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill(authenticated
    ? {
        contentType: "application/json",
        body: JSON.stringify({
          authenticated: true,
          user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          email: "speaker@example.test",
          display_name: "Account Speaker",
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
    body: JSON.stringify(serverDraft),
  }));
}

async function expectWithinViewport(page: Page, selector: string, viewportWidth: number) {
  const box = await page.locator(selector).boundingBox();
  expect(box).toBeTruthy();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewportWidth + 1);
}

async function reachableAtItsCentre(target: import("@playwright/test").Locator) {
  return target.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const top = document.elementFromPoint(
      Math.round(rect.left + rect.width / 2),
      Math.round(rect.top + rect.height / 2),
    );
    return top === element || element.contains(top);
  });
}

async function parkBehindActionBar(
  target: import("@playwright/test").Locator,
  actionBar: import("@playwright/test").Locator,
) {
  await target.evaluate((element, actionElement) => {
    const targetRect = element.getBoundingClientRect();
    const actionRect = (actionElement as Element).getBoundingClientRect();
    const coveredY = actionRect.top + Math.min(actionRect.height / 2, targetRect.height / 2);
    window.scrollBy({ top: targetRect.top + targetRect.height / 2 - coveredY, behavior: "instant" });
  }, await actionBar.elementHandle());
}

test.describe("public CFP responsive design", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  for (const width of [320, 390]) {
    test(`signed-out proposal form and event header fit a ${width}px phone`, async ({ page }) => {
      const pageErrors: string[] = [];
      page.on("pageerror", (error) => pageErrors.push(error.message));
      await page.setViewportSize({ width, height: 844 });
      await servePublicCfp(page, false);
      await page.goto("/cfp/mobile/responsive-conference");

      await expect(page.locator("#status")).not.toHaveText("Loading…");
      expect(pageErrors).toEqual([]);
      await expect(page.locator(".public-event-masthead__name")).toHaveText("Responsive Conference 2026");
      await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
      await expect(page.locator(".public-event-masthead__mark img")).toBeVisible();
      await expect(page.locator(".public-event-masthead__cover")).toBeVisible();
      // Signed out is a preview, not a gate: the questions render so a speaker
      // can read them before deciding to create an account.
      await expect(page.locator("#proposal-card")).toBeVisible();
      await expect(page.getByRole("button", { name: "Save draft in this browser" })).toBeVisible();
      await expect(page.locator("#draft-storage-note")).toHaveText("Stored on this device for 30 minutes.");
      await expect(page.locator("#call-opens")).toHaveText("Open now");
      await expect(page.locator("#preview-note")).toBeVisible();
      await expect(page.locator("#field-proposal_title")).toBeVisible();
      const signup = page.getByRole("button", { name: "Email me a signup link" });
      await expect(signup).toHaveAttribute("aria-describedby", "cfp-signup-help");
      await expect(page.locator("#cfp-signup-help")).toHaveText("We’ll email a one-time link to create your account and sign you in.");
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await expectWithinViewport(page, "#event-public-header", width);
      await expectWithinViewport(page, "#sign-in-card", width);
      await expectWithinViewport(page, "#proposal-card", width);
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      expect(results.violations.filter(({ impact }) => impact === "critical" || impact === "serious")).toEqual([]);
    });
  }

  test("account identity wins after browser and server draft restoration", async ({ page }) => {
    await servePublicCfp(page, true, [], {
      version: 2,
      answers: { proposal_title: "Title from server draft" },
    });
    await page.addInitScript(() => {
      if (sessionStorage.getItem("identity-draft-seeded")) return;
      sessionStorage.setItem("identity-draft-seeded", "true");
      localStorage.setItem("sessionbuddy:cfp:responsive-conference:draft:new", JSON.stringify({
        schemaVersion: 1,
        formVersion: 1,
        answers: { speaker_name: "Stale Draft Name", proposal_title: "Title from browser draft" },
        coSpeakers: [],
        submissionId: null,
        fileNames: [],
        readyToSubmit: false,
        ownerEmail: "speaker@example.test",
        savedAt: Date.now(),
      }));
    });
    await page.goto("/cfp/mobile/responsive-conference");

    await expect(page.getByLabel("Speaker name")).toHaveValue("Account Speaker");
    await expect(page.locator('[name="speaker_email"]')).toHaveValue("speaker@example.test");
    await expect(page.getByLabel("Proposal title")).toHaveValue("Title from browser draft");

    // A published form version change makes the old browser draft ineligible,
    // exercising the independent server-draft restoration path on reload.
    await page.route("**/api/v1/forms/responsive-conference", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...publishedForm, version: 2 }),
    }));
    await page.reload();
    await expect(page.getByLabel("Speaker name")).toHaveValue("Account Speaker");
    await expect(page.locator('[name="speaker_email"]')).toHaveValue("speaker@example.test");
    await expect(page.getByLabel("Proposal title")).toHaveValue("Title from server draft");
    await page.getByLabel(/Proposal abstract/).fill("Identity fields no longer block validation.");
    await page.getByRole("button", { name: "Review proposal" }).click();
    await expect(page.getByRole("heading", { name: "Review your proposal" })).toBeVisible();
  });

  test("a primary submitter sees the closed CFP as the real edit blocker", async ({ page }) => {
    const submission = {
      id: "99999999-9999-4999-8999-999999999999",
      proposal_title: "Reliable systems",
      proposal_abstract: "An existing submitted proposal.",
      speaker_name: "Priya Raman",
      speaker_email: "speaker@example.test",
      status: "submitted",
      editable: true,
      answers: {},
      co_speakers: [],
    };
    await servePublicCfp(page, true, [submission], null, {
      ...publishedForm,
      accepting_submissions: false,
      availability_state: "closed",
      availability_message: "This call is closed.",
    });

    await page.goto(`/speaker/proposals/responsive-conference/${submission.id}`);

    await expect(page.locator("#status")).toContainText(
      "The call for proposals is closed, so this proposal is read-only.",
    );
    await expect(page.locator("#call-opens")).toHaveText("Opened immediately");
    await expect(page.locator("#call-opens").locator("xpath=../dt")).toHaveText("Opened");
    await expect(page.locator("#call-deadline").locator("xpath=../dt")).toHaveText("Closed");
    await expect(page.locator("#availability")).toContainText(
      "take precedence over dates in the organizer introduction",
    );
    await expect(page.locator("#status")).not.toContainText(
      "Only the primary submitter can make changes.",
    );
    await expect(page.getByLabel("Proposal title")).toBeDisabled();
  });

  test("a scheduled call names its opening time in the event time zone", async ({ page }) => {
    const opensAt = Date.UTC(2026, 7, 21, 13);
    await servePublicCfp(page, false, [], null, {
      ...publishedForm,
      opens_at_ms: opensAt,
      accepting_submissions: false,
      availability_state: "scheduled",
      availability_message: "Applications have not opened yet.",
      availability_boundary_at_ms: opensAt,
      availability_boundary_kind: "opens",
    });

    await page.goto("/cfp/mobile/responsive-conference");

    await expect(page.locator("#availability")).toContainText("Applications open");
    await expect(page.locator("#availability")).toContainText("EDT");
    await expect(page.locator("#call-opens")).toContainText("EDT");
  });

  test("an accepted proposal keeps answers read-only while allowing participant corrections", async ({ page }) => {
    const submission = {
      id: "88888888-8888-4888-8888-888888888888",
      proposal_title: "Accepted systems talk",
      proposal_abstract: "The version accepted by the organizers.",
      speaker_name: "Priya Raman",
      speaker_email: "speaker@example.test",
      status: "accepted",
      editable: false,
      can_manage_participants: true,
      version: 3,
      answers: {},
      co_speakers: [],
    };
    await servePublicCfp(page, true, [submission]);
    await page.addInitScript((submissionId) => {
      localStorage.setItem(`sessionbuddy:cfp:responsive-conference:draft:${submissionId}`, JSON.stringify({
        schemaVersion: 1,
        formVersion: 1,
        answers: {
          speaker_name: "Priya Raman",
          speaker_email: "speaker@example.test",
          proposal_title: "Accepted systems talk",
          proposal_abstract: "The version accepted by the organizers.",
        },
        coSpeakers: [{ display_name: "Marcus Okafor", email: "marcus@example.test" }],
        submissionId,
        fileNames: [],
        readyToSubmit: false,
        ownerEmail: "speaker@example.test",
        savedAt: Date.now(),
      }));
    }, submission.id);

    await page.goto(`/speaker/proposals/responsive-conference/${submission.id}`);

    await expect(page.locator("#status")).toContainText("Proposal answers are final");
    await expect(page.locator("#status")).toContainText("additional participants can still be managed");
    await expect(page.getByLabel("Proposal title")).toBeDisabled();
    await expect(page.getByRole("button", { name: "Add participant" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Save participants" })).toBeVisible();
    await expect(page.getByText("Marcus Okafor")).toHaveCount(0);
    await expect.poll(() => page.evaluate(
      (submissionId) => localStorage.getItem(`sessionbuddy:cfp:responsive-conference:draft:${submissionId}`),
      submission.id,
    )).toBeNull();
  });

  test("password sign-in and email signup send separate authentication payloads", async ({ page }) => {
    await servePublicCfp(page, false);
    let passwordPayload: unknown;
    let signupPayload: unknown;
    await page.route("**/api/v1/auth/password/sign-in", async (route) => {
      passwordPayload = route.request().postDataJSON();
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: { message: "Incorrect credentials" } }) });
    });
    await page.route("**/api/v1/auth/magic-links", async (route) => {
      signupPayload = route.request().postDataJSON();
      await route.fulfill({ status: 202, contentType: "application/json", body: "{}" });
    });
    await page.goto("/cfp/mobile/responsive-conference");
    await page.getByLabel("Email address").fill("new-speaker@example.test");
    await page.getByLabel("Password").fill("private returning password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect.poll(() => passwordPayload).toEqual({
      email: "new-speaker@example.test",
      password: "private returning password",
      redirect_path: "/cfp/mobile/responsive-conference",
    });

    await page.getByLabel("Password").fill("");
    await page.getByRole("button", { name: "Email me a signup link" }).click();
    await expect.poll(() => signupPayload).toEqual({
      email: "new-speaker@example.test",
      form_slug: "responsive-conference",
      redirect_path: "/cfp/mobile/responsive-conference",
    });
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
  });

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
    await expect(page.getByRole("button", { name: "Save draft to account" })).toBeVisible();
    await expect(page.locator("#draft-storage-note")).toHaveText("Available after you sign in again.");
    await expect(page.getByRole("link", { name: "My proposals" })).toHaveCount(0);
    await expect(page.locator("#proposal-workspace")).toBeHidden();
    const columns = await page.locator("#dynamic-fields").evaluate(
      (element) => getComputedStyle(element).gridTemplateColumns.split(" ").length,
    );
    expect(columns).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expectWithinViewport(page, "#proposal-card", width);
    await expectWithinViewport(page, "#review-proposal", width);
  });

  for (const viewport of [
    { name: "desktop", width: 1280, height: 620 },
    { name: "mobile", width: 390, height: 600 },
  ]) {
    test(`the sticky proposal actions never cover short fields on ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await servePublicCfp(page, true, [], null, {
        ...publishedForm,
        fields: [
          ...publishedForm.fields,
          {
            key: "audience_level",
            label: "Audience level",
            type: "select",
            required: true,
            choices: ["Beginner", "Intermediate", "Advanced"],
            help_text: null,
            placeholder: null,
          },
        ],
      });
      await page.goto("/cfp/mobile/responsive-conference");

      const actionBar = page.locator("#proposal-form > .actions");
      const audience = page.getByLabel("Audience level");
      await expect(actionBar).toBeVisible();
      // Preserve enough scroll range to park even the final participant
      // control underneath the sticky rail. Real CFPs get this space from
      // longer forms; this compact fixture adds it explicitly.
      await actionBar.evaluate((actions) => {
        const spacer = document.createElement("div");
        spacer.setAttribute("data-test-scroll-range", "true");
        spacer.style.height = "100vh";
        spacer.setAttribute("aria-hidden", "true");
        actions.before(spacer);
      });
      await expect.poll(() => page.evaluate(() =>
        Number.parseFloat(getComputedStyle(document.documentElement)
          .getPropertyValue("--cfp-action-bar-height")))).toBeGreaterThan(0);

      // Pre-patch evidence: both viewport cases failed for each of these two
      // controls (4 failures total) when the scroll-margin rule was removed.
      // Playwright considered the covered control visible, so its recovery
      // scroll was a no-op. Park each control under the bar first to pin that
      // exact geometry rather than merely asserting an ordinary visible state.
      const participant = page.getByRole("button", { name: "+ Add participant" });
      for (const target of [audience, participant]) {
        await parkBehindActionBar(target, actionBar);
        expect(await reachableAtItsCentre(target)).toBe(false);
        await target.scrollIntoViewIfNeeded();
        expect(await reachableAtItsCentre(target)).toBe(true);
      }
      await audience.selectOption("Intermediate");
    });
  }

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
      const key = "sessionbuddy:cfp:responsive-conference:draft:new";
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
