import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const html = readFileSync(resolve(root, "agenda_admin.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</body>", ["api_client.js", "agenda.js"].map((file) => `<script>${readFileSync(resolve(root, file), "utf8")}</script>`).join("") + "</body>");

const cases = [
  { name: "weekday ordering and empty final day", zone: "America/Los_Angeles", start: "2026-10-14T07:00Z", end: "2026-10-17T06:59Z", sessions: ["2026-10-15T17:00Z", "2026-10-14T17:00Z"], days: ["2026-10-14", "2026-10-15", "2026-10-16"] },
  { name: "year boundary and retained out-of-range session", zone: "Pacific/Kiritimati", start: "2026-12-30T10:00Z", end: "2027-01-01T09:59Z", sessions: ["2027-01-02T00:00Z"], days: ["2026-12-31", "2027-01-01", "2027-01-02"] },
  { name: "DST spring boundary with entirely empty agenda", zone: "America/Los_Angeles", start: "2026-03-07T08:00Z", end: "2026-03-10T06:59Z", sessions: [], days: ["2026-03-07", "2026-03-08", "2026-03-09"] },
  { name: "DST fall boundary and month boundary", zone: "America/Los_Angeles", start: "2026-10-31T07:00Z", end: "2026-11-03T07:59Z", sessions: [], days: ["2026-10-31", "2026-11-01", "2026-11-02"] },
  { name: "reversed event range remains bounded", zone: "UTC", start: "2026-10-16T00:00Z", end: "2026-10-14T00:00Z", sessions: ["2026-10-15T10:00Z"], days: ["2026-10-15"] },
  { name: "oversized event range remains bounded", zone: "UTC", start: "2000-01-01T00:00Z", end: "2099-01-01T00:00Z", sessions: ["2026-10-15T10:00Z"], days: ["2026-10-15"] },
];

for (const scenario of cases) {
  test(`Day view: ${scenario.name}`, async ({ page }) => {
    const model = {
      event: { id: "event-a", name: "Day checks", time_zone: scenario.zone, starts_at_ms: Date.parse(scenario.start), ends_at_ms: Date.parse(scenario.end) },
      revision: { id: "revision-a", version: 1, state: "draft" }, published_revision: null,
      rooms: [{ id: "room-a", name: "Main", status: "active", version: 1 }],
      tracks: [], labels: [], archived_rooms: [], archived_tracks: [], archived_labels: [],
      can_manage_resource_lifecycle: true, unscheduled_sessions: [],
      items: scenario.sessions.map((start, index) => ({
        id: `item-${index}`, session_id: `session-${index}`, title: `Session ${index}`,
        abstract: "Description", content_status: "approved", content_version: 1, label_version: 1,
        labels: [], label_ids: [], participants: [], speaker_names: "Speaker", version: 1,
        room_id: "room-a", room_name: "Main", track_id: null, track_name: null,
        start_at_ms: Date.parse(start), end_at_ms: Date.parse(start) + 3600000,
      })),
    };
    await page.route("**/admin/events/event-a/agenda", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { csrf_token: "csrf" } }));
    await page.route("**/api/v1/admin/events/event-a/agenda", (route) => route.fulfill({ json: model }));
    await page.goto("https://test/admin/events/event-a/agenda");
    await expect(page.locator("#status")).toHaveText("Draft agenda loaded.");
    await page.getByRole("radio", { name: "Day", exact: true }).check();
    await expect(page.locator("#board .agenda-group")).toHaveCount(scenario.days.length);
    expect(await page.locator("#board .agenda-group").evaluateAll((groups) => groups.map((group) => (group as HTMLElement).dataset.group))).toEqual(scenario.days);
    await expect(page.locator("#board .session-card")).toHaveCount(scenario.sessions.length);
    if (scenario.sessions.length === 0) await expect(page.locator("#board .empty")).toHaveCount(scenario.days.length);
  });
}
