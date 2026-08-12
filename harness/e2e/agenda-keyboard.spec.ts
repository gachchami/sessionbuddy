import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const agendaHtml = readFileSync(resolve(staticRoot, "agenda_admin.html"), "utf8");
const agendaJavaScript = readFileSync(resolve(staticRoot, "agenda.js"), "utf8");
const agendaCss = readFileSync(resolve(staticRoot, "agenda.css"), "utf8");
const apiClientJavaScript = readFileSync(resolve(staticRoot, "api_client.js"), "utf8");
const appShellJavaScript = readFileSync(resolve(staticRoot, "app_shell.js"), "utf8");

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const sessionId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const itemId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
const mainRoomId = "11111111-1111-4111-8111-111111111111";
const breakoutRoomId = "22222222-2222-4222-8222-222222222222";
const platformTrackId = "33333333-3333-4333-8333-333333333333";

async function serveAgenda(page: Page) {
  let saved: Record<string, unknown> | null = null;
  let createWrites = 0;
  let moveWrites = 0;

  const model = () => ({
    event: {
      id: eventId,
      name: "Keyboard Conference",
      time_zone: "UTC",
      starts_at_ms: Date.UTC(2030, 2, 20, 9),
      ends_at_ms: Date.UTC(2030, 2, 20, 18),
    },
    revision: { id: "revision-1", version: 1, state: "draft" },
    published_revision: null,
    rooms: [
      { id: mainRoomId, name: "Main stage", status: "active", version: 1 },
      { id: breakoutRoomId, name: "Breakout room", status: "active", version: 1 },
    ],
    tracks: [{ id: platformTrackId, name: "Platform & Infra", status: "active", version: 1 }],
    labels: [],
    archived_rooms: [],
    archived_tracks: [],
    archived_labels: [],
    can_manage_resource_lifecycle: true,
    items: saved
      ? [{
          id: itemId,
          session_id: sessionId,
          title: "Keyboard-first session",
          abstract: "A session scheduled without drag and drop.",
          content_status: "approved",
          content_version: 1,
          label_version: 1,
          labels: [],
          label_ids: [],
          speaker_names: "Priya Raman",
          version: 1,
          room_name: saved.room_id === breakoutRoomId ? "Breakout room" : "Main stage",
          ...saved,
        }]
      : [],
    unscheduled_sessions: saved
      ? []
      : [{
          session_id: sessionId,
          title: "Keyboard-first session",
          abstract: "A session scheduled without drag and drop.",
          content_status: "approved",
          content_version: 1,
          label_version: 1,
          labels: [],
          label_ids: [],
          track_id: platformTrackId,
          track_name: "Platform & Infra",
          speaker_names: "Priya Raman",
        }],
  });

  await page.addInitScript(() => {
    if (!crypto.randomUUID) {
      Object.defineProperty(crypto, "randomUUID", {
        configurable: true,
        value: () => "ffffffff-ffff-4fff-8fff-ffffffffffff",
      });
    }
  });
  await page.route("**/admin/agenda/assets/agenda.js*", (route) => route.fulfill({
    contentType: "text/javascript",
    body: agendaJavaScript,
  }));
  await page.route("**/admin/agenda/assets/agenda.css*", (route) => route.fulfill({
    contentType: "text/css",
    body: agendaCss,
  }));
  await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({
    contentType: "text/javascript",
    body: apiClientJavaScript,
  }));
  await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({
    contentType: "text/javascript",
    body: appShellJavaScript,
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "organizer@example.com",
      display_name: "Organizer",
      profile_complete: true,
      csrf_token: "browser-test-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_access: [],
      event_access: [{
        organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        event_id: eventId,
        event_name: "Keyboard Conference",
        permissions: ["owner"],
        assignments: [],
      }],
    }),
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda/preview`, (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify({ valid: true, conflicts: [] }) }));
  await page.route(`**/api/v1/admin/events/${eventId}/sessions/${sessionId}/content`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        session_id: sessionId,
        title: "Keyboard-first session",
        abstract: "A session scheduled without drag and drop.",
        content_status: "approved",
        version: 1,
        history: [],
      }),
    }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda/items`, async (route) => {
    createWrites += 1;
    saved = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ id: itemId, version: 1 }),
    });
  });
  await page.route(`**/api/v1/admin/events/${eventId}/agenda/items/${itemId}`, async (route) => {
    moveWrites += 1;
    saved = route.request().postDataJSON();
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: itemId, version: 2 }),
    });
  });
  await page.route(`**/admin/events/${eventId}/agenda`, (route) => route.fulfill({
    contentType: "text/html",
    body: agendaHtml,
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/agenda`, (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(model()),
  }));

  return {
    createWrites: () => createWrites,
    moveWrites: () => moveWrites,
    saved: () => saved,
  };
}

test.describe("agenda keyboard scheduling", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("schedules from an empty board and moves rooms without a drag target", async ({ page }) => {
    const writes = await serveAgenda(page);
    await page.goto(`/admin/events/${eventId}/agenda`);

    await expect(page.locator(".agenda-group[tabindex]")).toHaveCount(0);
    const schedule = page.getByRole("button", { name: "Schedule Keyboard-first session" });
    await schedule.focus();
    await expect(schedule).toBeFocused();
    await page.keyboard.press("Enter");

    const scheduleDialog = page.getByRole("dialog", { name: "Schedule Keyboard-first session" });
    await expect(scheduleDialog.getByText("Speaker: Priya Raman")).toBeVisible();
    await expect(scheduleDialog.getByLabel("Track")).toHaveValue(platformTrackId);
    await expect(scheduleDialog).toBeVisible();
    await expect(scheduleDialog.getByLabel("Starts")).toBeFocused();
    await scheduleDialog.getByLabel("Starts").fill("2030-03-20T09:00");
    await scheduleDialog.getByLabel("Ends").fill("2030-03-20T10:00");
    await scheduleDialog.getByLabel("Room").selectOption(mainRoomId);
    await expect(scheduleDialog.getByText("No room or speaker conflicts found.")).toBeVisible();
    const save = scheduleDialog.getByRole("button", { name: "Save to draft" });
    await save.focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#status")).toHaveText("Session scheduled successfully.");
    expect(writes.createWrites()).toBe(1);

    const edit = page.getByRole("button", { name: "Edit schedule for Keyboard-first session" });
    await edit.focus();
    await page.keyboard.press("Enter");
    const editDialog = page.getByRole("dialog", { name: "Edit schedule for Keyboard-first session" });
    await expect(editDialog.getByLabel("Starts")).toBeFocused();
    const room = editDialog.getByLabel("Room");
    await room.focus();
    await page.keyboard.press("End");
    await expect(room).toHaveValue(breakoutRoomId);
    await expect(editDialog.getByText("No room or speaker conflicts found.")).toBeVisible();
    const saveMove = editDialog.getByRole("button", { name: "Save to draft" });
    await saveMove.focus();
    await page.keyboard.press("Enter");
    await expect.poll(writes.moveWrites).toBe(1);
    await expect(page.locator("#status")).toHaveText("Session scheduled successfully.");
    expect(writes.saved()).toMatchObject({ room_id: breakoutRoomId });

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const blocking = results.violations.filter(({ impact }) =>
      impact === "critical" || impact === "serious"
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      await page.evaluate(() => document.documentElement.clientWidth),
    );
  });
});
