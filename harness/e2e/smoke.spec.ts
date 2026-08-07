import { expect, test } from "@playwright/test";

test.describe("public smoke checks", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("application responds with visible content", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.ok()).toBeTruthy();
    await expect(page.locator("body")).not.toBeEmpty();
  });
});

