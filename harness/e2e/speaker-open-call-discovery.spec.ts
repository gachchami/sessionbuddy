import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const html = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/speaker_portal.html"), "utf8");
const css = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/speaker.css"), "utf8");
const apiClient = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/api_client.js"), "utf8");
const shell = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/app_shell.js"), "utf8");
const portal = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/speaker_portal.js"), "utf8");
const callsHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/open_calls.html"), "utf8");
const callsScript = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/open_calls.js"), "utf8");
const accountHtml = readFileSync(resolve(__dirname, "../../src/sessionbuddy/static/account.html"), "utf8");
const document = html
  .replace(/<link rel="stylesheet" href="\/app-shell\/assets\/app-shell\.css\?v=\d+">/, "")
  .replace(/<link rel="stylesheet" href="\/speaker\/assets\/speaker\.css\?v=\d+">/, `<style>${css}</style>`)
  .replace(/<script[^>]+defer><\/script>/g, "")
  .replace("</body>", `<script>${apiClient}</script><script>${shell}</script><script>${portal}</script></body>`);
const callsDocument = callsHtml
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+defer><\/script>/g, "")
  .replace("</body>", `<script>${apiClient}</script><script>${shell}</script><script>${callsScript}</script></body>`);
const accountDocument = accountHtml
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+defer><\/script>/g, "")
  .replace("</body>", `<script>${apiClient}</script><script>${shell}</script></body>`);

test("a roleless incomplete profile gets a usable account header", async ({ page }) => {
  await page.route("**/account?**", (route) => route.fulfill({ contentType: "text/html", body: accountDocument }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: {
    authenticated: true, active_role: null, account_roles: [], roles: [],
    csrf_token: "csrf", profile_complete: false, display_name: "New account",
  }}));

  await page.goto("http://sessionbuddy.test/account?onboarding=1&next=%2F");

  await expect(page.locator("[data-auth-shell]")).toContainText("SessionBuddy");
  await expect(page.getByText("Temporarily unavailable")).toHaveCount(0);
  await expect(page.getByText("Account access is unavailable")).toHaveCount(0);
  await expect(page.locator("#profile-form")).toBeAttached();
});

test("a roleless authenticated session renders a usable calls header", async ({ page }) => {
  await page.route("**/calls", (route) => route.fulfill({ contentType: "text/html", body: callsDocument }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ json: {
    authenticated: true, active_role: null, account_roles: [], roles: [],
    csrf_token: "csrf", profile_complete: true, display_name: "New account",
  }}));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: {
    authenticated: true, active_role: null, account_roles: [], roles: [],
    csrf_token: "csrf", profile_complete: true, display_name: "New account",
  }}));
  await page.route("**/api/v1/public/calls", (route) => route.fulfill({ json: { data: [] } }));

  await page.goto("http://sessionbuddy.test/calls");

  await expect(page.locator("[data-auth-shell]")).toContainText("SessionBuddy");
  await expect(page.getByText("Temporarily unavailable")).toHaveCount(0);
  await expect(page.getByText("Account access is unavailable")).toHaveCount(0);
  await expect(page.getByText("No calls are open or scheduled right now.")).toBeVisible();
});

test("a populated speaker portal discovers a call for another event", async ({ page }) => {
  await page.route("**/speaker", (route) => route.fulfill({ contentType: "text/html", body: document }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ json: {
    authenticated: true, active_role: "speaker", account_roles: ["speaker"], roles: ["speaker"],
    csrf_token: "csrf", profile_complete: true,
  }}));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: {
    authenticated: true, email: "speaker@example.test", display_name: "Speaker",
  }}));
  await page.route("**/api/v1/speaker/proposal-drafts", (route) => route.fulfill({ json: { data: [] } }));
  await page.route("**/api/v1/speaker/open-calls", (route) => route.fulfill({ json: { data: [{
    event_id: "bbbbbb00-0000-4000-8000-000000000000", event_name: "Event B",
    starts_at_ms: Date.UTC(2026, 8, 10), ends_at_ms: Date.UTC(2026, 8, 12), time_zone: "UTC",
    location: "Online", delivery_mode: "virtual", form_id: "form-b", slug: "event-b-call",
    cfp_state: "open", cfp_boundary_at_ms: Date.UTC(2026, 8, 1), cfp_boundary_kind: "closes",
    submission_limit: 2, submission_count: 0, remaining_submissions: 2,
    already_submitted: false, actionable: true,
  }] } }));
  await page.route("**/api/v1/speaker/portal**", (route) => route.fulfill({ json: {
    event: { id: "event-a", name: "Event A", starts_at_ms: 1, ends_at_ms: 2, time_zone: "UTC" },
    profile: { display_name: "Speaker", job_title: "", company: "", biography: "", location: "", links: [], version: 1 },
    tasks: [], submissions: [], notifications: [], open_call: null, completed_tasks: 0, total_tasks: 0,
  } }));
  await page.route("**/api/v1/speaker/**", (route) => /\/(open-calls|portal|proposal-drafts)(?:\?|$)/.test(route.request().url())
    ? route.fallback()
    : route.fulfill({ json: { data: [] } }));

  await page.goto("http://sessionbuddy.test/speaker#calls");

  await expect(page.getByRole("heading", { name: "Event B" })).toBeVisible();
  const action = page.getByRole("link", { name: "Submit a proposal" });
  await expect(action).toHaveAttribute("href", "/cfp/bbbbbb/event-b-call");
  await expect(page.locator("#portal")).toBeVisible();
});

test("a membership-scoped portal 404 does not hide call discovery", async ({ page }) => {
  await page.route("**/speaker", (route) => route.fulfill({ contentType: "text/html", body: document }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ json: {
    authenticated: true, active_role: "speaker", account_roles: ["speaker"], roles: ["speaker"],
    csrf_token: "csrf", profile_complete: true,
  }}));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: {
    authenticated: true, email: "speaker@example.test", display_name: "Speaker",
  }}));
  await page.route("**/api/v1/speaker/proposal-drafts", (route) => route.fulfill({ json: { data: [] } }));
  await page.route("**/api/v1/speaker/open-calls", (route) => route.fulfill({ json: { data: [{
    event_id: "bbbbbb00-0000-4000-8000-000000000000", event_name: "Event B",
    starts_at_ms: 1, ends_at_ms: 2, time_zone: "UTC", location: "Online",
    delivery_mode: "virtual", form_id: "form-b", slug: "event-b-call", cfp_state: "open",
    cfp_boundary_at_ms: null, cfp_boundary_kind: null, submission_limit: null,
    submission_count: 0, remaining_submissions: null, already_submitted: false, actionable: true,
  }] } }));
  await page.route("**/api/v1/speaker/portal**", (route) => route.fulfill({ status: 404, json: {
    error: { code: "not_found", message: "Not found" },
  } }));

  await page.goto("http://sessionbuddy.test/speaker#calls");

  await expect(page.getByRole("heading", { name: "Event B" })).toBeVisible();
  await expect(page.locator("#empty-state")).toBeVisible();
  await expect(page.locator("#calls")).toBeFocused();
});
