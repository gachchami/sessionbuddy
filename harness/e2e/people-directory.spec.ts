import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const organizationId = "11111111-1111-4111-8111-111111111111";

test.describe("platform People directory", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test.beforeEach(async ({ page }) => {
    const session = {
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "admin@example.test",
      display_name: "Alex Admin",
      profile_complete: true,
      csrf_token: "browser-test-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_access: [{
        organization_id: organizationId,
        organization_name: "SessionBuddy Eval",
        permissions: ["owner"],
      }],
      event_access: [],
    };
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify(session),
    }));
    await page.route("**/api/v1/session", (route) => route.fulfill({
      contentType: "application/json", body: JSON.stringify(session),
    }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{ id: organizationId, name: "SessionBuddy Eval" }] }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}/people`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        organization_id: organizationId,
        data: [
          {
            person_id: "person-alex", user_id: "user-alex", public_profile_enabled: false,
            email: "alex@example.test",
            display_name: "Alex Morgan", job_title: "Community Lead", company: "Northstar Labs",
            biography: "", location: "", links: [], version: 1,
            organization_roles: ["Organizer"], event_associations: [], participations: [],
          },
          {
            person_id: "person-riley", user_id: "user-riley", public_profile_enabled: true,
            email: "riley@example.test",
            display_name: "Riley Chen", job_title: "Principal Engineer", company: "Orbit Systems",
            biography: "", location: "", links: [], version: 1,
            organization_roles: ["Speaker"],
            event_associations: [{ event_id: "event-one", event_name: "AI Summit", role: "Reviewer", status: "accepted" }],
            participations: [{ event_id: "event-two", event_name: "Cloud Forum", event_speaker_id: "speaker-riley", selection_status: "accepted", proposal_title: "Reliable agents" }],
          },
          {
            person_id: "", user_id: "user-riley", public_profile_enabled: true,
            email: "riley@example.test",
            display_name: "Riley Chen", job_title: "", company: "",
            biography: "", location: "", links: [], version: 1,
            organization_roles: ["Organizer", "Reviewer"],
            event_associations: [{ event_id: "event-one", event_name: "AI Summit", role: "Reviewer", status: "accepted" }],
            participations: [],
          },
        ],
      }),
    }));
  });

  test("renders table results and honors every advertised search field", async ({ page }) => {
    await page.goto("/admin/people");
    await expect(page.getByRole("heading", { name: "People" })).toBeVisible();
    await expect(page.getByRole("table", { name: "People" })).toBeVisible();
    await expect(page.getByRole("row", { name: /Riley Chen/ })).toContainText("2 events");
    await expect(page.getByRole("row", { name: /Riley Chen/ })).toContainText("Organizer");
    await expect(page.getByRole("row", { name: /Riley Chen/ })).toContainText("Reviewer");
    await expect(page.getByRole("row", { name: /Riley Chen/ })).toContainText("Speaker");
    await expect(page.getByText("Alex Morgan", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "View Alex Morgan's public profile" })).toHaveCount(0);

    const search = page.getByRole("searchbox", { name: "Search people…" });
    const field = page.getByRole("combobox", { name: "Search field" });
    for (const [mode, query, expected] of [
      ["name", "riley", "Riley Chen"],
      ["email", "alex@example", "Alex Morgan"],
      ["company", "northstar", "Alex Morgan"],
    ] as const) {
      await field.selectOption(mode);
      await search.fill(query);
      await expect(page.getByRole("row", { name: new RegExp(expected) })).toBeVisible();
      await expect(page.locator(".people-table-row")).toHaveCount(1);
    }

    await search.fill("");
    await field.selectOption("all");
    await page.getByRole("combobox", { name: "Role" }).selectOption("Reviewer");
    await expect(page.locator(".people-table-row")).toHaveCount(1);
    await expect(page.getByRole("row", { name: /Riley Chen/ })).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations.filter(({ impact }) => impact === "critical" || impact === "serious"))
      .toEqual([]);
  });

  test("keeps the result table usable at a mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/admin/people");
    await expect(page.locator(".people-table-row")).toHaveCount(2);
    await expect(page.getByRole("searchbox", { name: "Search people…" })).toBeVisible();
    await expect(page.getByRole("row", { name: /Riley Chen/ })).toBeVisible();
  });

  test("opens a privacy-safe public identity card from a People row", async ({ page }) => {
    await page.route("**/api/v1/public/people/user-riley", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "user-riley",
        display_name: "Riley Chen",
        job_title: "Principal Engineer",
        company: "Orbit Systems",
        biography: Array.from(
          { length: 18 },
          (_, index) => `Chapter ${index + 1} covers reliable systems for humans and agents.`,
        ).join("\n\n"),
        website_url: "https://riley.example.test",
        linkedin_url: "https://www.linkedin.com/in/riley-chen",
        x_url: null,
        headshot_url: null,
      }),
    }));
    await page.goto("/admin/people");
    await page.getByRole("link", { name: "View Riley Chen's public profile" }).click();
    await expect(page).toHaveURL(/\/people\/user-riley$/);
    await expect(page.getByRole("heading", { name: "Riley Chen" })).toBeVisible();
    await expect(page.getByText("Principal Engineer · Orbit Systems")).toBeVisible();
    const biographyToggle = page.locator("#profile-biography-toggle");
    await expect(biographyToggle).toHaveAccessibleName("Show more");
    await expect(biographyToggle).toHaveAttribute("aria-expanded", "false");
    await biographyToggle.click();
    await expect(biographyToggle).toHaveAccessibleName("Show less");
    await expect(biographyToggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("link", { name: "https://riley.example.test" })).toBeVisible();
    await expect(page.getByRole("link", { name: "https://www.linkedin.com/in/riley-chen" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Website", exact: true })).toHaveCount(0);
    await expect(page.getByText("riley@example.test")).toHaveCount(0);
    await expect(page.getByText("Reviewer")).toHaveCount(0);

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations.filter(({ impact }) => impact === "critical" || impact === "serious"))
      .toEqual([]);

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(".public-profile-card")).toBeVisible();
    await expect(page.locator("body")).not.toHaveCSS("overflow-x", "auto");
  });
});
