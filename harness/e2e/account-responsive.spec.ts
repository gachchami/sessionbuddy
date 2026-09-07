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
const activityFormatJavaScript = readFileSync(resolve(staticRoot, "activity_format.js"), "utf8");
const productCss = readFileSync(resolve(staticRoot, "product.css"), "utf8");

const workspaceContract = (
  role: "organizer" | "reviewer" | "speaker",
  workspaceState: string,
  workspacePath: string | null,
) => ({
  workspace_state: workspaceState,
  workspace_path: workspacePath,
  usable_personas: workspacePath
    ? [{ role, workspace_state: workspaceState, workspace_path: workspacePath }]
    : [],
});

async function serveAccountPage(page: import("@playwright/test").Page) {
  await page.route(/^https?:\/\/[^/]+\/(?:account|admin\/organization)(?:\?.*)?$/, (route) => route.fulfill({
    contentType: "text/html",
    body: new URL(route.request().url()).pathname === "/admin/organization" ? organizationHtml : accountHtml,
  }));
  await page.route("**/product/assets/product.css*", (route) => route.fulfill({ contentType: "text/css", body: productCss }));
  await page.route("**/app-shell/assets/app-shell.css*", (route) => route.fulfill({ contentType: "text/css", body: appShellCss }));
  await page.route("**/app-shell/assets/api-client.js*", (route) => route.fulfill({ contentType: "text/javascript", body: apiClientJavaScript }));
  await page.route("**/app-shell/assets/app-shell.js*", (route) => route.fulfill({ contentType: "text/javascript", body: appShellJavaScript }));
  await page.route("**/app-shell/assets/activity-format.js*", (route) => route.fulfill({ contentType: "text/javascript", body: activityFormatJavaScript }));
  await page.route("**/account/assets/account.js*", (route) => route.fulfill({ contentType: "text/javascript", body: accountJavaScript }));
  await page.route("**/admin/organization/assets/organization.js*", (route) => route.fulfill({ contentType: "text/javascript", body: organizationJavaScript }));
}

