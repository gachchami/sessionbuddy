import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Rendered UI contracts with synthetic API responses; not real-email UAT.
const root = resolve(__dirname, "../../src/sessionbuddy/static");
const token = "synthetic-invitation-preview-token-00000000";
const fixture = { id: "invitation-preview", organization_name: "AIEngineer", status: "pending", expires_at_ms: 2000000000000, needs_reissue: false, identity_matches: null };
async function arrange(page: Page, overrides = {}, signedIn = false) {
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    const files: Record<string, string> = {
      "/organization-admin-invitations": "organization_invitation.html",
      "/organization-admin-invitations/assets/invitation.js": "organization_invitation.js",
      "/product/assets/product.css": "product.css",
      "/app-shell/assets/api-client.js": "api_client.js",
      "/landing/assets/sessionbuddy-favicon.svg": "sessionbuddy-favicon.svg",
    };
    if (url.pathname === "/api/v1/auth/session") return route.fulfill({ status: signedIn ? 200 : 401, json: signedIn ? { authenticated: true, csrf_token: "synthetic-csrf" } : { error: { code: "unauthorized" } } });
    if (url.pathname.endsWith("/resolve")) return route.fulfill({ json: { ...fixture, ...overrides } });
    const file = files[url.pathname];
    if (!file) return route.fulfill({ status: 404, body: "Not found" });
    const type = file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".svg") ? "image/svg+xml" : "text/html";
    return route.fulfill({ contentType: type, body: readFileSync(resolve(root, file), "utf8") });
  });
  await page.goto(`http://localhost/organization-admin-invitations#token=${token}`);
  await expect(page.locator(".invitation-card")).toHaveAttribute("aria-busy", "false");
}

test("invitation explains both sign-in paths, hides signed-out account link, and is accessible", async ({ page }) => {
  await arrange(page);
  await expect(page.getByRole("heading", { name: "Join AIEngineer" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "First time here?" })).toBeVisible();
  await expect(page.getByText(/Use a verification link sent to your email to create your account/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Already have an account?" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in with password" })).toHaveAttribute("href", /redirect=%2Forganization-admin-invitations%3Finvitation%3Dinvitation-preview/);
  await expect(page.getByRole("link", { name: "Your account and invitations" })).toBeHidden();
  expect(page.url()).not.toContain(token);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
});

test("verification request uses a body token, shows progress, and preserves password alternative", async ({ page }) => {
  await arrange(page);
  let calls = 0;
  await page.route("**/api/v1/organization-admin-invitations/verification", async route => {
    calls++;
    expect(route.request().method()).toBe("POST");
    expect(route.request().url()).not.toContain(token);
    expect(route.request().postDataJSON()).toEqual({ token });
    await route.fulfill({ status: 202, json: { status: "requested" } });
  });
  await page.getByRole("button", { name: "Email me a verification link" }).click();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();
  await expect(page.getByText(/Open it within 15 minutes/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in with password" })).toBeVisible();
  expect(calls).toBe(1);
});

test("failed verification remains retryable without claiming email sent", async ({ page }) => {
  await arrange(page);
  await page.route("**/api/v1/organization-admin-invitations/verification", route => route.fulfill({ status: 503, json: { error: { message: "Email is temporarily unavailable." } } }));
  await page.getByRole("button", { name: "Email me a verification link" }).click();
  await expect(page.locator("#invitation-status")).toHaveAttribute("data-kind", "error");
  await expect(page.getByRole("button", { name: "Email me a verification link" })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Check your email" })).toBeHidden();
});

test("wrong account cannot accept and receives sign-out recovery", async ({ page }) => {
  await arrange(page, { identity_matches: false }, true);
  await expect(page.getByRole("button", { name: "Switch account" })).toBeVisible();
  await expect(page.getByText("AIEngineer", { exact: false })).toHaveCount(0);
  await expect(page.locator("#invitation-expiry")).toBeHidden();
  await expect(page.locator("#invitation-footer")).toBeHidden();
  await expect(page.getByRole("button", { name: "Accept invitation" })).toHaveCount(0);
});

test("switch account stays on page and preserves verification and password choices", async ({ page }) => {
  await arrange(page, { identity_matches: false }, true);
  const destination = page.url();
  await page.route("**/api/v1/session/logout", async route => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-csrf-token"]).toBe("synthetic-csrf");
    await page.route("**/api/v1/auth/session", r => r.fulfill({ status: 401, json: {} }));
    await page.route("**/api/v1/organization-admin-invitations/resolve", r => {
      expect(r.request().postDataJSON()).toEqual({ token });
      return r.fulfill({ json: fixture });
    });
    await route.fulfill({ status: 204 });
  });
  await page.getByRole("button", { name: "Switch account" }).click();
  await expect(page.getByRole("button", { name: "Email me a verification link" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Sign in with password" })).toHaveAttribute("href", /invitation%3Dinvitation-preview/);
  expect(page.url()).toBe(destination);
  await expect(page.getByRole("heading", { name: "Join AIEngineer" })).toBeFocused();
});

test("failed logout keeps private state and can be retried", async ({ page }) => {
  await arrange(page, { identity_matches: false }, true);
  await page.route("**/api/v1/session/logout", route => route.fulfill({ status: 503, json: { error: { message: "Sign out unavailable. Try again." } } }));
  await page.getByRole("button", { name: "Switch account" }).click();
  await expect(page.getByRole("button", { name: "Switch account" })).toBeEnabled();
  await expect(page.locator("#invitation-status")).toHaveAttribute("data-kind", "error");
  await expect(page.getByText("AIEngineer", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Email me a verification link" })).toHaveCount(0);
});

for (const state of ["expired", "revoked", "accepted", "declined"]) {
  test(`${state} invitation has no accepting or verification action`, async ({ page }) => {
    await arrange(page, { status: state });
    await expect(page.getByRole("status")).toHaveText(`This invitation is ${state}.`);
    await expect(page.getByRole("button")).toHaveCount(0);
  });
}

test("verified recipient sees separate acceptance and keyboard-cancellable decline", async ({ page }) => {
  await arrange(page, { identity_matches: true }, true);
  await expect(page.getByRole("button", { name: "Accept invitation" })).toBeVisible();
  await page.getByRole("button", { name: "Decline", exact: true }).click();
  await expect(page.getByRole("button", { name: "Confirm decline" })).toBeVisible();
  await page.getByRole("button", { name: "Confirm decline" }).press("Escape");
  await expect(page.getByRole("button", { name: "Decline", exact: true })).toBeVisible();
});

test("invalid and missing links show recovery without signed-out Account navigation", async ({ page }) => {
  await arrange(page);
  await page.route("**/api/v1/organization-admin-invitations/resolve", route => route.fulfill({ status: 404, json: { error: { code: "not_found" } } }));
  await page.goto(`http://localhost/organization-admin-invitations#token=${token}`);
  await page.reload();
  await expect(page.getByRole("status")).toContainText("Ask the admin who invited you for a new link");
  await page.goto("http://localhost/organization-admin-invitations");
  await page.reload();
  await expect(page.getByRole("status")).toHaveText("Open the invitation link from your email to continue.");
  await expect(page.getByRole("link", { name: "Your account and invitations" })).toBeHidden();
});

test("long organization names wrap without widening the invitation", async ({ page }) => {
  await arrange(page, { organization_name: "International Conference on Thoughtful Software and Community Organization" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: "Email me a verification link" })).toBeVisible();
});
