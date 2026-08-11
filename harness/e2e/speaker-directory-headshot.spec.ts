import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const root = resolve(__dirname, "../../src/sessionbuddy/static");
const pageHtml = readFileSync(resolve(root, "speaker_directory.html"), "utf8")
  .replace(/<link[^>]+>/g, "")
  .replace(/<script[^>]+><\/script>/g, "")
  .replace("</head>", `<style>${readFileSync(resolve(root, "product.css"), "utf8")}</style></head>`)
  .replace("</body>", `<script>${readFileSync(resolve(root, "api_client.js"), "utf8")}</script><script>${readFileSync(resolve(root, "speaker_directory.js"), "utf8")}</script></body>`);

for (const width of [1280, 390]) {
  test(`organizer previews and uploads an event-scoped speaker headshot at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    let uploaded = false;
    await page.route("**/admin/events/event-a/speakers/es-a", (route) => route.fulfill({ contentType: "text/html", body: pageHtml }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ csrf_token: "csrf", organization_access: [{ permissions: ["manage"] }], event_access: [] }) }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ id: "org-a", name: "Org A" }] }) }));
    await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ id: "event-a", organization_id: "org-a", name: "DevFlow" }) }));
    await page.route("**/api/v1/admin/events/event-a/speaker-targets", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: [{ event_speaker_id: "es-a", person_id: null, user_id: "user-a", email: "speaker@example.test", display_name: "Priya Raman", job_title: "Engineer", company: "Example", biography: "Bio", location: "", links: [], version: 1, selection_status: "accepted", proposal_title: "A talk" }] }) }));
    await page.route("**/api/v1/admin/events/event-a/speakers/es-a/headshot*", async (route) => {
      if (route.request().method() === "PUT") {
        uploaded = true;
        expect(route.request().headers()["x-csrf-token"]).toBe("csrf");
        expect(route.request().headers()["content-type"]).toBe("image/png");
        await route.fulfill({ status: 204 });
      } else await route.fulfill({ status: 404 });
    });

    await page.goto("/admin/events/event-a/speakers/es-a");
    const form = page.locator("#speaker-headshot-form");
    await expect(form).toBeVisible();
    await form.locator('input[type="file"]').setInputFiles({ name: "speaker.png", mimeType: "image/png", buffer: Buffer.from("png") });
    await form.getByRole("button", { name: "Save headshot" }).click();
    await expect.poll(() => uploaded).toBe(true);
    await expect(page.locator("#speaker-headshot-status")).toHaveText("Headshot saved.");
    await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
    const results = await new AxeBuilder({ page }).include("#speaker-detail").analyze();
    expect(results.violations.filter((item) => ["serious", "critical"].includes(item.impact || ""))).toEqual([]);
  });
}
