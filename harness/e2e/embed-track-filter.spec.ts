import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

test("sharing preserves canonical track across every output and saved registry", async ({ page }) => {
  const root = resolve(__dirname, "../../src/sessionbuddy/static");
  const html = readFileSync(resolve(root, "event_workspace.html"), "utf8")
    .replace(/<link[^>]+>/g, "")
    .replace(/<script[^>]+><\/script>/g, "")
    .replace("</body>", ["api_client.js", "event_workspace.js"].map((file) => `<script>${readFileSync(resolve(root, file), "utf8")}</script>`).join("") + "</body>");
  await page.route("**/admin/events/event-a/workspace", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: { csrf_token: "csrf" } }));
  await page.route("**/api/v1/admin/events/event-a", (route) => route.fulfill({ json: { id: "event-a" } }));
  await page.route("**/api/v1/admin/events/event-a/agenda/tracks", (route) => route.fulfill({ json: { data: [{ id: "track-stable", name: "Platform & Infra", status: "active" }] } }));
  await page.goto("https://test/admin/events/event-a/workspace");
  await page.getByLabel("Track filter").selectOption("track-stable");
  for (const format of ["iframe", "html", "json", "xml", "ical"]) {
    await page.locator("#embed-output").selectOption(format);
    await expect(page.locator("#embed-code")).toHaveValue(/track_id=track-stable/);
    await expect(page.locator("#embed-code")).not.toHaveValue(/track=/);
  }
  await expect(page.locator("#open-embed")).toHaveAttribute("href", /track_id=track-stable/);
  await page.getByRole("button", { name: "Save preset", exact: true }).click();
  await expect(page.locator("#embed-registry")).toContainText("Platform & Infra");
  await page.reload();
  await expect(page.locator("#embed-registry")).toContainText("Platform & Infra");
});
