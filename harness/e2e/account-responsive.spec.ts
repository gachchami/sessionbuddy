import { expect, test } from "@playwright/test";

test.describe("account profile responsive design", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("profile stays aligned and usable at phone width", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/sign-in?redirect=%2Faccount");
    await page.getByLabel("Email").fill("user0@example.test");
    await page.getByLabel("Password").fill("user0-local-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page).toHaveURL(/\/account$/);
    await expect(page.getByRole("heading", { name: "Profile", exact: true })).toBeVisible();

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await expect(page.getByText("Your account is up to date.")).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Workspace navigation" })).toBeHidden();
    await expect(page.getByRole("radio", { name: /Organizer/ })).toBeChecked();
    await expect(page.getByRole("button", { name: "Save default role" })).toBeDisabled();

    const panel = await page.locator(".account-profile-panel").boundingBox();
    const headshot = await page.locator(".account-headshot").boundingBox();
    const choose = await page.locator(".account-headshot__choose").boundingBox();
    const save = await page.getByRole("button", { name: "Save headshot" }).boundingBox();
    expect(panel && headshot && choose && save).toBeTruthy();
    expect(headshot!.x).toBeGreaterThanOrEqual(panel!.x);
    expect(headshot!.x + headshot!.width).toBeLessThanOrEqual(panel!.x + panel!.width + 1);
    expect(Math.abs(choose!.width - save!.width)).toBeLessThanOrEqual(2);
    expect(choose!.height).toBeLessThanOrEqual(52);
    expect(save!.height).toBeLessThanOrEqual(52);

    await page.screenshot({ path: ".local/account-mobile.png", fullPage: true });
  });
});
