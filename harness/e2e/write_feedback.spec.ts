import { expect, test } from "@playwright/test";

// Successful writes must report SUCCESS. The eval run caught both of these
// flows showing "The request could not be completed. Try again." after the
// server had accepted the write — caused by touching event.currentTarget
// after an await (it is null outside dispatch), which threw and landed in
// the error path.

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const orgId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const sessionBody = JSON.stringify({
  user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  organization_id: orgId,
  event_id: eventId,
  csrf_token: "browser-test-csrf",
  email: "admin@example.com",
  display_name: "Admin User",
  profile_complete: true,
  account_roles: ["organizer"],
  active_role: "organizer",
  default_role: "organizer",
  organization_access: [{ organization_id: orgId, organization_name: "Example Organization", permissions: ["owner"] }],
  event_access: [{ organization_id: orgId, event_id: eventId, event_name: "Example Event", permissions: ["owner"], assignments: [] }],
});
const speakerTarget = {
  event_speaker_id: "speaker-1",
  person_id: "person-1",
  user_id: "uuuuuuuu-uuuu-4uuu-8uuu-uuuuuuuuuuuu",
  email: "priya@example.com",
  display_name: "Priya Raman",
  job_title: "",
  company: "",
  biography: "",
  location: "",
  links: [],
  version: 1,
  selection_status: "accepted",
  proposal_title: "Accepted talk",
};

async function mockCommonRoutes(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/auth/session", (route) =>
    route.fulfill({ contentType: "application/json", body: sessionBody }));
  await page.route(`**/api/v1/admin/events/${eventId}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: eventId, organization_id: orgId, name: "Feedback Conf", status: "active",
        version: 1, time_zone: "Asia/Kolkata", delivery_mode: "hybrid",
        starts_at_ms: Date.UTC(2027, 4, 18, 3, 30), ends_at_ms: Date.UTC(2027, 4, 20, 11, 30),
      }),
    }));
  await page.route(`**/api/v1/admin/events/${eventId}/speaker-targets`, (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [speakerTarget] }) }));
}

test.describe("write feedback is truthful", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("assigning a task reports success, not a failure banner", async ({ page }) => {
    await mockCommonRoutes(page);
    await page.route(`**/api/v1/admin/events/${eventId}/resources`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    await page.route(`**/api/v1/admin/events/${eventId}/assets`, (route) =>
      route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
    let taskPosts = 0;
    await page.route(`**/api/v1/admin/events/${eventId}/speaker-tasks`, async (route) => {
      taskPosts += 1;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "task-1", event_speaker_id: "speaker-1", task_type: "custom",
          title: "Travel preferences", state: "open", due_at_ms: null,
        }),
      });
    });

    await page.goto(`/admin/events/${eventId}/speaker-content`);
    // Let initialization settle first: its final status write would race the
    // action feedback we assert below.
    await expect(page.getByRole("status").first()).toHaveText("Speaker tasks and files ready.");
    await page.getByText("Choose speakers and task details", { exact: true }).click();
    await page.locator('input[name="event_speaker_id"][value="speaker-1"]').check();
    await page.getByRole("textbox", { name: /Task title/ }).fill("Travel preferences");
    await page.getByRole("button", { name: "Assign task" }).click();

    await expect(page.getByRole("status").first()).toHaveText("Task assigned to 1 speaker.");
    expect(taskPosts).toBe(1);
    await expect(page.getByText("could not be completed")).toHaveCount(0);
  });

  for (const width of [390, 1280]) {
    test(`bulk message confirmation is explicit at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await mockCommonRoutes(page);
    // NOTE: in Playwright globs "?" is a single-character wildcard — a
    // "communications?**" pattern can also match /communications/speakers/*.
    // A URL predicate keeps the history mock scoped to the history endpoint.
    await page.route(
      (url) => url.pathname.endsWith(`/api/v1/admin/events/${eventId}/communications`),
      (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], next_cursor: null }) }),
    );
    await page.route(`**/api/v1/admin/events/${eventId}/communications/speakers/preview`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          recipients: [{
            recipient_user_id: "uuuuuuuu-uuuu-4uuu-8uuu-uuuuuuuuuuuu",
            display_name: "Priya Raman",
            email: "priya@example.com",
            subject: "Welcome speakers",
            html_body: "<p>Welcome!</p>",
          }],
        }),
      }));
    let sendPosts = 0;
    await page.route(`**/api/v1/admin/events/${eventId}/communications/speakers/send`, (route) => {
      sendPosts += 1;
      return route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ message_ids: ["message-1"] }),
      });
    });

    await page.goto(`/admin/events/${eventId}/messages`);
    // Initialization's final status write must land before we act.
    await expect(page.getByRole("status").first()).toHaveText("1 available recipient.");
    await page.getByRole("checkbox", { name: /Priya Raman/ }).check();
    await page.getByRole("textbox", { name: /Subject/ }).fill("Welcome speakers");
    await page.getByRole("textbox", { name: /Message|Body/ }).fill("Welcome!");
    await page.getByRole("button", { name: /Preview/ }).click();
    await expect(page.getByRole("status").first()).toHaveText("Preview ready for 1 recipient.");
    await page.getByRole("button", { name: /^Send/ }).click();
    const confirmation = page.getByRole("dialog", { name: "Send messages?" });
    await expect(confirmation).toBeVisible();
    await expect(confirmation).toContainText("1 personalized email will be queued.");
    await expect(confirmation).toContainText("queued immediately and cannot be recalled");
    expect(sendPosts).toBe(0);
    await confirmation.getByRole("button", { name: "Queue emails" }).click();

    await expect(page.getByRole("status").first()).toHaveText("1 message queued.");
    expect(sendPosts).toBe(1);
      await expect(page.getByText("could not be completed")).toHaveCount(0);
    });
  }
});
