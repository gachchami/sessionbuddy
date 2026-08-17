import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(root, "speaker_directory.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</head>", `<style>${readFileSync(resolve(root, "product.css"), "utf8")}</style></head>`)
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "biography_disclosure.js"), "utf8")}</script><script>${readFileSync(resolve(root, "speaker_directory.js"), "utf8")}</script></body>`);
const onePixelPng = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");

for (const width of [1280, 390]) {
  test(`organizer previews and uploads an event-scoped speaker headshot at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    let uploaded = false;
    await page.route("**/admin/events/event-a/speakers/es-a", (route) => route.fulfill({ contentType: "text/html", body: pageHtml }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ csrf_token: "csrf", organization_access: [{ permissions: ["manage"] }], event_access: [] }) }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: "org-a", name: "Org A" }] }) }));
    await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", organization_id: "org-a", name: "DevFlow" }) }));
    await page.route("**/api/v1/admin/events/event-a/speaker-targets", (route) => route.abort("failed"));
    await page.route("**/api/v1/admin/events/event-a/speakers/es-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ event_speaker_id: "es-a", person_id: null, user_id: "user-a", email: "speaker@example.test", display_name: "Priya Raman", job_title: "Engineer", company: "Example", biography: "Bio", biography_source: "account", biography_override: null, location: "", links: [], version: 1, participation_version: 1, lifecycle_status: "onboarding", selection_status: "accepted", confirmation_status: "confirmed", proposal_title: "A talk", can_edit: true }) }));
    await page.route("**/api/v1/admin/events/event-a/speakers/es-a/headshot*", async (route) => {
      if (route.request().method() === "PUT") {
        uploaded = true;
        expect(route.request().headers()["x-csrf-token"]).toBe("csrf");
        expect(route.request().headers()["content-type"]).toBe("image/png");
        await route.fulfill({ status: 204 });
      } else if (uploaded) await route.fulfill({ status: 200, contentType: "image/png", body: onePixelPng });
      else await route.fulfill({ status: 404 });
    });

    await page.goto("/admin/events/event-a/speakers/es-a");
    await expect(page.locator("#speaker-profile-view")).toBeVisible();
    await expect(page.locator("#speaker-edit-view")).toBeHidden();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const form = page.locator("#speaker-headshot-form");
    await expect(form).toBeVisible();
    await form.locator('input[type="file"]').setInputFiles({ name: "speaker.png", mimeType: "image/png", buffer: Buffer.from("png") });
    await form.getByRole("button", { name: "Save headshot" }).click();
    await expect.poll(() => uploaded).toBe(true);
    await expect(page.locator("#speaker-headshot-status")).toHaveText("Headshot saved and verified.");
    await expect(page.locator("#speaker-headshot-preview")).toBeVisible();
    await expect(page.locator("#speaker-headshot-fallback")).toBeHidden();
    await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
    const results = await new AxeBuilder({ page }).include("#speaker-detail").analyze();
    expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact || ""))).toEqual([]);
  });
}

test("speaker profile validation is form-scoped and identifies the invalid link", async ({ page }) => {
  let patches = 0;
  await page.route("**/admin/events/event-a/speakers/es-a", (route) => route.fulfill({ contentType: "text/html", body: pageHtml }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ csrf_token: "csrf", organization_access: [{ permissions: ["manage"] }], event_access: [] }) }));
  await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: "org-a", name: "Org A" }] }) }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", organization_id: "org-a", name: "DevFlow" }) }));
  await page.route("**/api/v1/admin/events/event-a/speaker-targets", (route) => route.abort("failed"));
  await page.route("**/api/v1/admin/events/event-a/speakers/es-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ event_speaker_id: "es-a", person_id: "person-a", user_id: "user-a", email: "speaker@example.test", display_name: "Priya Raman", job_title: "Engineer", company: "Example", biography: "Account bio", biography_source: "account", biography_override: null, location: "", links: [], version: 1, participation_version: 1, lifecycle_status: "onboarding", selection_status: "accepted", confirmation_status: "pending", proposal_title: "A talk" }) }));
  await page.route("**/api/v1/admin/events/event-a/speakers/es-a/organizer-notes", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [], version: 1 }) }));
  // Playwright evaluates routes last-registered-first. This mutation handler
  // must fall back for GET so the singular-read fixture above remains active.
  await page.route("**/api/v1/admin/events/event-a/speakers/es-a", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    patches += 1;
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "validation_failed",
          message: "Value error, link must use an absolute HTTP or HTTPS URL",
          field: "links.0",
          metadata: { details: [{ field: "links.0", message: "Value error, link must use an absolute HTTP or HTTPS URL", type: "value_error" }] },
        },
        request_id: "request-a",
      }),
    });
  });

  await page.goto("/admin/events/event-a/speakers/es-a");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const form = page.locator("#speaker-form");
  const links = form.locator('[name="links"]');
  await links.fill("@priyabuilds");
  await form.getByRole("button", { name: "Save speaker details" }).click();
  await expect(links).toBeFocused();
  await expect(page.locator(`#${await links.getAttribute("aria-errormessage")}`)).toContainText("full http:// or https:// URL");
  expect(patches).toBe(0);

  await links.fill("https://x.com/priyabuilds");
  await form.getByRole("button", { name: "Save speaker details" }).click();
  await expect.poll(() => patches).toBe(1);
  await expect(links).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator(`#${await links.getAttribute("aria-errormessage")}`)).toContainText("absolute HTTP or HTTPS URL");
  await expect(page.locator("#invite-speaker-dialog")).not.toHaveAttribute("open", "");
});
