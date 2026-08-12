import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const accountHtml = readFileSync(resolve(staticRoot, "account.html"), "utf8");
const accountJavaScript = readFileSync(resolve(staticRoot, "account.js"), "utf8");
const organizationHtml = readFileSync(resolve(staticRoot, "organization_admin.html"), "utf8");
const organizationJavaScript = readFileSync(resolve(staticRoot, "organization_admin.js"), "utf8");
const apiClientJavaScript = readFileSync(resolve(staticRoot, "api_client.js"), "utf8");
const appShellCss = readFileSync(resolve(staticRoot, "app_shell.css"), "utf8");
const appShellJavaScript = readFileSync(resolve(staticRoot, "app_shell.js"), "utf8");
const productCss = readFileSync(resolve(staticRoot, "product.css"), "utf8");

async function serveAccountPage(page: import("@playwright/test").Page) {
  await page.route(/^https?:\/\/[^/]+\/(?:account|admin\/organization)(?:\?.*)?$/, (route) => route.fulfill({
    contentType: "text/html",
    body: new URL(route.request().url()).pathname === "/admin/organization" ? organizationHtml : accountHtml,
  }));
  await page.route("**/product/assets/product.css*", (route) => route.fulfill({ contentType: "text/css", body: productCss }));
  await page.route("**/app-shell/assets/app-shell.css*", (route) => route.fulfill({ contentType: "text/css", body: appShellCss }));
  await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({ contentType: "text/javascript", body: apiClientJavaScript }));
  await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({ contentType: "text/javascript", body: appShellJavaScript }));
  await page.route("**/account/assets/account.js*", (route) => route.fulfill({ contentType: "text/javascript", body: accountJavaScript }));
  await page.route("**/admin/organization/assets/organization.js*", (route) => route.fulfill({ contentType: "text/javascript", body: organizationJavaScript }));
}