test.describe("account profile responsive design", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  for (const persona of ["reviewer", "speaker"] as const) {
    test(`incomplete ${persona} onboarding has no workspace escape links`, async ({ page }) => {
      await serveAccountPage(page);
      const session = {
        authenticated: true,
        user_id: `${persona}-user`,
        email: `${persona}@example.test`,
        display_name: "New account",
        profile_complete: false,
        csrf_token: "browser-test-csrf",
        account_roles: [persona],
        active_role: persona,
        default_role: persona,
        ...workspaceContract(persona, "profile_incomplete", "/account"),
        organization_access: [],
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
          first_name: null,
          last_name: null,
          display_name: session.display_name,
          job_title: null,
          company: null,
          time_zone: "UTC",
          description: null,
          website_url: null,
          linkedin_url: null,
          x_url: null,
          roles: [persona],
          headshot_url: null,
          has_password: false,
          profile_complete: false,
          version: 1,
        }),
      }));

      await page.goto("/account");

      await expect(page.getByRole("heading", { name: "Complete your profile" })).toBeVisible();
      await expect(page.getByRole("link", { name: "SessionBuddy" })).toHaveCount(0);
      await expect(page.getByRole("link", { name: "Speaker portal" })).toHaveCount(0);
      await expect(page.getByRole("link", { name: "Calls for proposals" })).toHaveCount(0);
      await expect(page.getByRole("link", { name: "My reviews" })).toHaveCount(0);
      await page.getByLabel("Account menu for New account").click();
      await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
    });
  }

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
      ...workspaceContract("organizer", "ready", "/admin"),
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
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/account");
    await expect(page.getByRole("heading", { name: "Profile", exact: true })).toBeVisible();

    const viewportPadding = await page.evaluate(() =>
      Number.parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop),
    );
    const chrome = await page.locator(".sb-topbar").boundingBox();
    expect(chrome).not.toBeNull();
    expect(viewportPadding).toBeGreaterThanOrEqual(chrome!.y + chrome!.height);

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await expect(page.locator("#workspace-navigation")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Open navigation", exact: true })).toBeVisible();
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

  test("keeps account navigation after one transient shell session failure", async ({ page }) => {
    await serveAccountPage(page);
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
      ...workspaceContract("organizer", "ready", "/admin"),
      organization_access: [{
        organization_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        organization_name: "Example Events",
        permissions: ["owner"],
      }],
      event_access: [],
    };
    let sessionRequests = 0;
    await page.route("**/api/v1/auth/session", async (route) => {
      sessionRequests += 1;
      if (sessionRequests === 1) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        await route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Try again." } } });
        return;
      }
      await route.fulfill({ json: session });
    });
    await page.route("**/api/v1/account/profile", (route) => route.fulfill({ json: {
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
    } }));

    await page.goto("/account");
    await expect(page.getByRole("heading", { name: "Profile", exact: true })).toBeVisible();
    await expect(page.getByText("Temporarily unavailable")).toHaveCount(0);
    await page.getByLabel("Account menu for Admin User").click();
    await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
    expect(sessionRequests).toBeGreaterThanOrEqual(2);
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
      ...workspaceContract("organizer", "profile_incomplete", "/account"),
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
        Object.assign(
          session,
          { profile_complete: true },
          workspaceContract("organizer", "ready", "/admin"),
        );
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

    await page.goto("/account");

    await expect(page.locator("#workspace-navigation")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "SessionBuddy" })).toHaveCount(0);
    await expect(page.locator(".sb-topbar .sb-global-brand:not(a)")).toContainText("SessionBuddy");
    await expect(page.locator(".sb-topbar").getByRole("link", { name: "SessionBuddy" })).toHaveCount(0);
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
    await expect(page.locator(".sb-sidebar .sb-app-brand"))
      .toHaveAttribute("href", "/admin");
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
      ...workspaceContract("organizer", "ready", "/admin"),
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

  test("password rotation uses the replacement CSRF token for the queued headshot", async ({ page }) => {
    await serveAccountPage(page);
    const session = {
      authenticated: true,
      user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      email: "owner@example.test",
      display_name: "Password Owner",
      profile_complete: true,
      csrf_token: "old-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      ...workspaceContract("organizer", "organizer_authority_missing", null),
      organization_access: [],
      event_access: [],
    };
    const profile = {
      email: session.email,
      first_name: "Password",
      last_name: "Owner",
      display_name: session.display_name,
      job_title: null,
      company: null,
      time_zone: "UTC",
      description: null,
      website_url: null,
      linkedin_url: null,
      x_url: null,
      public_profile_enabled: false,
      roles: ["organizer"],
      headshot_url: null,
      has_password: false,
      profile_complete: true,
      version: 1,
    };
    let headshotCsrf = "";
    let headshotContentType = "";
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: session }));
    await page.route("**/api/v1/account/profile", (route) => {
      if (route.request().method() === "PATCH") {
        return route.fulfill({
          json: { ...profile, has_password: true, version: 2, csrf_token: "replacement-csrf" },
        });
      }
      return route.fulfill({ json: { ...profile, has_password: true, version: 2 } });
    });
    await page.route("**/api/v1/account/headshot", (route) => {
      headshotCsrf = route.request().headers()["x-csrf-token"] || "";
      headshotContentType = route.request().headers()["content-type"] || "";
      return route.fulfill({ json: { stored: true } });
    });

    await page.goto("/account");
    await page.getByLabel("Choose image").setInputFiles({
      name: "headshot.jpeg",
      mimeType: "image/jpg",
      buffer: Buffer.from("/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==", "base64"),
    });
    await expect(page.locator("#headshot-preview")).toBeVisible();
    await expect(page.getByRole("status")).toContainText("Profile photo selected: headshot.jpeg");
    await expect(page.getByRole("button", { name: "Save profile" })).toBeEnabled();
    await page.getByLabel("New password", { exact: true }).fill("a strong private passphrase");
    await page.getByLabel("Confirm new password").fill("a strong private passphrase");
    await page.getByRole("button", { name: "Save profile" }).click();

    await expect.poll(() => headshotCsrf).toBe("replacement-csrf");
    expect(headshotContentType).toBe("image/jpeg");
    await expect(page.getByRole("status")).toContainText(
      "Profile and password saved. Your other sessions were signed out.",
    );
  });

  test("organization owner manages the owner and admin grid on its dedicated page", async ({ page }) => {
    // The isolated harness uses http://worker, unlike the HTTPS product origin.
    // Supply the secure-context UUID API for this mocked UI contract test.
    await page.addInitScript(() => {
      if (!crypto.randomUUID) Object.defineProperty(crypto, "randomUUID", {
        value: () => Array.from(crypto.getRandomValues(new Uint8Array(16)),
          (byte) => byte.toString(16).padStart(2, "0")).join(""),
      });
    });
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
      ...workspaceContract("organizer", "ready", "/admin"),
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
    const invitations: Record<string, unknown>[] = [];
    await page.route(`**/api/v1/admin/organizations/${organizationId}/admin-invitations`, (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({ json: { data: invitations } });
      }
      const body = route.request().postDataJSON();
      mutations.push({ method: "POST", path: new URL(route.request().url()).pathname, body });
      const invitation = {
        id: "pending-admin", email: body.email, status: "pending", needs_reissue: false,
        expires_at_ms: Date.now() + 259200000, delivery: { status: "queued", reason: null },
      };
      invitations.push(invitation);
      return route.fulfill({ json: invitation });
    });
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
        const created = { user_id: "new-user", email: body!.email, permission: "manage", status: "active" };
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

    await page.getByRole("textbox", { name: "Email", exact: true }).fill("new@example.test");
    await page.getByRole("button", { name: "Invite admin" }).click();
    await expect(page.locator("li", { hasText: "new@example.test" })).toContainText("pending");
    expect(mutations[0]).toMatchObject({
      method: "POST",
      body: { email: "new@example.test" },
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

  test("a two-organization account edits one organization at a time", async ({ page }) => {
    await serveAccountPage(page);
    const first = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const second = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
    const session = {
      authenticated: true,
      user_id: "multi-owner",
      email: "owner@example.test",
      display_name: "Organization Owner",
      profile_complete: true,
      csrf_token: "browser-test-csrf",
      account_roles: ["organizer"],
      active_role: "organizer",
      default_role: "organizer",
      organization_id: first,
      organization_name: "Alpha Events",
      ...workspaceContract("organizer", "ready", "/admin"),
      organization_access: [
        { organization_id: first, organization_name: "Alpha Events", permissions: ["owner"] },
        { organization_id: second, organization_name: "Beta Summits", permissions: ["manage"] },
      ],
      event_access: [],
    };
    const names: Record<string, string> = { [first]: "Alpha Events", [second]: "Beta Summits" };
    const requested: string[] = [];
    const activity = (id: string, index: number, resourceType: string, subject: string) => ({
      activity_id: `${id.slice(0, 4)}-${index}`,
      actor_id: "U1",
      actor_name: names[id],
      operation: "update",
      resource_type: resourceType,
      resource_id: `R${index}`,
      subject_name: subject,
      event_id: null,
      occurred_at_ms: Date.UTC(2026, 7, 16, 10, index),
    });
    const activities: Record<string, unknown[]> = {
      [first]: [activity(first, 1, "event", "Alpha Day"), activity(first, 2, "proposal", "Alpha Talk")],
      [second]: [
        activity(second, 1, "event", "Beta Conf"),
        activity(second, 2, "event", "Beta Meetup"),
        activity(second, 3, "proposal", "Beta Talk"),
        activity(second, 4, "resource_access_grant", "editor@example.test"),
      ],
    };
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({ json: session }));
    await page.route("**/api/v1/admin/organizations", (route) => route.fulfill({ json: { data: [
      { id: first, name: "Alpha Events", status: "active", version: 1, event_count: 2, pending_review_count: 0 },
      { id: second, name: "Beta Summits", status: "active", version: 1, event_count: 1, pending_review_count: 0 },
    ] } }));
    await page.route(`**/api/v1/admin/organizations/${second}`, (route) => route.fulfill({
      json: { id: second, name: route.request().postDataJSON().name, status: "active", version: 2, event_count: 1, pending_review_count: 0 },
    }));
    await page.route(/\/api\/v1\/admin\/organizations\/[^/]+\/(access-grants|admin-invitations|activities)$/, async (route) => {
      const url = new URL(route.request().url());
      const [, , , , , organizationId, resource] = url.pathname.split("/");
      requested.push(`${organizationId}/${resource}`);
      if (resource === "access-grants") {
        return route.fulfill({ json: { data: [{ user_id: "multi-owner", email: "owner@example.test", permission: "owner", status: "active" }] } });
      }
      if (resource === "admin-invitations") return route.fulfill({ json: { data: [] } });
      if (organizationId === first) {
        // The first organization answers slowly so its rows would land after a switch.
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      return route.fulfill({ json: { data: activities[organizationId] } });
    });

    await page.goto(`/admin/organization?organization_id=${second}`);

    const title = page.locator("#organization-settings-title");
    await expect(title).toHaveText("Beta Summits");
    const switcher = page.locator("#organization-switcher");
    await expect(switcher).toBeVisible();
    await expect(switcher).toHaveValue(second);
    await expect(page.locator("#organization-activity-summary")).toHaveText("Showing all 4 changes.");
    // Invitations are requested only after access grants resolve, so poll.
    await expect.poll(() => [...new Set(requested)].sort()).toEqual([
      `${second}/access-grants`, `${second}/activities`, `${second}/admin-invitations`,
    ].sort());

    await page.locator("#organization-activity-filters").getByRole("button", { name: /^Events/ }).click();
    await expect(page.locator("#organization-activity-list .organization-activity-item")).toHaveCount(2);
    await expect(page.locator("#organization-activity-summary")).toHaveText("Showing 2 of the 4 most recent changes.");

    await page.locator("#organization-settings form").first().getByLabel("Organization name").fill("Beta Summits Renamed");
    await page.getByRole("button", { name: "Save organization" }).click();
    await expect(title).toHaveText("Beta Summits Renamed");
    await expect(switcher.locator("option[value=\"" + second + "\"]")).toHaveText("Beta Summits Renamed");

    // Switch to the slow first organization, then straight back: the late
    // response for the first organization must never paint the second's list.
    await switcher.selectOption(first);
    await expect(page).toHaveURL(new RegExp(`organization_id=${first}`));
    await expect(title).toHaveText("Alpha Events");
    await switcher.selectOption(second);
    await expect(page).toHaveURL(new RegExp(`organization_id=${second}`));
    await expect(title).toHaveText("Beta Summits Renamed");
    await expect(title).toBeFocused();
    await expect(page.getByRole("status").first()).toHaveText("Now showing Beta Summits Renamed.");
    await page.waitForTimeout(2000);
    await expect(page.locator("#organization-activity-list .organization-activity-item")).toHaveCount(4);
    await expect(page.locator("#organization-activity-list")).not.toContainText("Alpha Day");
    await expect(page.locator("#organization-activity-list")).toContainText("Beta Conf");
  });

  test("a retired organization edit fact does not reveal access administration", async ({ page }) => {
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
