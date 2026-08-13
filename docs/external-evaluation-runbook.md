# External SessionBoard evaluation

Use `scripts/run_sbek.sh` instead of rediscovering the evaluator checkout or
reconstructing its Docker command.

The script is an integration wrapper, not the evaluation launcher of record.
It performs SessionBuddy-specific checkout discovery, dependency mounting, and
preflight validation, then delegates execution to the eval repository's
declared `sbek` package-script entry point. It must not invoke `src/cli.ts`
directly or start, stop, reset, or deploy the target SessionBuddy environment;
the repository runner and the operator retain those lifecycle authorities.

The launcher remembers these non-secret integration facts:

- deployed target: `https://sessionbuddy-development.shiny-cloud-dd47.workers.dev`
- eval checkout discovery: `/private/tmp/sessionbuddy-evals.*/repo`
- browser image: `mcr.microsoft.com/playwright:v1.62.1-noble`
- browser location inside that image: `/ms-playwright`
- model runtime: the host Codex CLI, authenticated with the existing ChatGPT login
- required saved personas: `organizer`, `speaker`, and `reviewer` in the eval checkout's
  ignored `.auth/` directory

It never reads or prints cookies, OAuth data, Codex tokens, or application
secrets. For a live run it starts a short-lived authenticated bridge on the
host, mounts only a one-run capability file into the browser container, and
removes both when the command exits. The Codex auth file is never copied or
mounted into the evaluator.

The public-widget scenarios use anonymous attendee access, so SessionBuddy does
not need an attendee account. A second speaker email is still useful when the
agent exercises co-speaker handoffs.

Before a live run, the launcher makes a safe request with each saved persona,
checks the current `account_roles`/`active_role` contract, and stops immediately
if a session has expired or is using the wrong active persona.

## Reset the local evaluation database

Run this from the SessionBuddy repository root before a genuinely fresh local
evaluation. The reset exports an ignored, mode-`0600` full backup, extracts the
single bootstrap organization/administrator bundle, drops all SessionBuddy
tables, reapplies the canonical baseline, proves a repeat no-op, and restores
only that bootstrap identity. Events, eval personas, sessions, challenges,
proposals, reviews, and activity rows are intentionally removed.

```sh
docker compose stop worker activity-worker activity-poller

docker compose run --rm --no-deps worker \
  npm run worker:reset-data:local -- --confirm sessionbuddy-local

docker compose up --detach worker activity-worker activity-poller
```

Use `docker compose up --build --detach ...` only when the image must be rebuilt
for code or dependency changes. A routine data reset does not need `--build`.

The reset fails closed unless `migrations_baseline/` starts with the immutable
`0001_baseline.sql` and every later SQL file has a unique ordered name.
Cloudflare-owned local metadata tables are retained because Workerd forbids
dropping them; the Wrangler migration ledger is emptied before the complete
chain is applied. Do not start an eval if the migration layout is invalid or if
either Worker is still reachable.

After the reset, confirm the local application is ready:

```sh
docker compose ps worker activity-worker activity-poller
curl -fsS http://127.0.0.1:8787/health
```

Expected application state is one organization, one bootstrap administrator,
one active password credential, no events, and one migration-ledger row named
`0001_baseline.sql`. The backup and bootstrap bundle paths and SHA-256 hashes
are printed by the reset command. Do not paste either file into an eval report;
the bootstrap bundle contains password-verifier material.

The reset deliberately removes the eval Speaker and Reviewer. Provision all
three eval personas again and recapture their ignored `.auth` browser states
before starting the evaluator.

## Provision the starting personas through SessionBuddy

Use the ordinary product UI and emailed links. Do not insert accounts in D1 or
obtain an invitation acceptance URL from an API response.

1. Open `/setup` on the fresh instance. Enter the eval Organizer's exact first
   name, last name, email, organization, and deployment setup key, then select
   **Complete setup**. Open the newest administrator sign-in email and continue
   to the account. In **Profile**, review the names and use **Create a password**
   to set the eval password. The current UI requires at least 15 characters.
   Select **Save profile**, then sign in again at `/sign-in`; creating a password
   intentionally invalidates the email-link session. This bootstrap account is
   the one Organizer needed by the eval: it owns the organization and has the
   Organizer persona, so do not create a second Organizer or an event-admin
   substitute.
2. Open `/admin/events`, select **Create event**, fill the eval setup event, and
   select **Create active event**. The setup screen does not create an event, and
   Speaker and Reviewer invitations are scoped to a specific event.
3. Open the event and select **Team & access** in its sidebar. Select **Invite
   someone**, enter the eval Speaker email, choose **Speaker assignment**, fill
   the required **Name** under **Speaker details**, and select **Send
   invitation**.
