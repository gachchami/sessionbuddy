import "dotenv/config";
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.SESSIONBUDDY_BASE_URL;

// PW_FAST=1 trades failure artifacts (video/trace) for speed — useful while
// iterating on a spec, or on small machines where per-test recording
// dominates the run time. Full-fidelity artifacts stay the default.
// PW_WORKERS overrides the worker count (Playwright's default is half the
// cores, which on a 2-core box quietly serializes the whole suite).
const fast = Boolean(process.env.PW_FAST);

export default defineConfig({
  testDir: "./e2e",
  outputDir: ".local/test-results",
  fullyParallel: true,
  workers: process.env.CI ? 1 : process.env.PW_WORKERS ? Number(process.env.PW_WORKERS) : undefined,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { outputFolder: ".local/playwright-report", open: "never" }]],
  use: {
    baseURL,
    // Application code uses the viewer's locale. Tests that assert exact date
    // copy need a declared viewer locale instead of inheriting the host default.
    locale: "en-US",
    trace: fast ? "off" : "retain-on-failure",
    screenshot: "only-on-failure",
    video: fast ? "off" : "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chrome", use: { ...devices["Pixel 7"] } },
  ],
});
