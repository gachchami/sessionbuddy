import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const html = readFileSync(resolve(root, "agenda_admin.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "agenda.js"), "utf8")}</script></body>`);

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const model = {
  event: { id: eventId, name: "Content Conference", time_zone: "UTC", starts_at_ms: Date.UTC(2030, 2, 20, 9), ends_at_ms: Date.UTC(2030, 2, 20, 18) },
  revision: { id: "revision-1", version: 1, revision_number: 1 },
  published_revision: null,
  rooms: [{ id: "room-1", name: "Main stage", status: "active", version: 1 }],
  tracks: [], labels: [], archived_rooms: [], archived_tracks: [], archived_labels: [],
  can_manage_resource_lifecycle: true,
  items: [
    { id: "item-approved", session_id: "session-approved", title: "Ready session", abstract: "Ready", content_status: "approved", content_version: 1, label_version: 1, labels: [], label_ids: [], participants: [], speaker_names: "Priya", version: 1, room_id: "room-1", room_name: "Main stage", track_id: null, track_name: null, start_at_ms: Date.UTC(2030, 2, 20, 9), end_at_ms: Date.UTC(2030, 2, 20, 10) },
    { id: "item-draft", session_id: "session-draft", title: "Needs approval", abstract: "Draft", content_status: "draft", content_version: 1, label_version: 1, labels: [], label_ids: [], participants: [], speaker_names: "Marcus", version: 1, room_id: "room-1", room_name: "Main stage", track_id: null, track_name: null, start_at_ms: Date.UTC(2030, 2, 20, 10), end_at_ms: Date.UTC(2030, 2, 20, 11) },
  ],
  unscheduled_sessions: [],
};

test("publishing makes scheduled draft approval an explicit choice", async ({ page }) => {
  const publishBodies: Array<Record<string, unknown>> = [];
  let rejectNextPublish = false;
  await page.addInitScript(() => Object.defineProperty(crypto, "randomUUID", { configurable: true, value: () => "ffffffff-ffff-4fff-8fff-ffffffffffff" }));
  await page.route(`**/admin/events/${eventId}/agenda`, (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { csrf_token: "csrf" } }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda`, (route) => route.fulfill({ json: model }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda/publish`, async (route) => {
    const body = route.request().postDataJSON();
    publishBodies.push(body);
    if (rejectNextPublish) {
      rejectNextPublish = false;
      await route.fulfill({ status: 409, json: { error: { code: "conflict", message: "Conflict" } } });
      return;
    }
    const approve = body.approve_draft_sessions === true;
    await route.fulfill({ json: {
      published_revision_id: "revision-1", published_version: 2, draft_revision_id: "revision-2",
      newly_approved_session_ids: approve ? ["session-draft"] : [],
      hidden_session_count: approve ? 0 : 1,
    } });
  });

  await page.goto(`/admin/events/${eventId}/agenda`);
  await page.getByRole("button", { name: "Publish agenda" }).click();
  const dialog = page.getByRole("dialog", { name: "Publish this agenda?" });
  await expect(dialog).toContainText("1 of 2 scheduled sessions are approved and will become publicly visible.");
  await expect(dialog.getByText("Needs approval", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Publish only" })).toHaveClass(/secondary/);
  await expect(dialog.getByRole("button", { name: "Approve scheduled drafts and publish" })).toBeVisible();

  await dialog.getByRole("button", { name: "Publish only" }).click();
  await expect(page.locator("#status")).toHaveText("Agenda published. 1 session remains hidden until content is approved.");
  expect(publishBodies[0]).toMatchObject({ approve_draft_sessions: false });

  await page.getByRole("button", { name: "Publish agenda" }).click();
  await dialog.getByRole("button", { name: "Approve scheduled drafts and publish" }).click();
  await expect(page.locator("#status")).toHaveText("Agenda published. All scheduled sessions are publicly visible.");
  expect(publishBodies[1]).toMatchObject({ approve_draft_sessions: true });

  // A concurrent revision/content change reloads the current model and
  // reopens the choice instead of leaving the organizer at a stale toast.
  rejectNextPublish = true;
  await page.getByRole("button", { name: "Publish agenda" }).click();
  await dialog.getByRole("button", { name: "Approve scheduled drafts and publish" }).click();
  await expect(dialog).toBeVisible();
  await expect(page.locator("#status")).toHaveText("This draft changed before publication. Refresh and review it again.");
});
