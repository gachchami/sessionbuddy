import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const html = readFileSync(resolve(staticRoot, "co_speaker_invitation.html"), "utf8");
const productCss = readFileSync(resolve(staticRoot, "product.css"), "utf8");
const apiClient = readFileSync(resolve(staticRoot, "api_client.js"), "utf8");
const invitationScript = readFileSync(resolve(staticRoot, "co_speaker_invitation.js"), "utf8");
const invitationHtml = html
  .replace(/<link rel="stylesheet" href="\/product\/assets\/product\.css\?v=\d+">/, `<style>${productCss}</style>`)
  .replace(/<script src="\/app-shell\/assets\/api-client\.js\?v=\d+" defer><\/script>/, "")
  .replace(/<script src="\/co-speaker-invitations\/assets\/invitation\.js\?v=\d+" defer><\/script>/, "")
  .replace("</body>", `<script>${apiClient}</script><script>${invitationScript}</script></body>`);

const token = "co-speaker-confirmation-token-at-least-32-characters";
const invitation = {
  id: "contributor-a",
  submission_id: "submission-a",
  display_name: "Alex Speaker",
  email: "alex@example.test",
  role: "co_speaker",
  invitation_status: "pending",
  expires_at_ms: Date.UTC(2027, 4, 1),
  proposal_title: "Reliable Agent Systems",
  event_name: "AI Engineering Summit",
};

async function serveInvitation(page: Page, mutation: { responses: string[] }) {
  await page.route((url) => url.pathname === `/co-speaker-invitations/${token}`, (route) =>
    route.fulfill({ contentType: "text/html", body: invitationHtml }));
  await page.route(`**/api/v1/co-speaker-invitations/${token}`, (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(invitation) }));
  await page.route(`**/api/v1/co-speaker-invitations/${token}/*`, async (route) => {
    mutation.responses.push(route.request().url().split("/").at(-1) || "");
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ...invitation,
        invitation_status: mutation.responses.at(-1) === "accept" ? "accepted" : "declined",
        expires_at_ms: null,
      }),
    });
  });
}

test.describe("co-speaker invitation response confirmation", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  for (const width of [390, 1280]) {
    test(`decline is confirmed accessibly at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      const mutation = { responses: [] as string[] };
      await serveInvitation(page, mutation);
      await page.goto(`https://sessionbuddy.test/co-speaker-invitations/${token}`);
      await expect(page.getByRole("status")).toHaveText("Choose whether to join this proposal.");

      const decline = page.getByRole("button", { name: "Decline", exact: true });
      await decline.click();
      const dialog = page.getByRole("dialog", { name: "Decline this invitation?" });
      await expect(dialog).toBeVisible();
      await expect(dialog).toContainText("will stop working");
      await expect(dialog).toContainText("must send a new invitation");
      expect(mutation.responses).toEqual([]);
      const accessibility = await new AxeBuilder({ page }).include("#confirm-decline").analyze();
      expect(accessibility.violations).toEqual([]);

      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      await expect(decline).toBeFocused();
      expect(mutation.responses).toEqual([]);

      await decline.click();
      await dialog.getByRole("button", { name: "Keep invitation" }).click();
      await expect(decline).toBeFocused();
      expect(mutation.responses).toEqual([]);

      await decline.click();
      await dialog.getByRole("button", { name: "Decline invitation" }).click();
      await expect(page.getByRole("status")).toContainText("Invitation declined");
      expect(mutation.responses).toEqual(["decline"]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    });
  }

  test("accept remains a direct affirmative action", async ({ page }) => {
    const mutation = { responses: [] as string[] };
    await serveInvitation(page, mutation);
    await page.goto(`https://sessionbuddy.test/co-speaker-invitations/${token}`);

    await page.getByRole("button", { name: "Accept invitation" }).click();

    await expect(page.getByRole("status")).toContainText("Invitation accepted");
    expect(mutation.responses).toEqual(["accept"]);
    await expect(page.getByRole("dialog")).not.toBeVisible();
  });
});
