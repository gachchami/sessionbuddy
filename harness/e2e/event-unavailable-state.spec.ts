import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const root = resolve(__dirname, "../../src/sessionbuddy/static");

function pageSource(html: string, script: string) {
  return readFileSync(resolve(root, html), "utf8")
    .replace(/<link[^>]+>/g, "")
    .replace(/<script[^>]+><\/script>/g, "")
    .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, script), "utf8")}</script></body>`);
}

const session = {
  csrf_token: "csrf",
  organization_access: [{ permissions: ["manage"] }],
  event_access: [],
};

test("invalid event overview becomes a terminal unavailable state", async ({ page }) => {
  const html = pageSource("event_overview.html", "event_overview.js");
  await page.route("**/admin/events/not-an-event", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/not-an-event", (route) => route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "invalid event id" }) }));

  await page.goto("/admin/events/not-an-event");
  await expect(page.getByText("This event does not exist or is unavailable to your account.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to events" })).toHaveAttribute("href", "/admin");
  await expect(page.getByText("Finish your Call for Proposals")).toBeHidden();
  await expect(page.getByText("Event cover")).toBeHidden();
});

test("invalid event speaker directory removes the loading workspace", async ({ page }) => {
  const html = pageSource("speaker_directory.html", "speaker_directory.js");
  await page.route("**/admin/events/not-an-event/speakers", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [] }) }));
  await page.route("**/api/v1/admin/events/not-an-event", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));
  await page.route("**/api/v1/admin/events/not-an-event/speaker-targets", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "not found" }) }));

  await page.goto("/admin/events/not-an-event/speakers");
  await expect(page.getByText("This event does not exist or is unavailable to your account.")).toBeVisible();
  await expect(page.getByText("Loading people…")).toBeHidden();
  await expect(page.getByRole("search")).toBeHidden();
});

test("invalid onboarding event does not claim to reconnect", async ({ page }) => {
  const html = pageSource("admin_onboarding.html", "admin_onboarding.js");
  await page.route("**/admin/events/not-an-event/onboarding", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(session) }));
  await page.route("**/api/v1/admin/events/not-an-event/onboarding**", (route) => route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "invalid event id" }) }));

  await page.goto("/admin/events/not-an-event/onboarding");
  await expect(page.getByText("This event does not exist or is unavailable to your account.")).toBeVisible();
  await expect(page.getByText("Reconnecting")).toHaveCount(0);
  await expect(page.getByText(/last successful snapshot/i)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Filters" })).toBeHidden();
});
