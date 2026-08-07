import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.describe("public experience accessibility", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("home page has no automatically detectable serious violations", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("body")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    const blocking = results.violations.filter(({ impact }) =>
      impact === "critical" || impact === "serious"
    );
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
  });
});

