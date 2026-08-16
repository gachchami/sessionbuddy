import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const source = (name: string) => readFileSync(resolve(staticRoot, name), "utf8");
const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

async function serve(page: import("@playwright/test").Page) {
  let postedRole = "";
  await page.route(new RegExp(`/admin/events/${eventId}/reviewers(?:\\?.*)?$`), (route) => route.fulfill({
    contentType: "text/html", body: source("access_admin.html"),
  }));
  for (const [pattern, file, type] of [
    ["**/product/assets/product.css*", "product.css", "text/css"],
    ["**/app-shell/assets/app-shell.css*", "app_shell.css", "text/css"],
    ["**/app-shell/assets/api-client.js*", "api_client.js", "text/javascript"],
    ["**/app-shell/assets/app-shell.js*", "app_shell.js", "text/javascript"],
    ["**/admin/access/assets/access.js*", "access_admin.js", "text/javascript"],
  ] as const) {
    await page.route(pattern, (route) => route.fulfill({ contentType: type, body: source(file) }));
  }
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: {
    authenticated: true, user_id: "owner", email: "owner@example.test", display_name: "Owner",
    profile_complete: true, csrf_token: "csrf", account_roles: ["organizer"], active_role: "organizer",
    default_role: "organizer", organization_access: [{ organization_id: "org", organization_name: "Org", permissions: ["owner"] }],
    event_access: [],
  } }));
  await page.route(new RegExp(`/api/v1/admin/events/${eventId}$`), (route) => route.fulfill({
    json: { id: eventId, organization_id: "org", name: "Example Conference" },
  }));
  await page.route(`**/api/v1/admin/events/${eventId}/evaluators?email=*`, (route) => {
    const email = new URL(route.request().url()).searchParams.get("email");
    return route.fulfill({ json: { data: email === "reviewer@example.test"
      ? [{ user_id: "reviewer", display_name: "Existing Reviewer" }] : [] } });
  });
  await page.route(`**/api/v1/admin/events/${eventId}/invitations`, async (route) => {
    if (route.request().method() === "POST") {
      postedRole = String(route.request().postDataJSON().role);
      return route.fulfill({ status: 201, json: { id: "new", event_id: eventId, email: "new@example.test", display_name: "New Reviewer", role: postedRole, status: "pending", access_url: "https://example.test/auth/verify#token=NEW" } });
    }
    return route.fulfill({ json: { data: [
      { id: "accepted", event_id: eventId, email: "reviewer@example.test", display_name: "Rina Reviewer", job_title: "Engineering Director", company: "Review Labs", role: "evaluator", status: "accepted" },
      { id: "pending", event_id: eventId, email: "pending@example.test", display_name: "Pat Pending", role: "evaluator", status: "pending" },
      { id: "speaker", event_id: eventId, email: "speaker@example.test", role: "speaker", status: "pending" },
      { id: "legacy", event_id: eventId, email: "legacy@example.test", role: "event_admin", status: "accepted" },
    ] } });
  });
  return { postedRole: () => postedRole };
}

test.describe("event invitation RBAC", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("shows participant eligibility without legacy event administration", async ({ page }) => {
    await serve(page);
    await page.goto(`/admin/events/${eventId}/reviewers`);
    await expect(page.getByRole("heading", { name: "Reviewers", exact: true })).toBeVisible();
    await expect(page.getByRole("table", { name: "Event reviewers" })).toBeVisible();
    await expect(page.getByText("Rina Reviewer", { exact: true })).toBeVisible();
    await expect(page.getByText("Engineering Director · Review Labs", { exact: true })).toBeVisible();
    await expect(page.getByText("Eligible", { exact: true })).toBeVisible();
    await expect(page.getByText("Invitation pending", { exact: true })).toBeVisible();
    await expect(page.getByText("reviewer@example.test")).toBeVisible();
    await expect(page.getByText("pending@example.test")).toBeVisible();
    await expect(page.getByText("speaker@example.test")).toHaveCount(0);
    await expect(page.getByText("legacy@example.test")).toHaveCount(0);
    await expect(page.getByText("Resource access")).toHaveCount(0);
    await expect(page.getByText("Event ownership")).toHaveCount(0);
    expect((await new AxeBuilder({ page }).include("#main").analyze()).violations).toEqual([]);
  });

  test("searches exact-event reviewers and offers an email invitation when absent", async ({ page }) => {
    const state = await serve(page);
    await page.setViewportSize({ width: 320, height: 740 });
    await page.goto(`/admin/events/${eventId}/reviewers`);
    await page.getByLabel("Find reviewer by email").fill("reviewer@example.test");
    await page.getByRole("button", { name: "Search" }).click();
    await expect(page.getByText(/Existing Reviewer is eligible/)).toBeVisible();
    await page.getByLabel("Find reviewer by email").fill("new@example.test");
    await page.getByRole("button", { name: "Search" }).click();
    await page.getByRole("button", { name: "Invite new@example.test" }).click();
    await expect(page.getByRole("dialog", { name: "Invite reviewer" })).toBeVisible();
    await expect(page.getByRole("dialog").getByLabel("Email address")).toHaveValue("new@example.test");
    await page.getByRole("dialog").getByLabel("Full name").fill("New Reviewer");
    await page.getByRole("button", { name: "Send invitation" }).click();
    await expect.poll(state.postedRole).toBe("evaluator");
    await expect(page.locator("html")).toHaveJSProperty("scrollWidth", 320);
  });
});
