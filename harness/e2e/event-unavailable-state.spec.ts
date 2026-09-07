import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const errorPage = readFileSync(resolve(root, "error_page.html"), "utf8");
const apiClientSource = readFileSync(resolve(root, "api_client.js"), "utf8");

function pageSource(html: string, script: string) {
  return readFileSync(resolve(root, html), "utf8")
    .replace(/<link[^>]+>/g, "")
    .replace(/<script[^>]+><\/script>/g, "")
    .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "activity_format.js"), "utf8")}</script><script>${readFileSync(resolve(root, script), "utf8")}</script></body>`);
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

function speakerUnavailablePage() {
  return errorPage
    .replace("{{TITLE}}", "Speaker record unavailable")
    .replace("{{EYEBROW}}", "Speaker record unavailable")
    .replace("{{STATUS}}", "404")
    .replace("{{HEADING}}", "This speaker record isn’t available.")
    .replace("{{MESSAGE}}", "The link may be outdated, or the speaker may no longer belong to this event.")
    .replace("{{PRIMARY_LABEL}}", "Return to speakers")
    .replace("{{PRIMARY_HREF}}", "/admin/events/event-a/speakers")
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

test("event overview renders recent activity as a compact list", async ({ page }) => {
  const html = pageSource("event_overview.html", "event_overview.js");
  await page.route(/^http:\/\/worker:8787\/admin\/events\/activity-event$/, (route) => route.fulfill({
    contentType: "text/html",
    body: html,
  }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/activity-event/**", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [] }),
  }));
  await page.route("**/api/v1/admin/events/activity-event/activities", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ data: [{
      activity_id: "activity-1",
      actor_name: "Dana Demo",
      operation: "update",
      resource_type: "call_for_speaker_form",
      resource_id: "public-cfp-reference",
      subject_name: "CFP for Activity Summit",
      event_id: "activity-event",
      occurred_at_ms: Date.now() - 60_000,
    }] }),
  }));
  await page.route("**/api/v1/admin/events/activity-event", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      id: "activity-event",
      name: "Activity Summit",
      time_zone: "UTC",
      starts_at_ms: 1_900_000_000_000,
      ends_at_ms: 1_900_003_600_000,
      delivery_mode: "in_person",
      location: "Test Hall",
      status: "active",
    }),
  }));

  await page.goto("/admin/events/activity-event");
  await expect(page.getByRole("heading", { name: "Recent activity" })).toBeVisible();
  const activity = page.locator("#event-activity");
  await expect(activity.getByText("Dana Demo updated call for proposals CFP for Activity Summit")).toBeVisible();
  await expect(activity.getByText("1 minute ago")).toBeVisible();
  await expect(activity.getByRole("table")).toHaveCount(0);
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

test("speaker detail recovery is scoped to the singular record", async ({ page }) => {
  const html = pageSource("speaker_directory.html", "speaker_directory.js");
  let documentRequests = 0;
  let rosterRequests = 0;
  await page.route("**/recovery-key-fixture", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" }));
  await page.goto("/recovery-key-fixture");
  await page.addScriptTag({ content: apiClientSource });
  const expectedRecoveryKey = await page.evaluate(() => {
    const api = (window as any).SessionBuddyApi;
    return api.documentRecoveryKey("404", api.recoveryScope.eventSpeaker("event-a", "missing-speaker"));
  });
  await page.route((url) => url.pathname === "/admin/events/event-a/speakers/missing-speaker", (route) => {
    documentRequests += 1;
    if (documentRequests === 1) {
      return route.fulfill({ contentType: "text/html", body: html });
    }
    return route.fulfill({
      status: 404,
      contentType: "text/html",
      body: speakerUnavailablePage(),
    });
  });
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: "org-a", name: "Example" }] }) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", organization_id: "org-a", name: "Example event" }) }));
  await page.route("**/api/v1/admin/events/event-a/speaker-targets", (route) => {
    rosterRequests += 1;
    return route.abort("failed");
  });
  await page.route("**/api/v1/admin/events/event-a/speakers/missing-speaker", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));

  await page.goto("/admin/events/event-a/speakers/missing-speaker");
  await expect(page.getByRole("heading", { name: "This speaker record isn’t available." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Return to speakers" })).toHaveAttribute("href", "/admin/events/event-a/speakers");
  expect(documentRequests).toBe(2);
  expect(rosterRequests).toBe(0);
  expect(await page.evaluate((key) => sessionStorage.getItem(key), expectedRecoveryKey)).not.toBeNull();
});

test("onboarding stops polling when unavailable recovery was already attempted", async ({ page }) => {
  const html = pageSource("admin_onboarding.html", "admin_onboarding.js");
  let onboardingRequests = 0;
  await page.addInitScript(() => {
    const nativeSetInterval = window.setInterval.bind(window);
    (window as any).setInterval = (handler: TimerHandler) => nativeSetInterval(handler, 20);
  });
  await page.route("**/recovery-key-fixture", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" }));
  await page.goto("/recovery-key-fixture");
  await page.addScriptTag({ content: apiClientSource });
  await page.evaluate(() => {
    const api = (window as any).SessionBuddyApi;
    const scope = api.recoveryScope.event("not-an-event");
    sessionStorage.setItem(api.documentRecoveryKey("404", scope), String(Date.now()));
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

test("onboarding re-enters its guarded document exactly once on the first 404", async ({ page }) => {
  const html = pageSource("admin_onboarding.html", "admin_onboarding.js");
  let documentRequests = 0;
  let onboardingRequests = 0;
  await page.route("**/admin/events/missing-event/onboarding", (route) => {
    documentRequests += 1;
    return route.fulfill({
      status: documentRequests === 1 ? 200 : 404,
      contentType: "text/html",
      body: documentRequests === 1 ? html : eventUnavailablePage(),
    });
  });
  await page.route("**/api/v1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/missing-event/onboarding**", (route) => {
    onboardingRequests += 1;
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) });
  });

  await page.goto("/admin/events/missing-event/onboarding");
  await expect(page.getByRole("heading", { name: "This event isn’t available." })).toBeVisible();
  expect(documentRequests).toBe(2);
  expect(onboardingRequests).toBe(1);
});

test("onboarding keeps retryable failures in the reconnecting state", async ({ page }) => {
  const html = pageSource("admin_onboarding.html", "admin_onboarding.js");
  await page.route("**/admin/events/event-a/onboarding", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/event-a/onboarding**", (route) => route.fulfill({
    status: 503,
    headers: { "x-request-id": "onboarding-503" },
    contentType: "application/json",
    body: JSON.stringify({ error: { code: "dependency_unavailable", message: "Unavailable" } }),
  }));

  await page.goto("/admin/events/event-a/onboarding");
  await expect(page.getByText("Reconnecting", { exact: true })).toBeVisible();
  await expect(page.locator("#status")).toContainText("Reference: onboarding-503");
  await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();
});

test("onboarding restarts expired pagination without declaring the event unavailable", async ({ page }) => {
  const html = pageSource("admin_onboarding.html", "admin_onboarding.js");
  const requests: string[] = [];
  const payload = (nextCursor: string | null) => ({
    event_name: "Example event",
    time_zone: "UTC",
    generated_at_ms: Date.now(),
    next_cursor: nextCursor,
    summary: { complete: 0, incomplete: 0, overdue: 0, due_soon: 0, awaiting_acceptance: 0 },
    data: [],
  });
  await page.route("**/admin/events/event-a/onboarding", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/event-a/onboarding**", (route) => {
    const url = route.request().url();
    requests.push(url);
    if (url.includes("cursor=expired")) {
      return route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "stale_cursor", message: "Cursor expired", metadata: { reason: "expired" } } }),
      });
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(payload(requests.length === 1 ? "expired" : null)) });
  });

  await page.goto("/admin/events/event-a/onboarding");
  await page.locator("#load-more").click();
  await expect.poll(() => requests.length).toBe(3);
  await expect(page.locator("#results-panel")).toBeFocused();
  await expect(page.getByText("Reconnecting")).toHaveCount(0);
  await expect(page.getByText("This event is no longer available")).toHaveCount(0);
  expect(requests.filter((url) => url.includes("cursor=expired"))).toHaveLength(1);
  expect(requests.at(-1)).not.toContain("cursor=");
});

test("onboarding stops polling before a 401 navigation commits", async ({ page }) => {
  const html = pageSource("admin_onboarding.html", "admin_onboarding.js");
  let onboardingRequests = 0;
  await page.addInitScript(() => {
    const nativeSetInterval = window.setInterval.bind(window);
    (window as any).setInterval = (handler: TimerHandler) => nativeSetInterval(handler, 20);
  });
  await page.route("**/admin/events/event-a/onboarding", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route((url) => url.pathname === "/sign-in", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Sign in</title>" }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/event-a/onboarding**", (route) => {
    onboardingRequests += 1;
    return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: { message: "Sign in" } }) });
  });

  await page.goto("/admin/events/event-a/onboarding");
  await expect(page).toHaveURL(/\/sign-in\?redirect=/);
  await page.waitForTimeout(100);
  expect(onboardingRequests).toBe(1);
});

test("reviewer invitation 404 stays inline when the event still exists", async ({ page }) => {
  const html = pageSource("access_admin.html", "access_admin.js");
  let documentRequests = 0;
  await page.route("**/admin/events/event-a/reviewers", (route) => {
    documentRequests += 1;
    return route.fulfill({ contentType: "text/html", body: html });
  });
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", name: "Example", status: "active" }) }));
  await page.route("**/api/v1/admin/events/event-a/invitations", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));

  await page.goto("/admin/events/event-a/reviewers");
  await expect(page.locator("#status")).toContainText(/could not be found/i);
  expect(documentRequests).toBe(1);
  expect(page.url()).toContain("/admin/events/event-a/reviewers");
});

test("message-history 404 stays inline when the event still exists", async ({ page }) => {
  const html = pageSource("speaker_messages.html", "speaker_messages.js");
  let documentRequests = 0;
  await page.route("**/admin/events/event-a/messages", (route) => {
    documentRequests += 1;
    return route.fulfill({ contentType: "text/html", body: html });
  });
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", name: "Example", time_zone: "UTC" }) }));
  await page.route("**/api/v1/admin/events/event-a/speaker-targets", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
  await page.route("**/api/v1/admin/events/event-a/communications**", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));

  await page.goto("/admin/events/event-a/messages");
  await expect(page.locator("#status")).toContainText(/could not be found/i);
  expect(documentRequests).toBe(1);
  expect(page.url()).toContain("/admin/events/event-a/messages");
});

test("workspace tools remain usable when the document probe is transiently unavailable", async ({ page }) => {
  const html = pageSource("event_workspace.html", "event_workspace.js");
  await page.route("**/admin/events/event-a/workspace", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...session, csrf_token: "csrf" }) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ error: { code: "dependency_unavailable", message: "Unavailable" } }),
  }));

  await page.goto("/admin/events/event-a/workspace");
  await expect(page.locator("#generate-token")).toBeEnabled();
  await expect(page.locator("#status")).toHaveText("Sharing and integration tools ready.");
});

test("workspace keeps tools disabled when access recovery is throttled", async ({ page }) => {
  const html = pageSource("event_workspace.html", "event_workspace.js");
  await page.route("**/recovery-key-fixture", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><html><body></body></html>" }));
  await page.goto("/recovery-key-fixture");
  await page.addScriptTag({ content: apiClientSource });
  await page.evaluate(() => {
    const api = (window as any).SessionBuddyApi;
    const scope = api.recoveryScope.event("event-a");
    sessionStorage.setItem(api.documentRecoveryKey("403", scope), String(Date.now()));
  });
  await page.route("**/admin/events/event-a/workspace", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...session, csrf_token: "csrf" }) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({
    status: 403,
    headers: { "x-request-id": "workspace-403" },
    contentType: "application/json",
    body: JSON.stringify({ error: { code: "forbidden", message: "Forbidden" } }),
  }));

  await page.goto("/admin/events/event-a/workspace");
  await expect(page.locator("#generate-token")).toBeDisabled();
  await expect(page.locator("#status")).toContainText("Event access could not be confirmed");
  await expect(page.locator("#status")).toContainText("Reference: workspace-403");
});

test("round-history 404 stays inline when proposal data still loads", async ({ page }) => {
  const html = pageSource("admin_submissions.html", "admin_submissions.js");
  let documentRequests = 0;
  await page.route("**/admin/events/event-a/submissions", (route) => {
    documentRequests += 1;
    return route.fulfill({ contentType: "text/html", body: html });
  });
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...session, user_id: "organizer-a" }) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", name: "Example", time_zone: "UTC" }) }));
  await page.route("**/api/v1/admin/events/event-a/submissions", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], total: 0, next_cursor: null }) }));
  await page.route("**/api/v1/admin/events/event-a/cfp", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ published_form: null }) }));
  await page.route("**/api/v1/admin/events/event-a/evaluation-rounds", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));

  await page.goto("/admin/events/event-a/submissions");
  await expect(page.locator("#status")).toContainText(/could not be found/i);
  expect(documentRequests).toBe(1);
  expect(page.url()).toContain("/admin/events/event-a/submissions");
});

test("round-history 401 redirects the proposal inbox to sign in", async ({ page }) => {
  const html = pageSource("admin_submissions.html", "admin_submissions.js");
  await page.route("**/admin/events/event-a/submissions", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...session, user_id: "organizer-a" }) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", name: "Example", time_zone: "UTC" }) }));
  await page.route("**/api/v1/admin/events/event-a/submissions", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], total: 0, next_cursor: null }) }));
  await page.route("**/api/v1/admin/events/event-a/cfp", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ published_form: null }) }));
  await page.route("**/api/v1/admin/events/event-a/evaluation-rounds", (route) => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: { code: "authentication_required", message: "Sign in." } }) }));

  const signInNavigation = page.waitForRequest((request) => {
    const target = new URL(request.url());
    return request.isNavigationRequest()
      && target.pathname === "/sign-in"
      && target.searchParams.get("redirect") === "/admin/events/event-a/submissions";
  });
  await page.goto("/admin/events/event-a/submissions");
  await signInNavigation;
});