test.describe("account profile responsive design", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("profile stays aligned and usable at phone width", async ({ page }) => {
    await serveAccountPage(page);
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const session = {
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "admin@example.test",
      display_name: "Admin User",
      profile_complete: true,
      csrf_token: "browser-test-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_access: [{
        organization_id: organizationId,
        organization_name: "Example Events",
        permissions: ["owner"],
      }],
      event_access: [],
    };
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(session),
    }));
    await page.route("**/api/v1/account/profile", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        email: session.email,
        first_name: "Admin",
        last_name: "User",
        job_title: null,
        company: null,
        time_zone: "UTC",
        description: null,
        website_url: null,
        linkedin_url: null,
        x_url: null,
        roles: ["organizer"],
        headshot_url: null,
        has_password: true,
        version: 1,
      }),
    }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ id: organizationId, name: "Example Events", status: "active", version: 1 }],
      }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}/access-grants**`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ user_id: session.user_id, email: session.email, permission: "owner", status: "active" }],
      }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}/ownership-recovery/events**`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [], next_cursor: null }),
    }));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/account");
    await expect(page.getByRole("heading", { name: "Profile", exact: true })).toBeVisible();

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await expect(page.locator("#workspace-navigation")).toHaveCount(1);
    await expect(page.locator(".sb-topbar").getByRole("link", { name: "SessionBuddy" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Speaker portal" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "My reviews" })).toHaveCount(0);
    await expect(page.getByText("Your account is up to date.")).toHaveCount(0);
    await expect(page.getByText("Nothing changes until you save your profile.")).toBeHidden();
    await expect(page.getByRole("navigation", { name: "Workspace navigation" })).toBeHidden();
    await expect(page.getByRole("radio", { name: /Organizer/ })).toBeChecked();
    await expect(page.getByRole("button", { name: "Save default role" })).toBeDisabled();

    const panel = await page.locator(".account-profile-panel").boundingBox();
    const headshot = await page.locator(".account-headshot").boundingBox();
    const choose = await page.locator(".account-headshot__choose").boundingBox();
    const save = await page.getByRole("button", { name: "Save profile" }).boundingBox();
    expect(panel && headshot && choose && save).toBeTruthy();
    expect(headshot!.x).toBeGreaterThanOrEqual(panel!.x);
    expect(headshot!.x + headshot!.width).toBeLessThanOrEqual(panel!.x + panel!.width + 1);
    expect(choose!.height).toBeLessThanOrEqual(52);
    expect(save!.height).toBeLessThanOrEqual(52);

    await page.screenshot({ path: ".local/account-mobile.png", fullPage: true });
  });

  test("a legacy display name becomes an editable, unsaved profile draft", async ({ page }) => {
    await serveAccountPage(page);
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const session = {
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "admin@example.test",
      display_name: "Devang Hanushali",
      profile_complete: false,
      csrf_token: "browser-test-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_access: [{
        organization_id: organizationId,
        organization_name: "Example Events",
        permissions: ["owner"],
      }],
      event_access: [],
    };
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(session),
    }));
    let savedProfile: Record<string, unknown> | null = null;
    const legacyProfile = {
      email: session.email,
      first_name: null,
      last_name: null,
      display_name: session.display_name,
      job_title: null,
      company: "Example Events",
      time_zone: "Asia/Kolkata",
      description: null,
      website_url: null,
      linkedin_url: null,
      x_url: null,
      roles: ["organizer"],
      headshot_url: null,
      has_password: false,
      profile_complete: false,
      version: 1,
    };
    await page.route("**/api/v1/account/profile", (route) => {
      if (route.request().method() === "PATCH") {
        const profilePayload = route.request().postDataJSON() as Record<string, unknown>;
        savedProfile = profilePayload;
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            ...legacyProfile,
            ...profilePayload,
            display_name: `${profilePayload.first_name} ${profilePayload.last_name}`,
            profile_complete: true,
            version: 2,
          }),
        });
      }
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(legacyProfile),
      });
    });
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ id: organizationId, name: "Example Events", status: "active", version: 1 }],
      }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}/access-grants**`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ user_id: session.user_id, email: session.email, permission: "owner", status: "active" }],
      }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}/ownership-recovery/events**`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [], next_cursor: null }),
    }));

    await page.goto("/account?onboarding=1&next=%2Fadmin");

    await expect(page.locator("#workspace-navigation")).toHaveCount(1);
    await expect(page.locator(".sb-topbar").getByRole("link", { name: "SessionBuddy" })).toBeVisible();
    const workspaceNavigation = page.getByRole("navigation", { name: "Workspace navigation" });
    if ((page.viewportSize()?.width || 0) <= 760) {
      await expect(workspaceNavigation).toBeHidden();
    } else {
      await expect(workspaceNavigation).toBeVisible();
    }
    await expect(page.getByRole("link", { name: "Speaker portal" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "My reviews" })).toHaveCount(0);
    await expect(page.getByLabel("First name")).toHaveValue("Devang");
    await expect(page.getByLabel("Last name")).toHaveValue("Hanushali");
    await expect(page.locator("#status")).toContainText(
      "Review the suggested name fields",
    );
    await expect(page.getByText("Nothing changes until you save your profile.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save profile" })).toBeEnabled();
    expect(savedProfile).toBeNull();

    await page.getByLabel("LinkedIn").fill("htttps://linkedin.com");
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect(page.getByLabel("LinkedIn")).toHaveJSProperty(
      "validationMessage",
      "Enter a complete HTTPS URL, such as https://example.com, without a username or password.",
    );
    expect(savedProfile).toBeNull();
    await page.getByLabel("LinkedIn").fill("");

    await page.getByLabel("First name").fill("Devang H.");
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect.poll(() => savedProfile).toMatchObject({
      first_name: "Devang H.",
      last_name: "Hanushali",
      version: 1,
    });
  });

  test("password configuration failure explains how to save the remaining profile", async ({ page }) => {
    await serveAccountPage(page);
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const session = {
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "admin@example.test",
      display_name: "Admin User",
      profile_complete: true,
      csrf_token: "browser-test-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_access: [{
        organization_id: organizationId,
        organization_name: "Example Events",
        permissions: ["owner"],
      }],
      event_access: [],
    };
    const profile = {
      email: session.email,
      first_name: "Admin",
      last_name: "User",
      display_name: session.display_name,
      job_title: null,
      company: "Example Events",
      time_zone: "UTC",
      description: null,
      website_url: null,
      linkedin_url: null,
      x_url: null,
      roles: ["organizer"],
      headshot_url: null,
      has_password: false,
      profile_complete: true,
      version: 1,
    };
    const submittedProfiles: Record<string, unknown>[] = [];
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(session),
    }));
    await page.route("**/api/v1/account/profile", (route) => {
      if (route.request().method() === "PATCH") {
        const payload = route.request().postDataJSON() as Record<string, unknown>;
        submittedProfiles.push(payload);
        if (payload.password) {
          return route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({
              error: { code: "service_unavailable", message: "Service unavailable" },
            }),
          });
        }
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({ ...profile, ...payload, version: 2 }),
        });
      }
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(profile) });
    });
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ id: organizationId, name: "Example Events", status: "active", version: 1 }],
      }),
    }));

    await page.goto("/account");
    await page.getByLabel("Job title").fill("Program chair");
    await page.getByLabel("New password", { exact: true }).fill("a strong private passphrase");
    await page.getByLabel("Confirm new password").fill("a strong private passphrase");
    await page.getByRole("button", { name: "Save profile" }).click();

    const status = page.getByRole("status");
    await expect(status).toBeFocused();
    await expect(status).toHaveText(
      "Your profile was not saved because password sign-in is temporarily unavailable. "
      + "Leave both password fields blank to save the rest of your profile now, or ask "
      + "the administrator to check password configuration.",
    );
    await expect(page.getByLabel("New password", { exact: true })).toHaveValue("a strong private passphrase");
    await expect(page.getByRole("button", { name: "Save profile" })).toBeEnabled();

    await page.getByLabel("New password", { exact: true }).fill("");
    await page.getByLabel("Confirm new password").fill("");
    await page.getByRole("button", { name: "Save profile" }).click();

    await expect(status).toHaveText("Profile saved.");
    await expect(page.getByRole("button", { name: "✓ Saved" })).toBeDisabled();
    await page.getByLabel("Job title").fill("Lead program chair");
    await expect(page.getByRole("button", { name: "Save profile" })).toBeEnabled();
    expect(submittedProfiles).toHaveLength(2);
    expect(submittedProfiles[0]).toMatchObject({
      job_title: "Program chair",
      password: "a strong private passphrase",
    });
    expect(submittedProfiles[1]).toMatchObject({
      job_title: "Program chair",
      password: null,
      password_confirmation: null,
    });
  });

  test("organization owner manages the owner and admin grid on its dedicated page", async ({ page }) => {
    await serveAccountPage(page);
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const session = {
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "owner@example.test",
      display_name: "Organization Owner",
      profile_complete: true,
      csrf_token: "browser-test-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_access: [{
        organization_id: organizationId,
        organization_name: "Example Events",
        permissions: ["owner"],
      }],
      event_access: [],
    };
    const profile = {
      email: session.email,
      first_name: "Organization",
      last_name: "Owner",
      display_name: session.display_name,
      job_title: null,
      company: "Example Events",
      time_zone: "UTC",
      description: null,
      website_url: null,
      linkedin_url: null,
      x_url: null,
      roles: ["organizer"],
      headshot_url: null,
      has_password: true,
      profile_complete: true,
      version: 1,
    };
    let grants = [
      { user_id: session.user_id, email: session.email, permission: "owner", status: "active" },
      { user_id: "manager-user", email: "manager@example.test", permission: "manage", status: "active" },
    ];
    const mutations: { method: string; path: string; body: Record<string, unknown> | null }[] = [];
    let transferBody: Record<string, unknown> | null = null;
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(session),
    }));
    await page.route("**/api/v1/account/profile", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(profile),
    }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        data: [{ id: organizationId, name: "Example Events", status: "active", version: 1 }],
      }),
    }));
    await page.route(`**/api/v1/admin/organizations/${organizationId}/access-grants**`, (route) => {
      const request = route.request();
      const method = request.method();
      const path = new URL(request.url()).pathname;
      if (method === "GET") {
        return route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: grants }) });
      }
      const body = request.postDataJSON() as Record<string, unknown> | null;
      mutations.push({ method, path, body });
      if (method === "POST") {
        const created = { user_id: "new-user", email: body!.email, permission: body!.permission, status: "active" };
        grants = [...grants, created];
        return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(created) });
      }
      if (method === "PATCH") {
        grants = grants.map((grant) => grant.user_id === "manager-user"
          ? { ...grant, permission: String(body!.permission) }
          : grant);
        return route.fulfill({ contentType: "application/json", body: JSON.stringify(grants[1]) });
      }
      grants = grants.filter((grant) => grant.user_id !== "manager-user");
      return route.fulfill({ status: 204, body: "" });
    });
    await page.route(`**/api/v1/admin/organizations/${organizationId}/ownership-transfers`, (route) => {
      transferBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          transfer_id: "transfer-id",
          organization_id: organizationId,
          previous_owner_user_id: session.user_id,
          new_owner_user_id: "manager-user",
          previous_owner_permission: "manage",
          transferred_at_ms: 1,
        }),
      });
    });
    await page.route(`**/api/v1/admin/organizations/${organizationId}/activities`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ data: [{
        activity_id: "A101",
        actor_id: "U789",
        actor_name: "Organization Owner",
        operation: "create",
        resource_type: "resource_access_grant",
        resource_id: "X456",
        event_id: null,
        occurred_at_ms: Date.UTC(2026, 7, 16, 10, 0),
      }] }),
    }));

    await page.goto("/admin/organization");

    await expect(page).toHaveTitle("Organization settings · SessionBuddy");
    await expect(page.getByRole("heading", { name: "Organization settings", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Profile" })).toBeHidden();
    await expect(page.getByRole("heading", { name: "Roles and Access" })).toBeHidden();
    await expect(page.getByRole("heading", { name: "Organizers" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Organization activity" })).toBeVisible();
    const ownerRow = page.locator("li", { hasText: "owner@example.test" });
    await expect(ownerRow).toContainText("Owner");
    await expect(ownerRow.locator("select")).toHaveCount(0);
    await expect(page.locator("li", { hasText: "manager@example.test" })).toContainText("Admin");

    await page.getByLabel("Account email").fill("new@example.test");
    await page.getByRole("button", { name: "Add admin" }).click();
    await expect(page.getByText("new@example.test", { exact: true })).toBeVisible();
    expect(mutations[0]).toMatchObject({
      method: "POST",
      body: { email: "new@example.test", permission: "manage" },
    });

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      await page.evaluate(() => window.innerWidth),
    );
    await page.getByLabel("New owner’s admin email").fill("manager@example.test");
    await page.getByRole("button", { name: "Review ownership transfer" }).click();
    expect(transferBody).toBeNull();
    await page.getByRole("button", { name: "Confirm transfer to manager@example.test" }).click();
    await expect(page.getByRole("status").first()).toHaveText(
      "Organization ownership transferred to manager@example.test. You remain an Admin.",
    );
    expect(transferBody).toEqual({ email: "manager@example.test" });
    await expect(page.getByRole("heading", { name: "Event ownership recovery" })).toHaveCount(0);
  });

  test("organization edit permission does not reveal access administration", async ({ page }) => {
    await serveAccountPage(page);
    const organizationId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    let organizationRequests = 0;
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        user_id: "editor-user",
        email: "editor@example.test",
        display_name: "Organization Editor",
        profile_complete: true,
        csrf_token: "browser-test-csrf",
        account_roles: ["organizer"],
        active_role: "organizer",
        default_role: "organizer",
        organization_access: [{
          organization_id: organizationId,
          organization_name: "Example Events",
          permissions: ["edit"],
        }],
        event_access: [],
      }),
    }));
    await page.route("**/api/v1/account/profile", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        email: "editor@example.test",
        first_name: "Organization",
        last_name: "Editor",
        display_name: "Organization Editor",
        job_title: null,
        company: null,
        time_zone: "UTC",
        description: null,
        website_url: null,
        linkedin_url: null,
        x_url: null,
        roles: ["organizer"],
        headshot_url: null,
        has_password: true,
        profile_complete: true,
        version: 1,
      }),
    }));
    await page.route("**/api/v1/admin/organizations**", (route) => {
      organizationRequests += 1;
      return route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
    });

    await page.goto("/account");

    await expect(page.getByRole("heading", { name: "Organization access" })).toHaveCount(0);
    await expect(page.locator("#organization-settings")).toBeHidden();
    expect(organizationRequests).toBe(0);
  });
});
