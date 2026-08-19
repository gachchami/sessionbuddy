import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const eventId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const staticRoot = resolve(__dirname, "../../src/sessionbuddy/static");
const speakerGalleryHtml = readFileSync(resolve(staticRoot, "speaker_gallery.html"), "utf8");
const speakerGalleryJs = readFileSync(resolve(staticRoot, "speaker_gallery.js"), "utf8");
const mastheadJs = readFileSync(resolve(staticRoot, "public_event_masthead.js"), "utf8");
const biographyDisclosureJs = readFileSync(resolve(staticRoot, "biography_disclosure.js"), "utf8");
const apiClientJs = readFileSync(resolve(staticRoot, "api_client.js"), "utf8");
const productCss = readFileSync(resolve(staticRoot, "product.css"), "utf8");

const longBiography = Array.from(
  { length: 18 },
  (_, index) => `Paragraph ${index + 1} explains the speaker's work and perspective.`,
).join("\n\n");

const speakers = [
  { id: "6", display_name: "Ada Lovelace" },
  { id: "4", display_name: "Martin Luther King Jr." },
  { id: "1", display_name: "Cher" },
  { id: "5", display_name: "García Márquez, Gabriel" },
  { id: "3", display_name: "Grace Brewster Murray Hopper" },
  { id: "2", display_name: "Anne Hopper" },
  { id: "7", display_name: "anne hopper" },
].map((speaker) => ({
  ...speaker,
  job_title: "Research lead",
  company: "Example Labs",
  biography: speaker.id === "5" ? longBiography : "A concise speaker biography.",
  location: "",
  links: [],
  headshot_url: null as string | null,
  sessions: [{
    id: `session-${speaker.id}`,
    title: `Session by ${speaker.display_name}`,
    starts_at_ms: 1787581800000,
    room_name: "Main stage",
    track_name: "Engineering",
  }],
}));

async function mount(page: Page, path: string, entries = speakers) {
  await page.route(
    (url) => url.pathname === path,
    (route) => route.fulfill({ contentType: "text/html", body: speakerGalleryHtml }),
  );
  await page.route("**/product/assets/product.css*", (route) =>
    route.fulfill({ contentType: "text/css", body: productCss }));
  await page.route("**/app-shell/assets/api-client.js*", (route) =>
    route.fulfill({ contentType: "text/javascript", body: apiClientJs }));
  await page.route("**/public/assets/event-masthead.js*", (route) =>
    route.fulfill({ contentType: "text/javascript", body: mastheadJs }));
  await page.route("**/public/assets/biography-disclosure.js*", (route) =>
    route.fulfill({ contentType: "text/javascript", body: biographyDisclosureJs }));
  await page.route("**/gallery/assets/gallery.js*", (route) =>
    route.fulfill({ contentType: "text/javascript", body: speakerGalleryJs }));
  await page.route(`**/api/v1/public/events/${eventId}/speakers`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        event: {
          id: eventId,
          name: "Example Summit",
          time_zone: "UTC",
          accent_color: "#7847d6",
          logo_url: null,
          cover_image_url: null,
          website_url: "https://example.test",
        },
        data: entries,
      }),
    }));
  await page.goto(path);
  await expect(page.getByRole("status")).toContainText(`${entries.length} speakers`);
}

