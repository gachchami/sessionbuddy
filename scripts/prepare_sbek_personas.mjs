// Prepare the separate speaker and reviewer identities required by a chained
// SBEK run. The organizer's saved browser session is used to create/reuse the
// fixture event and issue invitations; invitation URLs never leave this process.
import { chromium } from "/eval/node_modules/playwright/index.mjs";

const [targetUrl, hostName, speakerEmail, reviewerEmail] = process.argv.slice(2);
if (!targetUrl || !hostName || !speakerEmail || !reviewerEmail) {
  throw new Error("usage: prepare_sbek_personas.mjs <url> <host> <speaker-email> <reviewer-email>");
}

const organizerState = `/eval/.auth/${hostName}.organizer.json`;
const eventName = "DevFlow Conf 2027";

async function json(response, action) {
  if (!response.ok()) throw new Error(`${action} failed with HTTP ${response.status()}`);
  return response.json();
}

const browser = await chromium.launch({ headless: true });
try {
  const organizer = await browser.newContext({ storageState: organizerState });
  const request = organizer.request;
  const session = await json(
    await request.get(`${targetUrl}/api/v1/auth/session`),
    "organizer session check",
  );
  const organizationId = session.organization_access?.[0]?.organization_id;
  if (!organizationId) throw new Error("saved organizer session has no organization-admin scope");

  const headers = {
    "content-type": "application/json",
    origin: targetUrl,
    "x-csrf-token": session.csrf_token,
  };
  const events = await json(
    await request.get(`${targetUrl}/api/v1/admin/organizations/${organizationId}/events?limit=50`),
    "event list",
  );
  let event = events.data.find((item) => item.name === eventName && item.status !== "archived");
  if (!event) {
    event = await json(
      await request.post(`${targetUrl}/api/v1/admin/organizations/${organizationId}/events`, {
        headers,
        data: {
          name: eventName,
          starts_at_ms: Date.parse("2027-05-18T03:30:00Z"),
          ends_at_ms: Date.parse("2027-05-20T12:30:00Z"),
          time_zone: "Asia/Kolkata",
          location: "Bengaluru, India",
          delivery_mode: "hybrid",
          description: "Evaluation conference for the complete proposal-to-schedule workflow.",
        },
      }),
      "fixture event creation",
    );
  }

  async function inviteAndSave(persona, email, role, displayName) {
    const invitation = await json(
      await request.post(`${targetUrl}/api/v1/admin/events/${event.id}/invitations`, {
        headers,
        data: {
          email,
          role,
          expires_in_days: 7,
          ...(displayName ? { display_name: displayName } : {}),
        },
      }),
      `${persona} invitation`,
    );
    if (!invitation.accept_url) throw new Error(`${persona} invitation did not return an acceptance URL`);

    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(invitation.accept_url, { waitUntil: "domcontentloaded", timeout: 30000 });
    const accepted = await json(
      await context.request.get(`${targetUrl}/api/v1/auth/session`),
      `${persona} acceptance`,
    );
    const roles = accepted.event_access?.flatMap((scope) => scope.roles) ?? [];
    if (!roles.includes(role)) throw new Error(`${persona} session is missing the ${role} role`);
    await context.storageState({ path: `/eval/.auth/${hostName}.${persona}.json` });
    await context.close();
  }

  await inviteAndSave("speaker", speakerEmail, "speaker", "Priya Raman");
  await inviteAndSave("reviewer", reviewerEmail, "evaluator", "Sam Whitfield");
  await organizer.close();
  console.log(`SBEK personas prepared for ${eventName}; separate role sessions saved.`);
} finally {
  await browser.close();
}
