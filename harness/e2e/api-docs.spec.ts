import { expect, test } from "@playwright/test";

test.describe("read-only API documentation", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("browses the checked contract without sending mutations", async ({ page }) => {
    const nonGetRequests: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") {
        nonGetRequests.push(`${request.method()} ${request.url()}`);
      }
    });

    const response = await page.goto("/docs");

    expect(response?.status()).toBe(200);
    await expect(page).toHaveTitle("API contract · SessionBuddy");
    await expect(page.getByRole("heading", { name: "API contract, without side effects." })).toBeVisible();
    await expect(page.getByText("No request runner. No credentials entered. No data changed.")).toBeVisible();
    await expect(page.locator("#result-count")).toHaveText(/\d+ operations shown/);
    await expect(page.getByRole("link", { name: "View JSON" })).toHaveAttribute("href", "/api/v1/openapi.json");

    const initialCount = Number.parseInt((await page.locator("#result-count").textContent()) || "0", 10);
    await page.getByLabel("Search path, summary, or tag").fill("agenda");
    await expect(page.locator("#result-count")).toHaveText(/\d+ operations shown/);
    const searchCount = Number.parseInt((await page.locator("#result-count").textContent()) || "0", 10);
    expect(searchCount).toBeGreaterThan(0);
    expect(searchCount).toBeLessThan(initialCount);

    await page.getByLabel("Search path, summary, or tag").fill("");
    const agendaFilter = page.locator('.tag-filter[data-tag="agenda"]');
    await agendaFilter.click();
    await expect(agendaFilter).toHaveAttribute("aria-current", "true");
    await expect(page.locator("#result-count")).toHaveText(/18 operations shown/);

    const firstOperation = page.locator(".operation-card").first();
    await firstOperation.getByText("Contract details").click();
    await expect(firstOperation.getByText("Operation ID")).toBeVisible();
    expect(nonGetRequests).toEqual([]);
  });
});
