import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const errorPage = readFileSync(resolve(root, "error_page.html"), "utf8");

function pageSource(html: string, script: string) {
  return readFileSync(resolve(root, html), "utf8")
    .replace(/<link[^>]+>/g, "")
    .replace(/<script[^>]+><\/script>/g, "")
    .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, script), "utf8")}</script></body>`);
}

function eventUnavailablePage() {
  return errorPage
    .replace("{{TITLE}}", "Event unavailable")
    .replace("{{EYEBROW}}", "Event unavailable")
    .replace("{{STATUS}}", "404")
    .replace("{{HEADING}}", "This event isn’t available.")
    .replace("{{MESSAGE}}", "The link may be outdated, or your account may no longer have access.")
    .replace("{{PRIMARY_LABEL}}", "Open active workspace")
    .replace("{{PRIMARY_HREF}}", "/")
    .replace("{{REFERENCE}}", "");
}

const session = {
  csrf_token: "csrf",
  organization_access: [{ permissions: ["manage"] }],
  event_access: [],
};

test("invalid event overview link leaves the loading state", async ({ page }) => {
  const html = pageSource("event_overview.html", "event_overview.js");
  await page.route("**/admin/events/", (route) => route.fulfill({ contentType: "text/html", body: html }));

  await page.goto("/admin/events/");
  await expect(page.getByText("This event link is invalid. Open your active workspace and select an event.")).toBeVisible();
  await expect(page.getByText("Loading event…")).toBeHidden();
});

test("archived event overview renders context when operational APIs are unavailable", async ({ page }) => {
  const html = pageSource("event_overview.html", "event_overview.js");
  await page.route("**/admin/events/archived-event", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/archived-event", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      id: "archived-event",
      name: "Archived Summit",
      time_zone: "UTC",
      starts_at_ms: 1_800_000_000_000,
      ends_at_ms: 1_800_003_600_000,
      delivery_mode: "in_person",
      location: "Old venue",
      status: "archived",
    }),
  }));
  await page.route("**/api/v1/admin/events/archived-event/**", (route) => route.fulfill({
    status: 404,
    contentType: "application/json",
    body: JSON.stringify({ detail: "not found" }),
  }));

  await page.goto("/admin/events/archived-event");
  await expect(page.getByRole("heading", { name: "Archived Summit" })).toBeVisible();
  await expect(page.getByText("Some program information is unavailable. Refresh to try again.")).toBeVisible();
  await expect(page.getByText("The event could not be loaded. Try again.")).toHaveCount(0);
});

test("archived reviewer access is maintenance-only", async ({ page }) => {
  const html = pageSource("access_admin.html", "access_admin.js");
  await page.route("**/admin/events/archived-event/reviewers", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/archived-event", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ id: "archived-event", name: "Archived Summit", status: "archived" }),
  }));
  await page.route("**/api/v1/admin/events/archived-event/invitations", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      data: [{
        id: "invitation-1",
        event_id: "archived-event",
        email: "reviewer@example.test",
        role: "evaluator",
        display_name: "Riley Reviewer",
        job_title: "",
        company: "",
        status: "pending",
      }],
    }),
  }));

  await page.goto("/admin/events/archived-event/reviewers");
  await expect(page.getByText("This event is archived. Existing reviewer access can be revoked, but new invitations cannot be sent.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Invite reviewer" })).toBeHidden();
  await expect(page.getByRole("search")).toBeHidden();
  await expect(page.getByRole("button", { name: "Send again" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Revoke" })).toBeVisible();
});

test("invalid event speaker directory recovers once into the server 404 page", async ({ page }) => {
  const html = pageSource("speaker_directory.html", "speaker_directory.js");
  let documentRequests = 0;
  await page.route("**/admin/events/not-an-event/speakers", (route) => {
    documentRequests += 1;
    if (documentRequests === 1) {
      return route.fulfill({ contentType: "text/html", body: html });
    }
    return route.fulfill({
      status: 404,
      contentType: "text/html",
      body: eventUnavailablePage(),
    });
  });
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
  await page.route("**/api/v1/admin/events/not-an-event", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));
  await page.route("**/api/v1/admin/events/not-an-event/speaker-targets", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));

  await page.goto("/admin/events/not-an-event/speakers");
  await expect(page.getByRole("heading", { name: "This event isn’t available." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open active workspace" })).toHaveAttribute("href", "/");
  await page.waitForTimeout(250);
  expect(documentRequests).toBe(2);
});

test("onboarding stops polling when unavailable recovery was already attempted", async ({ page }) => {
  const html = pageSource("admin_onboarding.html", "admin_onboarding.js");
  let onboardingRequests = 0;
  await page.addInitScript(() => {
    const nativeSetInterval = window.setInterval.bind(window);
    (window as any).setInterval = (handler: TimerHandler) => nativeSetInterval(handler, 20);
    sessionStorage.setItem(
      "sessionbuddy:document-recovery:404:/admin/events/not-an-event/onboarding",
      String(Date.now()),
    );
  });
  await page.route("**/admin/events/not-an-event/onboarding", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/not-an-event/onboarding**", (route) => {
    onboardingRequests += 1;
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
  });

  await page.goto("/admin/events/not-an-event/onboarding");
  await expect(page.getByText("This event is no longer available to your account.")).toBeVisible();
  await expect(page.getByText("Access unavailable")).toBeVisible();
  await expect(page.getByText("Reconnecting")).toHaveCount(0);
  await page.waitForTimeout(100);
  expect(onboardingRequests).toBe(1);
});
