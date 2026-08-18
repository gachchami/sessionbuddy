import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// One-click demo persona sign-in on /sign-in. Every API interaction is mocked
// so this pins the browser-side contract: the section only exists when the
// server confirms demo mode, one click posts a role (never an identity), only
// the clicked control enters a loading state, and each persona lands on its
// own workspace.

const personas = {
  data: [
    { role: "organizer", label: "Sign in as demo organizer", description: "Manage the event, proposals, speakers, and agenda.", destination: "/admin" },
    { role: "reviewer", label: "Sign in as demo reviewer", description: "Score the proposals assigned to you.", destination: "/reviews" },
    { role: "speaker", label: "Sign in as demo speaker", description: "Manage your proposal, profile, tasks, and files.", destination: "/speaker" },
  ],
};

async function withDemoMode(page: import("@playwright/test").Page, enabled: boolean) {
  await page.route("**/api/v1/auth/demo-personas", (route) =>
    enabled
      ? route.fulfill({ contentType: "application/json", body: JSON.stringify(personas) })
      : route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "not_found", message: "Not found" } }),
        }));
}

function captureSignIn(page: import("@playwright/test").Page) {
  const requests: Array<Record<string, unknown>> = [];
  page.route("**/api/v1/auth/demo-sign-in", async (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    requests.push(body);
    const destination = personas.data.find((p) => p.role === body.role)?.destination || "/";
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ user_id: "demo-user", csrf_token: "demo-csrf", redirect_path: destination }),
    });
  });
  return requests;
}