4. Repeat **Invite someone** for the eval Reviewer, choosing **Reviewer
   assignment**. This provisions the Reviewer account and event membership; it
   does not put that person into an evaluation round or assign submissions.
   Configure reviewer pools, rounds, and submission assignments separately in
   the evaluation workflow.
5. In each recipient inbox, open the newest SessionBuddy invitation, select
   **Accept your SessionBuddy invitation**, and continue to the account. The
   Speaker is directed toward `/speaker` and the Reviewer toward `/reviews`;
   incomplete profiles are first routed to Account. Enter the required first
   and last names, create and confirm the 15-or-more-character password, select
   **Save profile**, and then sign in again with that password.

Only Organizer, Speaker, and Reviewer need provisioned accounts, passwords, and
saved browser state. `speaker2` never starts a scenario: retain only its
controlled email for the co-speaker handoff and do not create saved auth for it.
Do not provision Attendee at all. SessionBuddy's public program and browser-local
**My itinerary** flow are intentionally exercised anonymously.

On the local Docker stack, normal outbound messages are captured by Mailpit at
`http://127.0.0.1:8025`. The links inside those messages use the configured
`PUBLIC_BASE_URL`, not necessarily the origin open in the browser, so confirm
that it resolves to the running local instance or active tunnel before sending
invitations. The deployed instance delivers the same flows to the configured
real inboxes.

For each persona, request a SessionBuddy sign-in email in the normal UI. Then
copy its one-time URL into the helper; the URL is consumed inside a fresh eval
browser context and its storage state is saved without printing cookies:

```sh
scripts/run_sbek.sh auth organizer
scripts/run_sbek.sh auth-link organizer '<organizer-magic-link-url>'
scripts/run_sbek.sh auth-link speaker '<speaker-magic-link-url>'
scripts/run_sbek.sh auth-link reviewer '<reviewer-magic-link-url>'
```

The `auth` command prints the correct sign-in and `auth-link` instructions; it
does not pass the judge kit's removed `--paste-link` or `--reuse` flags. The
browser state remains in the eval kit's ignored `.auth/` directory.

Use three distinct accounts. Reusing one account for multiple starting personas
can leak the wrong active role into a scenario and is not representative of the
judge's multi-user workflows.

Configure inboxes you control in the eval checkout's ignored
`evalconfig.json`. Plus-address aliases are acceptable when the mail provider
delivers all aliases to the same inbox:

```json
{
  "personaEmails": {
    "organizer": "you+sbek-organizer@example.com",
    "speaker": "you+sbek-speaker@example.com",
    "speaker2": "you+sbek-speaker2@example.com",
    "reviewer": "you+sbek-reviewer@example.com"
  }
}
```

Also give the three starting accounts passwords in the ignored config. Saved
browser state starts the correct persona, while several judge scenarios later
sign out and switch to another user. The launcher therefore refuses a live run
unless Organizer, Speaker, and Reviewer each have matching email/password
credentials. `speaker2` needs only an email because it never starts a scenario.
Do not add Attendee credentials; the two attendee scenarios intentionally test
the anonymous public experience.

Inspect the resolved setup without launching an evaluation:

```sh
scripts/run_sbek.sh where
```

Run the required areas:

```sh
scripts/run_sbek.sh \
  --areas call-for-papers,abstract-management,speaker-management,content-management,ai-agenda,public-widgets \
  --agent-model gpt-5.6-sol \
  --judge-model gpt-5.6-sol
```

The current judge contains 18 required scenarios across six required areas.
Speaker CRM is optional and runs only with `--include-optional`.

The live run needs the host Codex CLI to be signed in with ChatGPT. Confirm it
without inspecting or copying its credential file:

```sh
codex login status
```

No Anthropic or OpenAI API key is required. Validate the harness without a
model call first:

```sh
scripts/run_sbek.sh list
scripts/run_sbek.sh smoke
scripts/run_sbek.sh --dry-run \
  --agent-model gpt-5.6-sol \
  --judge-model gpt-5.6-sol
```

Resume an interrupted run without paying for completed scenarios again:

```sh
scripts/run_sbek.sh resume runs/<timestamp> \
  --agent-model gpt-5.6-sol \
  --judge-model gpt-5.6-sol
```

Override a discovered value only when the local setup changes:

```sh
SBEK_ROOT=/path/to/killmysaas-evals \
SBEK_TARGET_URL=https://example.workers.dev \
scripts/run_sbek.sh where
```

The launcher defaults to the Codex binary bundled with the macOS ChatGPT app.
If Codex is installed somewhere else, provide its absolute executable path:

```sh
SBEK_CODEX_BIN=/absolute/path/to/codex \
scripts/run_sbek.sh --dry-run \
  --agent-model gpt-5.6-sol \
  --judge-model gpt-5.6-sol
```

The Playwright image and the eval kit's Playwright package must have the same
version. The current pinned pair is 1.62.1. A missing browser executable means
the launcher was bypassed or `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` was not
passed to the container.
