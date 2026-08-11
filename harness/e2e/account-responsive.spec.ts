import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const accountHtml = readFileSync(resolve(staticRoot, "account.html"), "utf8");
const accountJavaScript = readFileSync(resolve(staticRoot, "account.js"), "utf8");
const apiClientJavaScript = readFileSync(resolve(staticRoot, "api_client.js"), "utf8");
const appShellCss = readFileSync(resolve(staticRoot, "app_shell.css"), "utf8");
const appShellJavaScript = readFileSync(resolve(staticRoot, "app_shell.js"), "utf8");
const productCss = readFileSync(resolve(staticRoot, "product.css"), "utf8");

async function serveAccountPage(page: import("@playwright/test").Page) {
  await page.route(/^https?:\/\/[^/]+\/account(?:\?.*)?$/, (route) => route.fulfill({
    contentType: "text/html",
    body: accountHtml,
  }));
  await page.route("**/product/assets/product.css*", (route) => route.fulfill({ contentType: "text/css", body: productCss }));
  await page.route("**/app-shell/assets/app-shell.css*", (route) => route.fulfill({ contentType: "text/css", body: appShellCss }));
  await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({ contentType: "text/javascript", body: apiClientJavaScript }));
  await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({ contentType: "text/javascript", body: appShellJavaScript }));
  await page.route("**/account/assets/account.js*", (route) => route.fulfill({ contentType: "text/javascript", body: accountJavaScript }));
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
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible();
    await expect(page.getByRole("link", { name: "SessionBuddy" })).toBeVisible();
    await expect(page.getByText("Your account is up to date.")).toHaveCount(0);
    await expect(page.getByText("Nothing changes until you save your profile.")).toBeHidden();
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
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
    await expect(page.getByRole("link", { name: "SessionBuddy" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
    await expect(page.getByLabel("First name")).toHaveValue("Devang");
    await expect(page.getByLabel("Last name")).toHaveValue("Hanushali");
    await expect(page.locator("#status")).toContainText(
      "Review the suggested name fields",
    );
    await expect(page.getByText("Nothing changes until you save your profile.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save profile" })).toBeEnabled();
    expect(savedProfile).toBeNull();

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

  test("organization owner can manage exact access and recover event ownership", async ({ page }) => {
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
    const recoveryEvents = [{
      event_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      name: "Agent Platforms in Production",
      status: "active",
      current_owner_user_id: "departed-owner",
      current_owner_email: "departed@example.test",
    }, {
      event_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      name: "Historic AI Engineering Summit",
      status: "archived",
      current_owner_user_id: "historic-owner",
      current_owner_email: "historic@example.test",
    }];
    let recoveryRequests = 0;
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
    await page.route(`**/api/v1/admin/organizations/${organizationId}/ownership-recovery/events**`, (route) => {
      recoveryRequests += 1;
      const cursor = new URL(route.request().url()).searchParams.get("cursor");
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(cursor === "second-page"
          ? { data: [recoveryEvents[1]], next_cursor: null }
          : { data: [recoveryEvents[0]], next_cursor: "second-page" }),
      });
    });
    await page.route(`**/api/v1/admin/events/${recoveryEvents[0].event_id}/ownership-transfers`, (route) => {
      transferBody = route.request().postDataJSON() as Record<string, unknown>;
      recoveryEvents[0] = {
        ...recoveryEvents[0],
        current_owner_user_id: "new-owner",
        current_owner_email: String(transferBody.email),
      };
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          transfer_id: "transfer-id",
          event_id: recoveryEvents[0].event_id,
          previous_owner_user_id: "departed-owner",
          new_owner_user_id: "new-owner",
          previous_owner_permission: "manage",
          transferred_at_ms: 1,
        }),
      });
    });

    await page.goto("/account");

    await expect(page.getByRole("heading", { name: "Organization access" })).toBeVisible();
    await expect(page.getByText("They do not grant, revoke, or change access to any event.")).toBeVisible();
    const ownerRow = page.locator("li", { hasText: "owner@example.test" });
    await expect(ownerRow).toContainText("Owner");
    await expect(ownerRow.locator("select")).toHaveCount(0);

    await page.getByLabel("Account email").fill("new@example.test");
    await page.getByLabel("Organization permission to grant").selectOption("view");
    await page.getByRole("button", { name: "Grant organization access" }).click();
    await expect(page.getByText("new@example.test", { exact: true })).toBeVisible();
    expect(mutations[0]).toMatchObject({
      method: "POST",
      body: { email: "new@example.test", permission: "view" },
    });

    const managerRow = page.locator("li", { hasText: "manager@example.test" });
    await managerRow.getByLabel("Organization permission for manager@example.test").selectOption("edit");
    await managerRow.getByRole("button", { name: "Save access" }).click();
    expect(mutations[1]).toMatchObject({ method: "PATCH", body: { permission: "edit" } });

    await page.getByRole("button", { name: "Revoke organization access for manager@example.test" }).click();
    expect(mutations.filter((mutation) => mutation.method === "DELETE")).toHaveLength(0);
    await page.getByRole("button", { name: "Confirm revoke organization access for manager@example.test" }).click();
    await expect(page.getByText("Organization access revoked for manager@example.test. Event access was not changed.")).toBeVisible();
    expect(mutations.filter((mutation) => mutation.method === "DELETE")).toHaveLength(1);

    await expect(page.getByRole("heading", { name: "Event ownership recovery" })).toBeVisible();
    await expect(page.getByText(/does not grant you access to the event or its content/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      await page.evaluate(() => window.innerWidth),
    );
    const recoveryRow = page.locator("li", { hasText: "Agent Platforms in Production" });
    await expect(recoveryRow).toContainText("active · Current owner: departed@example.test");
    await expect(recoveryRow).toContainText(`Event ${recoveryEvents[0].event_id} · Owner account departed-owner`);
    await expect(recoveryRow.getByRole("link")).toHaveCount(0);

    await page.getByRole("button", { name: "Load more recovery events" }).click();
    await expect(page.getByText("Historic AI Engineering Summit", { exact: true })).toBeVisible();
    await recoveryRow.getByLabel("New owner email for Agent Platforms in Production").fill("new-owner@example.test");
    await recoveryRow.getByLabel("Ownership transfer reason for Agent Platforms in Production").fill("The event owner left the team.");
    await recoveryRow.getByLabel("Keep departed@example.test as Can manage after transfer").check();
    await recoveryRow.getByRole("button", { name: "Review ownership transfer" }).click();

    expect(transferBody).toBeNull();
    const confirmation = recoveryRow.getByRole("alert");
    await expect(confirmation).toBeFocused();
    await expect(confirmation).toContainText("new-owner@example.test will own Agent Platforms in Production");
    await recoveryRow.getByRole("button", { name: "Confirm transfer" }).click();

    await expect(page.getByRole("status").first()).toHaveText(
      "Event ownership transferred to new-owner@example.test. Event content and other access were not changed.",
    );
    expect(transferBody).toEqual({
      email: "new-owner@example.test",
      reason: "The event owner left the team.",
      grant_previous_owner_manage: true,
    });
    await expect(page.getByText(/Current owner: new-owner@example.test/)).toBeVisible();

    const requestsBeforeManageReload = recoveryRequests;
    session.organization_access[0].permissions = ["manage"];
    await page.reload();
    await expect(page.getByRole("heading", { name: "Organization access" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Event ownership recovery" })).toHaveCount(0);
    expect(recoveryRequests).toBe(requestsBeforeManageReload);
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