test.describe("demo persona access", () => {
  test("the section is absent when demo mode is unavailable", async ({ page }) => {
    await withDemoMode(page, false);
    await page.goto("/sign-in");
    await expect(page.locator("#sign-in-form")).toBeVisible();
    // Absent, not merely hidden: the capability must not be discoverable.
    await expect(page.locator(".demo-access")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /sign in as demo/i })).toHaveCount(0);
  });

  test("all three personas are offered with an explicit destination", async ({ page }) => {
    await withDemoMode(page, true);
    await page.goto("/sign-in");
    const buttons = page.getByRole("button", { name: /sign in as demo/i });
    await expect(buttons).toHaveCount(3);
    for (const persona of personas.data) {
      const button = page.getByRole("button", { name: persona.label });
      await expect(button).toBeVisible();
      await expect(button).toHaveAccessibleDescription(persona.description);
    }
    // The ordinary credential form is untouched.
    await expect(page.locator("#sign-in-form")).toBeVisible();
    await expect(page.locator("#send-sign-in-link")).toBeVisible();
  });

  for (const persona of personas.data) {
    test(`one click signs in as ${persona.role} and opens ${persona.destination}`, async ({ page }) => {
      await withDemoMode(page, true);
      const requests = captureSignIn(page);
      await page.goto("/sign-in");
      await page.getByRole("button", { name: persona.label }).click();
      await page.waitForURL(`**${persona.destination}`);
      expect(requests).toHaveLength(1);
      expect(requests[0].role).toBe(persona.role);
      // The browser never names the account it signs in as.
      expect(requests[0]).not.toHaveProperty("email");
      expect(requests[0]).not.toHaveProperty("user_id");
      expect(requests[0]).not.toHaveProperty("password");
    });
  }

  test("replacing an account clears stale persona routing before navigation", async ({ page }) => {
    await withDemoMode(page, true);
    captureSignIn(page);
    const visited: string[] = [];
    page.on("request", (request) => visited.push(new URL(request.url()).pathname));
    await page.route("**/admin", (route) => route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Organizer workspace</title><h1>Dana organizer</h1>",
    }));

    await page.goto("/sign-in");
    await page.evaluate(() => {
      sessionStorage.setItem("sessionbuddy:shell-session", JSON.stringify({
        stored_at: Date.now(),
        session: {
          active_role: "reviewer",
          default_role: "reviewer",
          account_roles: ["reviewer"],
        },
      }));
      sessionStorage.setItem("sessionbuddy:document-recovery:reviews", "2");
    });

    await page.getByRole("button", { name: "Sign in as demo organizer" }).click();
    await page.waitForURL("**/admin");
    await expect(page.getByRole("heading", { name: "Dana organizer" })).toBeVisible();
    expect(visited).not.toContain("/reviews");
    await expect.poll(() => page.evaluate(() => ({
      shell: sessionStorage.getItem("sessionbuddy:shell-session"),
      recovery: sessionStorage.getItem("sessionbuddy:document-recovery:reviews"),
    }))).toEqual({ shell: null, recovery: null });
  });

  test("password sign-in checks the authoritative session before following a stale workspace", async ({ page }) => {
    await withDemoMode(page, false);
    await page.route("**/api/v1/auth/password/sign-in", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        user_id: "revoked-manager",
        csrf_token: "fresh-csrf",
        redirect_path: "/admin",
      }),
    }));
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        workspace_state: "organizer_authority_missing",
        workspace_path: null,
      }),
    }));
    await page.route("**/account?workspace=recovery", (route) => route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><title>Account recovery</title><h1>Choose an available workspace</h1>",
    }));

    await page.goto("/sign-in?redirect=%2Fadmin");
    await page.getByLabel("Email address").fill("revoked-manager@example.test");
    await page.locator("#sign-in-password").fill("private recovery password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();

    await page.waitForURL("**/account?workspace=recovery");
    await expect(page.getByRole("heading", { name: "Choose an available workspace" })).toBeVisible();
  });

  test("an existing session offers continue or deliberate account replacement", async ({ page }) => {
    await withDemoMode(page, false);
    await page.route("**/api/v1/auth/session", (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        display_name: "Alex Speaker",
        csrf_token: "existing-csrf",
        workspace_path: "/speaker",
      }),
    }));
    let signedOut = false;
    await page.route("**/api/v1/session/logout", async (route) => {
      expect(route.request().method()).toBe("POST");
      expect(route.request().headers()["x-csrf-token"]).toBe("existing-csrf");
      signedOut = true;
      await route.fulfill({ status: 204 });
    });

    await page.goto("/sign-in");
    await expect(page.getByRole("heading", { name: "Already signed in" })).toBeVisible();
    await expect(page.locator("#sign-in-form")).toBeHidden();
    await page.getByRole("button", { name: "Sign out and use another account" }).click();
    await expect.poll(() => signedOut).toBe(true);
    await expect(page.locator("#sign-in-form")).toBeVisible();
  });

  test("only the clicked control enters a loading state", async ({ page }) => {
    await withDemoMode(page, true);
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/v1/auth/demo-sign-in", async (route) => {
      await held;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ user_id: "demo-user", csrf_token: "c", redirect_path: "/reviews" }),
      });
    });
    await page.goto("/sign-in");
    const reviewer = page.locator('[data-demo-role="reviewer"]');
    await reviewer.click();
    await expect(reviewer).toHaveAttribute("data-loading", "true");
    // The control keeps its card layout while loading; the label slot names
    // the persona being opened rather than collapsing the whole card.
    await expect(reviewer).toContainText("Opening reviewer…");
    await expect(page.getByRole("button", { name: "Sign in as demo organizer" }))
      .not.toHaveAttribute("data-loading", "true");
    release();
  });

  test("a failure reports a generic message and restores the controls", async ({ page }) => {
    await withDemoMode(page, true);
    await page.route("**/api/v1/auth/demo-sign-in", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "unavailable", message: "binding missing" } }),
      }));
    await page.goto("/sign-in");
    const speaker = page.getByRole("button", { name: "Sign in as demo speaker" });
    await speaker.click();
    await expect(page.locator(".demo-access__status")).toHaveText(/Demo access is currently unavailable/);
    // No backend detail reaches the visitor.
    await expect(page.locator(".demo-access__status")).not.toHaveText(/binding missing/);
    await expect(speaker).toBeEnabled();
  });

  test("the controls are reachable and operable from the keyboard", async ({ page }) => {
    await withDemoMode(page, true);
    const requests = captureSignIn(page);
    await page.goto("/sign-in");
    const organizer = page.getByRole("button", { name: "Sign in as demo organizer" });
    await organizer.focus();
    await expect(organizer).toBeFocused();
    await page.keyboard.press("Enter");
    await page.waitForURL("**/admin");
    expect(requests[0].role).toBe("organizer");
  });

  test("the demo section has no accessibility violations", async ({ page }) => {
    await withDemoMode(page, true);
    await page.goto("/sign-in");
    await expect(page.locator(".demo-access")).toBeVisible();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test("the demo section is usable on a narrow viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await withDemoMode(page, true);
    await page.goto("/sign-in");
    for (const persona of personas.data) {
      const button = page.getByRole("button", { name: persona.label });
      await expect(button).toBeVisible();
      const box = await button.boundingBox();
      expect(box?.width).toBeGreaterThan(0);
      // Comfortably above the 24px minimum target size.
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(32);
    }
  });
});
