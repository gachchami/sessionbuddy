import { expect, test, type Page } from "@playwright/test";

const userId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const programId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const assignmentId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";

const organizerSession = {
  authenticated: true,
  user_id: userId,
  email: "admin@example.com",
  display_name: "Admin User",
  profile_complete: true,
  csrf_token: "browser-test-csrf",
  organization_id: organizationId,
  event_id: eventId,
  organization_access: [{ organization_id: organizationId, roles: ["organization_admin"] }],
  event_access: [{ organization_id: organizationId, event_id: eventId, roles: ["event_admin", "evaluator"] }],
};

async function polyfillUuid(page: Page) {
  await page.addInitScript(() => {
    if (!crypto.randomUUID) {
      Object.defineProperty(crypto, "randomUUID", {
        configurable: true,
        value: () => "ffffffff-ffff-4fff-8fff-ffffffffffff",
      });
    }
    if (!crypto.subtle) {
      Object.defineProperty(crypto, "subtle", {
        configurable: true,
        value: { digest: async () => new Uint8Array(32).buffer },
      });
    }
  });
}

async function mockSession(page: Page, session = organizerSession) {
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
  await page.route("**/api/v1/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(session),
  }));
}

test.describe("form validation and workflow wiring", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("organization and account forms validate before writing", async ({ page }) => {
    await mockSession(page);
    let organizationName = "Example Events";
    let organizationWrites = 0;
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{ id: organizationId, name: organizationName, status: "active", version: 1 }] }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}`, async (route) => {
      organizationWrites += 1;
      organizationName = route.request().postDataJSON().name;
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: organizationId, name: organizationName, status: "active", version: 2 }) });
    });
    let profileWrites = 0;
    let profile = { email: "admin@example.com", display_name: "Admin User", job_title: null, company: null, time_zone: "UTC", version: 1 };
    await page.route("**/api/v1/account/profile", async (route) => {
      if (route.request().method() === "PATCH") {
        profileWrites += 1;
        profile = { ...profile, ...route.request().postDataJSON(), version: 2 };
      }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(profile) });
    });
    await page.goto("/account");
    const organizationForm = page.locator("#organization-settings form").first();
    await organizationForm.getByLabel("Organization name").fill("");
    await organizationForm.getByRole("button", { name: "Save organization" }).click();
    expect(organizationWrites).toBe(0);
    await organizationForm.getByLabel("Organization name").fill("Updated Events");
    await organizationForm.getByRole("button", { name: "Save organization" }).click();
    await expect.poll(() => organizationWrites).toBe(1);

    await page.getByLabel(/Time zone/).fill("Mars/Phobos");
    await page.getByRole("button", { name: "Save profile" }).click();
    expect(profileWrites).toBe(0);
    await page.getByLabel(/Time zone/).fill("Asia/Kolkata");
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect(page.getByRole("status").first()).toHaveText("Profile saved.");
    await expect.poll(() => profileWrites).toBe(1);
  });

  test("CFP builder rejects invalid availability and publishes a valid form", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    await page.route(`**/api/v1/admin/events/${eventId}/cfp`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        organization_id: organizationId,
        event_id: eventId,
        event_name: "Conference 2030",
        event_starts_at_ms: 1_901_000_000_000,
        published_form: null,
      }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, time_zone: "UTC" }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/tracks`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [] }),
    }));
    let publishWrites = 0;
    let publishedBody: Record<string, unknown> | null = null;
    await page.route(`**/api/v1/admin/events/${eventId}/cfp/publish`, async (route) => {
      publishWrites += 1;
      publishedBody = route.request().postDataJSON();
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ slug: "conference-2030" }) });
    });

    await page.goto(`/admin/events/${eventId}/cfp`);
    const form = page.locator("#publish-form");
    await form.getByLabel("Welcome message").fill("Share your best conference proposal.");
    await form.getByLabel("Public URL slug").fill("ab");
    await form.getByRole("button", { name: "Publish CFP" }).click();
    expect(publishWrites).toBe(0);
    await form.getByLabel("Public URL slug").fill("conference-2030");
    await form.getByLabel(/Opens/).fill("2030-03-20T10:00");
    await form.getByLabel(/Closes/).fill("2030-03-20T09:00");
    await form.getByRole("button", { name: "Publish CFP" }).click();
    expect(publishWrites).toBe(0);
    await form.getByLabel(/Closes/).fill("2030-03-21T09:00");
    await form.getByRole("button", { name: "Publish CFP" }).click();
    await expect(page.locator("#status")).toHaveText("Your CFP was published successfully.");
    expect(publishWrites).toBe(1);
    expect(publishedBody).toMatchObject({ slug: "conference-2030" });
  });

  test("review round and evaluator forms block incomplete payloads", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, time_zone: "UTC" }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/submissions`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        organization_id: organizationId,
        event_id: eventId,
        data: [{
          id: assignmentId,
          speaker_name: "Speaker",
          speaker_email: "speaker@example.com",
          proposal_title: "A proposal",
          proposal_abstract: "Abstract",
          status: "submitted",
          submitted_at_ms: 1_900_000_000_000,
          routed_category: null,
          routed_track: "Platform",
          routed_review_queue: "Technical",
          answers: { audience_level: "Intermediate" },
        }],
      }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/evaluators`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ user_id: userId, display_name: "Reviewer" }] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/evaluation-rounds/current`, (route) => route.fulfill({ contentType: "application/json", body: "null" }));
    let roundWrites = 0;
    await page.route(`**/api/v1/admin/events/${eventId}/evaluation-rounds`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) });
        return;
      }
      roundWrites += 1;
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: "11111111-1111-4111-8111-111111111111", name: "Initial review", assignment_count: 1, evaluator_count: 1 }) });
    });

    await page.goto(`/admin/events/${eventId}/submissions`);
    await page.getByRole("button", { name: "View details" }).click();
    const detail = page.getByRole("dialog", { name: "Submission details" });
    await expect(detail).toContainText("speaker@example.com");
    await expect(detail).toContainText("Platform");
    await expect(detail).toContainText("Audience level");
    await expect(detail).toContainText("Intermediate");
    await detail.getByRole("button", { name: "Close" }).click();
    await page.getByText("Open a new evaluation round", { exact: true }).click();
    await page.getByLabel("Minimum rating").fill("5");
    await page.getByLabel("Maximum rating").fill("5");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    expect(roundWrites).toBe(0);
    await page.getByLabel("Minimum rating").fill("1");
    await page.getByLabel("Maximum rating").fill("5");
    await page.getByLabel("Recommendations").fill("accept, accept");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    expect(roundWrites).toBe(0);
    await page.getByLabel("Recommendations").fill("accept, reject");
    await page.getByRole("button", { name: "Open evaluation round" }).click();
    await expect.poll(() => roundWrites).toBe(1);

    const assignment = {
      id: assignmentId, round_id: "11111111-1111-4111-8111-111111111111", round_name: "Initial review",
      submission_id: assignmentId, proposal_title: "A proposal", proposal_abstract: "Abstract", speaker_name: "Speaker",
      rating_min: 1, rating_max: 5, recommendations: ["accept", "reject"], evaluator_guidance: "", evaluation_state: "not_started",
      criteria: [], criterion_scores: {}, blind_review: false, review_closes_at_ms: null,
      rating: null, recommendation: null, internal_comment: "",
    };
    await page.route("**/api/v1/evaluator/assignments*", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [assignment], next_cursor: null, total: 1, completed_count: 0 }) }));
    let evaluationWrites = 0;
    let conflictWrites = 0;
    await page.route(`**/api/v1/evaluator/assignments/${assignmentId}/evaluation`, async (route) => { evaluationWrites += 1; await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) }); });
    await page.route(`**/api/v1/evaluator/assignments/${assignmentId}/conflict`, async (route) => { conflictWrites += 1; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({}) }); });
    await page.goto("/reviews");
    await page.getByRole("button", { name: "Save draft" }).click();
    expect(evaluationWrites).toBe(0);
    await page.getByLabel("Rating").fill("4");
    await page.getByLabel("Recommendation").selectOption("accept");
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect.poll(() => evaluationWrites).toBe(1);
    await page.getByText("Declare a conflict of interest").click();
    await page.getByRole("button", { name: "Remove my assignment" }).click();
    expect(conflictWrites).toBe(0);
    await page.getByLabel("Explanation").fill("Same employer");
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Remove my assignment" }).click();
    await expect.poll(() => conflictWrites).toBe(1);
  });

  test("workspace resource, task, and token forms enforce their contracts", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    let resourceWrites = 0;
    let taskWrites = 0;
    let tokenWrites = 0;
    await page.route(`**/api/v1/admin/events/${eventId}/resources`, async (route) => {
      if (route.request().method() === "POST") resourceWrites += 1;
      await route.fulfill({ status: route.request().method() === "POST" ? 201 : 200, contentType: "application/json", body: JSON.stringify(route.request().method() === "POST" ? {} : { data: [] }) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: eventId, time_zone: "Asia/Kolkata" }),
    }));
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-targets`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ event_speaker_id: assignmentId, display_name: "Speaker", proposal_title: "A proposal", selection_status: "accepted" }] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-tasks`, async (route) => { taskWrites += 1; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({}) }); });
    await page.route(`**/api/v1/admin/events/${eventId}/integrations/accelevents/tokens`, async (route) => { tokenWrites += 1; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ token: "one-time-token" }) }); });

    await page.goto(`/admin/events/${eventId}/workspace`);
    await page.getByText("Add a resource", { exact: true }).click();
    const resource = page.locator("#resource-form");
    await resource.getByLabel("Title").fill("Speaker guide");
    await resource.getByLabel(/Approved embed URL/).fill("https://evil.example/document");
    await resource.getByRole("button", { name: "Publish resource" }).click();
    expect(resourceWrites).toBe(0);
    await resource.getByLabel(/Approved embed URL/).fill("https://docs.google.com/document/d/example");
    await resource.getByRole("button", { name: "Publish resource" }).click();
    await expect.poll(() => resourceWrites).toBe(1);

    await page.getByText("Assign a task", { exact: true }).click();
    const task = page.locator("#task-form");
    await task.getByLabel("Speaker").selectOption(assignmentId);
    await task.getByLabel("Task title").fill("Confirm requirements");
    await task.getByText("Required", { exact: true }).click();
    await task.getByRole("button", { name: "Assign task" }).click();
    expect(taskWrites).toBe(0);
    await task.getByLabel("Question label").fill("Dietary requirements");
    await task.getByRole("button", { name: "Assign task" }).click();
    await expect.poll(() => taskWrites).toBe(1);

    await page.getByText("Connect Accelevents", { exact: true }).click();
    await page.getByLabel("Token label").fill("Accelevents demo");
    await page.getByRole("button", { name: "Generate token" }).click();
    await expect(page.getByText("one-time-token")).toBeVisible();
    expect(tokenWrites).toBe(1);
  });

  test("onboarding filters refresh the server-backed snapshot", async ({ page }) => {
    await mockSession(page);
    let dashboardReads = 0;
    let lastQuery = "";
    await page.route(`**/api/v1/admin/events/${eventId}/onboarding*`, async (route) => {
      dashboardReads += 1;
      lastQuery = new URL(route.request().url()).search;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          event_name: "Conference",
          time_zone: "Asia/Kolkata",
          generated_at_ms: Date.now(),
          summary: { complete: 0, incomplete: 1, overdue: 1, due_soon: 0, submitted: 1, accepted: 1, rejected: 0, evaluations_finalized: 0, evaluations_total: 0 },
          data: [{ event_speaker_id: assignmentId, display_name: "Speaker", proposal_title: "A proposal", task_id: "77777777-7777-4777-8777-777777777777", task_type: "headshot", task_title: "Upload headshot", state: "overdue", due_at_ms: Date.now() - 1000, last_activity_at_ms: Date.now() - 2000 }],
          next_cursor: null,
        }),
      });
    });

    await page.goto(`/admin/events/${eventId}/onboarding`);
    await expect(page.getByRole("status").first()).toHaveText("1 speaker record shown.");
    await page.getByLabel("Task state").selectOption("overdue");
    await page.getByLabel("Task type").selectOption("headshot");
    await page.getByRole("button", { name: "Apply filters" }).click();
    await expect.poll(() => dashboardReads).toBeGreaterThanOrEqual(2);
    expect(lastQuery).toBe("?state=overdue&task_type=headshot");
    await expect(page).toHaveURL(/state=overdue&task_type=headshot/);
  });

  test("speaker profile, custom task, and upload forms validate locally", async ({ page }) => {
    await polyfillUuid(page);
    const speakerSession = { ...organizerSession, email: "speaker@example.com", organization_access: [], event_access: [{ organization_id: organizationId, event_id: eventId, roles: ["speaker"] }] };
    await mockSession(page, speakerSession);
    let profileWrites = 0;
    let taskWrites = 0;
    let taskComplete = false;
    let authorizationWrites = 0;
    let storageWrites = 0;
    let completionWrites = 0;
    let profile = { display_name: "Speaker", job_title: "", company: "", biography: "", location: "", links: [], version: 1 };
    const portal = () => ({
      event: { id: eventId, name: "Conference", starts_at_ms: Date.UTC(2030, 2, 20), ends_at_ms: Date.UTC(2030, 2, 21), time_zone: "Asia/Kolkata" },
      profile,
      tasks: taskComplete ? [] : [{ id: assignmentId, task_type: "custom", title: "Dietary needs", help_text: "Tell us", destination_path: "#tasks", state: "open", due_at_ms: null, completed_at_ms: null, form_fields: [{ key: "response", label: "Dietary requirements", type: "text", required: true, choices: [] }], response: {}, version: 1 }],
      submissions: [{ id: programId, proposal_title: "A proposal", status: "accepted" }], completed_tasks: taskComplete ? 1 : 0, total_tasks: 1,
    });
    await page.route("**/api/v1/speaker/portal", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(portal()) }));
    await page.route("**/api/v1/speaker/profile", async (route) => { profileWrites += 1; profile = { ...profile, ...route.request().postDataJSON(), version: 2 }; await route.fulfill({ contentType: "application/json", body: JSON.stringify(profile) }); });
    await page.route("**/api/v1/speaker/resources", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/speaker/events/${eventId}/assets`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/speaker/tasks/${assignmentId}/response`, async (route) => { taskWrites += 1; taskComplete = true; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: assignmentId, state: "completed", response: {}, version: 2 }) }); });
    await page.route(`**/api/v1/speaker/events/${eventId}/upload-authorizations`, async (route) => {
      authorizationWrites += 1;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          intent_id: "99999999-9999-4999-8999-999999999999",
          upload_url: "/api/v1/uploads/99999999-9999-4999-8999-999999999999/content?token=test",
          method: "PUT",
          headers: { "content-type": "image/png" },
          expires_at_ms: Date.now() + 60_000,
        }),
      });
    });
    await page.route("**/api/v1/uploads/99999999-9999-4999-8999-999999999999/content?token=test", async (route) => {
      storageWrites += 1;
      await route.fulfill({ status: 204, body: "" });
    });
    await page.route(`**/api/v1/speaker/events/${eventId}/upload-intents/99999999-9999-4999-8999-999999999999/complete`, async (route) => {
      completionWrites += 1;
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ state: "clean" }) });
    });

    await page.goto("/speaker");
    await page.getByRole("button", { name: "Save profile" }).click();
    expect(profileWrites).toBe(0);
    await page.getByLabel(/Biography/).fill("Conference speaker biography.");
    await page.getByLabel("Website").fill("ftp://example.com");
    await page.getByRole("button", { name: "Save profile" }).click();
    expect(profileWrites).toBe(0);
    await page.getByLabel("Website").fill("https://example.com");
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect.poll(() => profileWrites).toBe(1);

    const taskForm = page.locator("#task-list form");
    await taskForm.getByRole("button", { name: "Send response" }).click();
    expect(taskWrites).toBe(0);
    await taskForm.getByLabel("Dietary requirements").fill("Vegetarian");
    await taskForm.getByRole("button", { name: "Send response" }).click();
    await expect.poll(() => taskWrites).toBe(1);

    await page.getByLabel("What changed?").first().fill("Updated conference headshot");
    await page.getByLabel("Choose headshot").setInputFiles({ name: "not-an-image.txt", mimeType: "text/plain", buffer: Buffer.from("not an image") });
    await page.getByRole("button", { name: "Upload headshot" }).click();
    await expect(page.locator('[data-kind="headshot"] .upload-status')).toHaveText("This file type is not allowed.");
    await page.getByLabel("Choose headshot").setInputFiles({ name: "speaker.png", mimeType: "image/png", buffer: Buffer.from("valid image payload") });
    await page.getByRole("button", { name: "Upload headshot" }).click();
    await expect(page.locator('[data-kind="headshot"] .upload-status')).toHaveText("Upload checked and ready.");
    expect(authorizationWrites).toBe(1);
    expect(storageWrites).toBe(1);
    expect(completionWrites).toBe(1);
  });

  test("fresh agenda setup leads directly to timezone-correct scheduling", async ({ page }) => {
    await polyfillUuid(page);
    await mockSession(page);
    const start = Date.UTC(2030, 2, 19, 18, 30);
    const end = Date.UTC(2030, 2, 20, 18, 30);
    let configured = false;
    let scheduled = false;
    let setupWrites = 0;
    let publishWrites = 0;
    let savedCandidate: Record<string, unknown> | null = null;
    const model = () => ({
      event: { id: eventId, name: "Conference", time_zone: "Asia/Kolkata", starts_at_ms: start, ends_at_ms: end },
      revision: { id: "11111111-1111-4111-8111-111111111111", version: 1, state: "draft" },
      rooms: [{ id: "22222222-2222-4222-8222-222222222222", name: "Main stage" }],
      tracks: [{ id: "33333333-3333-4333-8333-333333333333", name: "General" }],
      items: scheduled ? [{ id: "44444444-4444-4444-8444-444444444444", session_id: assignmentId, title: "A proposal", abstract: "Abstract", content_status: "approved", content_version: 1, start_at_ms: savedCandidate?.start_at_ms, end_at_ms: savedCandidate?.end_at_ms, room_id: "22222222-2222-4222-8222-222222222222", room_name: "Main stage", track_id: null, track_name: null, version: 1 }] : [],
      unscheduled_sessions: scheduled ? [] : [{ session_id: assignmentId, title: "A proposal", abstract: "Abstract", content_status: "approved", content_version: 1 }],
    });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda`, async (route) => {
      if (!configured) { await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { message: "Not found" } }) }); return; }
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(model()) });
    });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/setup`, async (route) => { setupWrites += 1; configured = true; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(model()) }); });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/preview`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ valid: true, conflicts: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/items`, async (route) => { savedCandidate = route.request().postDataJSON(); scheduled = true; await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ id: "44444444-4444-4444-8444-444444444444", version: 1 }) }); });
    await page.route(`**/api/v1/admin/events/${eventId}/agenda/publish`, async (route) => { publishWrites += 1; await route.fulfill({ contentType: "application/json", body: JSON.stringify({ state: "published" }) }); });

    await page.goto(`/admin/events/${eventId}/agenda`);
    await expect(page.getByRole("heading", { name: "Set up the schedule" })).toBeVisible();
    await page.getByRole("button", { name: "Create agenda" }).click();
    expect(setupWrites).toBe(0);
    await page.getByLabel("Rooms").fill("Main stage");
    await page.getByLabel(/Tracks/).fill("General");
    await page.getByRole("button", { name: "Create agenda" }).click();
    await expect(page.getByRole("button", { name: "Schedule" })).toBeVisible();
    expect(setupWrites).toBe(1);
    await page.getByRole("button", { name: "Schedule" }).click();
    const editor = page.getByRole("dialog", { name: "Schedule session" });
    await editor.getByLabel("Starts").fill("2030-03-20T09:00");
    await editor.getByLabel("Ends").fill("2030-03-20T10:00");
    await editor.getByLabel("Room").selectOption("22222222-2222-4222-8222-222222222222");
    await expect(editor.getByText("No room or speaker conflicts found.")).toBeVisible();
    await editor.getByRole("button", { name: "Save to draft" }).click();
    await expect(page.locator("#status")).toHaveText("Session scheduled successfully.");
    expect(savedCandidate).toMatchObject({
      start_at_ms: Date.UTC(2030, 2, 20, 3, 30),
      end_at_ms: Date.UTC(2030, 2, 20, 4, 30),
      room_id: "22222222-2222-4222-8222-222222222222",
    });
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Publish agenda" }).click();
    await expect(page.locator("#status")).toHaveText("Agenda published. Calendar updates were queued for speakers.");
    expect(publishWrites).toBe(1);
  });
});