test.describe("public speaker surfaces", () => {
  test.skip(!process.env.SESSIONBUDDY_BASE_URL, "Set SESSIONBUDDY_BASE_URL to run browser tests");

  test("sorts by surname and differentiates the gallery without changing speaker facts", async ({ page }) => {
    await mount(page, `/events/${eventId}/gallery`);

    await expect(page.getByRole("link", { name: "Gallery" })).toHaveAttribute("aria-current", "page");
    await expect(page.locator(".speaker-card h2")).toHaveText([
      "Cher",
      "García Márquez, Gabriel",
      "Anne Hopper",
      "anne hopper",
      "Grace Brewster Murray Hopper",
      "Martin Luther King Jr.",
      "Ada Lovelace",
    ]);
    await expect(page.locator(".speaker-card").first()).toContainText("Research lead · Example Labs");
    await expect(page.locator(".public-event-masthead__band")).toBeVisible();
    await expect(page.locator(".public-event-masthead__mark")).toHaveText("ES");
    await page.getByLabel("Find a speaker").fill("Hopper");
    await expect(page.getByRole("link", { name: "List" })).toHaveAttribute("href", `/events/${eventId}/speakers?q=Hopper`);
  });

  test("expands long biographies in an accessible bounded profile dialog", async ({ page }) => {
    await mount(page, `/events/${eventId}/gallery`);

    const card = page.locator(".speaker-card").filter({ hasText: "García Márquez, Gabriel" });
    const opener = card.getByRole("button", { name: "View profile" });
    await opener.click();

    const dialog = page.getByRole("dialog", { name: "García Márquez, Gabriel" });
    await expect(dialog).toBeVisible();
    const toggle = dialog.locator(".speaker-profile__bio-toggle");
    await expect(toggle).toHaveAccessibleName("Show more");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(toggle).toHaveText("Show less");
    await dialog.getByRole("button", { name: "Close speaker profile" }).click();
    await expect(opener).toBeFocused();
  });

  test("suppresses the event masthead in embed mode", async ({ page }) => {
    await mount(page, `/embeds/events/${eventId}/gallery`);
    await expect(page.locator(".gallery-event-branding")).toBeHidden();
    await expect(page.locator(".public-header")).toBeHidden();
    await expect(page.locator(".speaker-card")).toHaveCount(speakers.length);
  });

  for (const surface of ["speakers", "gallery"]) {
    test(`${surface} keeps mixed portraits, surname order and profiles correct after search`, async ({ page }) => {
      const entries = ["Aaron Zulu", "Sasha Speaker", "Zoe Adams"].map((name, index) => ({
        ...speakers[0],
        id: `mixed-${index}`,
        display_name: name,
        headshot_url: name === "Zoe Adams" ? "/test-headshot.png" : null,
      }));
      await page.route("**/test-headshot.png", (route) => route.fulfill({
        contentType: "image/png",
        body: readFileSync(resolve(__dirname, "../../uat/sessionboard/fixtures/headshot.png")),
      }));
      await mount(page, `/events/${eventId}/${surface}`, entries);
      const cards = page.locator(".speaker-card");
      const expectedNames = ["Zoe Adams", "Sasha Speaker", "Aaron Zulu"];
      await expect(cards.locator("h2")).toHaveText(expectedNames);
      const photo = cards.first().locator(".speaker-card__media img");
      await expect(photo).toBeVisible();
      await expect.poll(() => photo.evaluate((image: HTMLImageElement) =>
        image.complete && image.naturalWidth > 0)).toBe(true);
      await expect(cards.nth(1).locator(".speaker-initials")).toHaveText("SS");
      await expect(cards.nth(2).locator(".speaker-initials")).toHaveText("AZ");
      await expect(cards.nth(1).locator("img")).toHaveCount(0);
      await expect(cards.nth(2).locator("img")).toHaveCount(0);

      const search = page.getByLabel("Find a speaker");
      await search.fill("Sasha");
      await expect(cards.locator("h2")).toHaveText(["Sasha Speaker"]);
      await search.fill("");
      await expect(cards.locator("h2")).toHaveText(expectedNames);
      for (const name of expectedNames) {
        const card = cards.filter({ has: page.getByRole("heading", { name, exact: true }) });
        const opener = card.getByRole("button", { name: "View profile" });
        await opener.click();
        const dialog = page.getByRole("dialog", { name, exact: true });
        await expect(dialog).toBeVisible();
        if (name === "Zoe Adams") {
          await expect(dialog.locator("img")).toHaveAttribute("src", "/test-headshot.png");
        } else {
          await expect(dialog.locator(".speaker-initials")).toHaveText(
            name === "Sasha Speaker" ? "SS" : "AZ",
          );
        }
        await dialog.getByRole("button", { name: "Close speaker profile" }).click();
        await expect(dialog).not.toBeVisible();
        await expect(opener).toBeFocused();
      }
    });
  }
});
