# SessionBuddy product status

This is the canonical capability-oriented status record. Numbered delivery phases
are no longer used as product or architecture terminology.

## Platform and Engine Room

Implemented: a public, role-oriented SessionBuddy homepage, Cloudflare Worker packaging, D1/R2 boundaries, guarded browser-based first-run setup that preserves the administrator's exact first and last name and shows an in-place first-link confirmation, magic-link and password identity, invitation and
submission-context provisioning, opaque sessions, RBAC, CSRF/origin protection,
rate limiting, structured API errors, safe shared browser error handling,
branded browser 404/500 recovery pages, request IDs, observability, containerized
development, registration-ready account profiles with private headshots and public links,
account-level roles with one active session role, password changes that invalidate every
other session while atomically replacing the caller's cookie and CSRF token,
browser-friendly expired-link recovery, and the read-only
`/engine-room` operator console.

The platform People directory is organization-authority scoped and presents each
person once in a table across the organizations the operator may manage. Search
has an explicit, tested field contract for name, email, and company; organization
and role remain categorical filters, while speaker and reviewer states stay in
their event-specific workflows.

Fresh D1 databases install from immutable `0001_baseline.sql` followed by the
ordered incremental migration ledger. Data-bearing installations upgrade in
place only after backup and restore rehearsal; fresh-chain and preceding-schema
upgrade tests, repeat no-op application, and foreign-key validation are release
requirements.

Development reset tooling exports
a complete recovery backup plus a minimal explicit-column bootstrap identity
bundle, recreates local or remote development D1 schema from the ordered chain,
restores only the organization owner and complete password credential,
and verifies that operational tables are empty. Remote recreation also updates
both Worker bindings and redeploys them; local recreation refuses to run while
the local application or activity Worker is reachable.

The active-role and resource-consumption redesign is in progress. Its durable
handoff, intended user-story arc, completed UI work, and known transitional gaps
are recorded in `docs/rbac-redesign-handoff.md`.

Disposable local and development deployments may explicitly bypass the
magic-link confirmation and first-login profile redirect for automated
evaluation. Stored profile completion remains truthful, and staging and
production ignore the setting.

The external SessionBoard evaluation launcher now uses the host Codex CLI with
its existing ChatGPT-managed login. Playwright remains isolated in the pinned
Linux container; a short-lived authenticated bridge carries schema-constrained
model requests without mounting Codex credentials or requiring an Anthropic or
OpenAI API key. Dry runs remain model-free.

## CFP management

Implemented: program creation, browser-managed text, choice, checkbox, phone,
URL, image, and document questions; conditional display and answer-based
routing; organizer-configurable combined session formats; structured display
rules that reference earlier proposal fields and valid configured answers;
open/close times and submission limits; branded public CFP rendering;
richer CFP descriptions with safe formatting; public important-date milestones;
review-before-submit; confirmation email copy; authenticated speaker
registration; versioned drafts; owned submissions; admin submission listing;
tenant scoping; authenticated writes; and a persistent event-scoped CFP link
using `/cfp/{event_key}/{slug}`, with legacy-link redirects plus copy, open, and
submission-review actions. Same-speaker duplicate titles remain valid and show
an inline, non-blocking warning as soon as the title field loses focus; speaker
and organizer rows include submission date and receipt identity for
disambiguation. The advisory title lookup is owner-scoped and searches the full
form history rather than relying on the 25-item recent-proposal list. Additional
participants can be identified as co-speakers,
co-authors, moderators, panelists, or another participant role; the selected role
is preserved through invitation acceptance and later proposal edits. Primary
speakers can withdraw their own submitted
proposal before review begins; withdrawal is audited, repeat-safe, excluded from
review assignment, and read-only in the speaker portal. CFP-scoped sign-in grants the
speaker role for that event even when the email already belongs to an administrator,
and organizers can inspect the complete proposal, routing, and custom answers.

The published CFP page shows the proposal form to a visitor who is not signed
in. Every field, its conditional display, and its client validation already
travel in the public `GET /api/v1/forms/{slug}` payload, so a speaker can read
what the call actually asks for, try the conditional questions, and decide
whether to apply before creating an account. Answers stay in that browser under
the existing thirty-minute draft, which is only restored to a session whose
email matches the one the draft was written under. Authentication is still
required to submit: the anonymous path saves the completed proposal locally and
asks for email verification, and the server refuses an unauthenticated write
regardless of what the page displays. A `/speaker/proposals/...` workspace URL
names one stored submission, so it continues to require a session. The page no
longer submits the sign-in form on the visitor's behalf; a speaker who may have
no password chooses between signing in and requesting a one-time link.

## Organization and event administration

Implemented: organization rename, event create/edit/archive, an authenticated
application shell with a profile/sign-out menu, organization and event hubs,
safe duplicate-as-draft confirmation with event-owned branding copies,
clickable event overviews and speaker directories, event-scoped navigation,
organization-wide organizer management, event-specific reviewer and speaker
invitations, invitation revocation, and verified invitation acceptance. Every
workspace page keeps its own durable URL and normal browser navigation; the
shell paints in the page's first frame from a per-tab cached session that is
revalidated in the background (and dropped on sign-out, role switch, or 401),
named view transitions hold the sidebar, topbar, and event navigation visually
still across documents, and supporting browsers prerender one hovered
destination at a time (keyboard and touch intent warms the cache with a
prefetch instead) so the click swaps to a finished page. The cached shell
session never stores the CSRF token, expires after fifteen minutes, and is
invalidated across the account's other tabs on sign-out or role switch; the
content security policy admits inline speculation rules and nothing else
inline. Reviewer
invitations capture the person's display name, return the newly minted one-time
access URL to the authorized organizer for copying, rotate that URL on resend,
and take a first-time reviewer through profile and optional password setup before
Reviews. Invitation-list responses never disclose bearer URLs. The
event UI does not expose a separate event-administrator role or generic
view/edit/manage grants; organization owners and managers administer every
event in that organization.

## Evaluation

Implemented: evaluation rounds, balanced assignments, blind review, conflict
declaration and reassignment, immutable final decisions, results, and audit
records. Scorecards combine weighted numeric criteria with required or optional
dropdown and free-text responses; only numeric criteria contribute to the
overall mean. Draft rounds can be reopened and edited before review starts, and
are directly available from event navigation. Acceptance creates the accepted session and only the onboarding work
that remains actionable: a biography task when the registered speaker has not
provided one, a headshot task when no clean headshot exists, and the required
presentation task. It does not generate a generic supporting-material task;
additional documents must be requested later with explicit context. Rejection
waives outstanding onboarding; either decision can queue a speaker email with
organizer-controlled copy. Organizers can also accept or reject an unreviewed
proposal directly with a required audited reason, without manufacturing a reviewer
assignment. Final decisions remain immutable. An explicit correction workflow
appends the prior and corrected outcomes, reason, actor, and timestamp; acceptance
creates or restores the accepted session, while a correction to rejection withdraws
it without destroying its content or audit history.
Once a proposal belongs to an active evaluation round, the proposal inbox removes
the direct-rejection action and links organizers to that round's audited decision
controls instead.
A rejection immediately revokes unfinished assignments while preserving final
reviews as immutable history.
The organizer round view uses a compact progress rail and reviewer roster;
proposal decision reasons, notification controls, and permanence warnings are
revealed only after the organizer selects Accept or Reject.
The proposal inbox presents rounds as an ordered status ledger. Only an open
round blocks a proposal from selection; closed-round proposals can enter a later
round, and draft rounds may be saved before any reviewers are assigned.
A round's assignments are the explicit reviewer/proposal pairs the organizer
built; `assignment_strategy` only generates the opening matrix when the payload
carries no list at all. An empty list alongside selected proposals and reviewers
is refused rather than saved, because such a round can never be opened and shows
nothing anywhere the organizer can see it. Rounds report the reviewers and
proposals they hold from round membership rather than inferring them from
assignments, so a proposal awaiting its first reviewer, or a reviewer awaiting
their first proposal, still counts as part of the round; revoked assignments are
excluded from the counts. Editing a draft preserves the proposals that the
paginated proposal table cannot show.

## Speaker operations

Implemented: multi-event speaker portal, decision-aware status, default and custom form
tasks, Account-owned profile and headshot management, organizer-published resources/wiki content with
allowlisted embeds, public branded speaker galleries, quarantined asset uploads,
fixed-length R2-to-scanner streaming, asynchronous malware scanning, private
download grants, cursor-paginated communications, reminders, and admin progress
views. Pending speaker invitations appear in the event roster without granting
speaker permissions before acceptance and are available to invitation-safe
bulk communications with an explicit invited-recipient label. Registered invited speakers can receive
onboarding tasks before a proposal is accepted, organizers can edit their
event-scoped speaker details, and event rosters accept validated CSV invitation
imports of up to 500 speakers. CSV imports require an explicit identity review
before creating a same-name person under a different email. Post-acceptance
participation is tracked independently from proposal selection, and organizers
can persist awaiting-confirmation, confirmed, or declined status. Organizers can
also maintain private, configurable
label/value notes for event-specific travel, logistics, accessibility, and hospitality
details. Speaker and welcome-message attribution prefers
the accepted session over a newer unrelated proposal. Public profiles include
each published session's time, room, and track. The speaker portal presents
event-scoped updates separately from account authentication history, with message
categories, safe expandable content, browser-local read state, and bounded history
expansion. Organizer message history excludes authentication mail and provides
responsive category/status filtering with expandable delivery detail. Organizers
can bulk-remind the currently visible active-speaker tasks, inspect clean file
versions, record immutable version-scoped comments and replies, and export selected
clean deliverables as a private audited ZIP. Session content history preserves
versions whose original editor identity is no longer resolvable and exposes their
saved title, abstract, status, event-local timestamp, and restore action. A scheduled communication dispatcher
republishes stuck queued messages, retries transient provider failures, and
recovers abandoned delivery claims with bounded attempts. Organization
administrators can use an audited Engine Room recovery endpoint to extend the
retry budget for exhausted transient failures after correcting a provider issue;
permanent rejections are never requeued by that action.

## Scheduling

Implemented: first-agenda setup with event rooms and optional tracks, conflict
preview, event-time-zone-safe atomic agenda edits, concurrency protection,
publication, branded list/day/week/track/room schedule views, a browser-local
attendee itinerary, embeddable public schedule and speaker views, versioned
calendar invitations, and a read-only Sessionboard-compatible feed for pulling
accepted speakers and sessions into Accelevents.

Accepted-session handoff preserves the proposal's routed track when it matches
an active agenda track and exposes the primary and co-speaker names in the
schedule editor.

Organizers can also create first-class sessions directly in the agenda without
manufacturing a proposal or acceptance decision. A session may name active
speakers or pending speaker invitees; pending people remain invitations until
acceptance, when their session participant and any scheduled agenda association
are transactionally linked to the newly created event speaker.
Before publication, the organizer confirmation lists scheduled session titles that
still contain pending invitees. Public output remains privacy-safe and shows those
participants as Speaker TBA until acceptance.

The agenda editor's authenticated unschedule action includes the required JSON
media type, and the public schedule identifies the published revision number
rather than exposing an internal optimistic-lock version.

## Release readiness

Implemented and verified: fail-closed production configuration, accessibility
coverage, large-dataset seeding, database backup/restore checks, desktop and
mobile browser form/flow tests (including rejected invalid writes), API benchmarks,
Lighthouse audits, Cloudflare packaging, remote D1
migrations, R2, Queues/DLQs, Workflow binding, core secret bindings, deployment,
health checks, browser-route checks, desktop/mobile API-failure recovery checks,
and the anonymous identity boundary.

Direct speaker and CFP uploads now bind the declared byte size into their R2
PUT signatures. Speaker uploads additionally enforce authorization rate limits,
three live pending intents, a 250 MiB retained-storage budget per speaker/event,
and scheduled cleanup of expired pending objects. Synchronous account-headshot
scans have their own three-per-minute per-user limiter.

Magic-link requests now apply independent per-recipient and per-source limits.
Issued sign-in and invitation links keep bearer tokens in URL fragments, remove
them from browser history client-side, and redeem them only through the existing
explicit, exact-Origin, body-only confirmation POST.

The isolated Cloudflare development rehearsal is complete. Resend and direct-R2
credentials are configured. On 2026-08-16 the development D1 database, R2 bucket,
queues/DLQs, and reminder workflow were backed up where applicable, purged, and
recreated; the Worker was redeployed from the rebased canonical baseline while
all seven secret bindings were preserved. That rehearsal had 23 passing checks,
no pending activation, and the first administrator was bootstrapped. Malware
scanning is explicitly bypassed on this isolated development deployment.
The application no longer contains synthetic identities or data-seeding endpoints, and
fresh instances begin with no organizations, events, or speakers. Live organization/event
edits, invitation creation/revocation/acceptance, speaker ownership, conditional
draft restore and submission, and a direct R2 upload all pass. The run exposed
three integration defects—an asynchronous invitation reset, conditional draft and
submission-payload handling, and CSP blocking R2—and each is fixed, covered by a
regression, and deployed. The checked-in development configuration explicitly
allows the development-only malware-scan bypass; preview, staging, production,
and unknown environments still fail closed. No Cloudflare Container is configured. Applied D1 migration
ledger contains only the single canonical baseline migration.

The requirement-by-requirement evidence and the remaining authenticated
development rehearsal are tracked in `delivery-completion-audit.md`.

## Updates — 2026-08-16

The public CFP is the anonymous/new-submission entry point. Authenticated
speakers see compact proposal rows in their portal and open one exact, URL-addressable proposal
page at `/speaker/proposals/{slug}/{submission_id}`. New proposals are created
only from the published CFP page. The exact proposal page uses the same
published schema, conditional fields, browser/server drafts, staged uploads,
validation, create, and patch contracts as the public CFP; the speaker portal
links to that workspace instead of maintaining a second proposal editor.
Open/closed reasoning remains shared through `sessionbuddy.cfp.availability`.
Speaker profile and headshot management use one coordinated save flow in Account;
the canonical identity synchronizes to linked speaker records, and its private
headshot is the public-gallery fallback when an event has no legacy event-specific
image. Public profiles are private by default and require an explicit account opt-in.
Organization People rows link to a public profile only while that opt-in remains enabled;
disabling it makes both the anonymous profile and headshot unavailable immediately.
Profile and headshot tasks link to that Account flow and reconcile when the
canonical data is saved. Organizers can preview and replace a linked speaker's headshot from
the event-scoped directory; the route requires exact event speaker-management
authority, validates and scans the image when a scanner is configured, and emits
an audit record. Organizer-uploaded headshots are stored as speaker asset versions,
so the profile and organizer file inventory reference the same scanned object and
the inventory exposes uploader, upload time, scan state, preview, and download history.
Explicit development scanner-disable mode performs no scanner
request, while production remains fail closed.

The portal deliberately exposes only calls belonging to events the speaker
already has an active speaker membership on. It is not a discovery surface and
does not advertise other events' calls; organizers run events from the admin
side, and public discovery stays on the public event pages. A speaker with no
event membership therefore has no portal entry point by design.

Both proposal surfaces now hold their idempotency key steady across retries of
an unchanged proposal, minting a new key only when the payload changes. A lost
response after a stored proposal previously invited a retry under a fresh key,
which the server could not recognise as a replay and would have accepted as a
second proposal. Browser coverage in
`harness/e2e/speaker-portal-proposal-composer.spec.ts` exercises the composer
end to end: submission, conditional fields, co-speaker limits and self-listing,
retry key reuse, key rotation after an edit, exhausted allowances, a call that
closes mid-session, Axe checks, and 320/390px layouts.

The Day-N operational fixture pack is now a portable, UI-only execution kit:
seven event worksheets, target-neutral resource keys, an instance-map template,
original event branding, deterministic synthetic speaker portraits, an original
supporting PDF, labels, sessions, reviews, speaker operations, and a phase-ordered
runbook. Downloaded third-party speaker catalogs, biographies, portraits, and
schedule files are excluded and ignored. A release-package gate proves that no
fixture worksheet or asset is included in the deployed Worker. A target-specific
map records generated IDs without storing passwords, cookies, mail links, or
tokens. Local rehearsal evidence includes exact persona/resource denials, an
overdue speaker task, uploaded asset safety boundaries, and three manual speaker
messages delivered through Mailpit.

Local communication sends now publish to the bound communication queue instead
of intentionally remaining queued, so the same consumer and Mailpit provider
exercise the deployed delivery boundary. Speaker file completion failures after
the byte transfer retain the pending upload for a safe completion retry and tell
the user that the file was received, is not yet public/current, and does not need
to be selected or transferred again.

Persona document boundaries now fail closed before a portal shell is served.
An authenticated Organizer receives HTTP 403 from `/speaker` and `/reviews`,
while matching Speaker/Reviewer sessions and anonymous sign-in shells retain
their intended behavior. The SessionBuddy brand follows only the explicit
active persona. Missing or unknown active-role state also returns 403; the
server and browser no longer infer a destination from default roles, legacy
memberships, or unrelated resource access.

First-run profile onboarding preserves the exact bootstrap first and last name,
shows legacy display-name splitting as an editable unsaved draft, and provides
actionable recovery when password configuration is temporarily unavailable.
Persona-neutral account pages with no real destinations use a topbar-only shell
instead of an empty navigation column.

An Organizer with only an exact-event grant now lands in that event workspace,
sees event navigation without organization-wide navigation, and can load the
event overview even when the organization list is intentionally unavailable.
Other event workspaces remain denied, and switching the same account to Speaker
continues to block all organizer APIs and pages.

Destructive administration is separated from ordinary editing. Event editors
cannot archive or restore events, rooms, or tracks; exact managers and owners
retain recovery access to archived-event grants. Label archive and restore are
likewise event-manage actions, so a manager can recover labels created by a
revoked editor without receiving unrelated label-edit ownership.

Organization speaker-directory reads expose people and per-event participation
only for events the caller can manage exactly. Event duplication additionally
requires exact source-event management before private branding references are
read or copied.

The resource control plane now includes exact organization `view`, `edit`, and
`manage` grant CRUD, authoritative owner rows, exact-owner-only audited
organization ownership transfer to an existing organization admin, audited event ownership transfer, and
an organization-owner-only paginated recovery inventory for colleague-owned
events, including archived events. Transfer is explicit and non-cascading: it
does not move, delete, archive, or rewrite event content and does not silently
preserve the previous owner's event access.

The versioned OpenAPI contract is generated at release time and served as
pre-generated bytes from `/api/v1/openapi.json` in local, development, and
preview environments. Production, staging, missing, and unknown environment
bindings return 404. This removes request-time schema generation from the
Cloudflare CPU path while preserving `app.openapi()` for deterministic contract
generation and drift checks.

Those same approved non-production environments expose a self-hosted,
read-only reference at `/docs`. It searches and filters the checked OpenAPI
contract without a request runner, credentials, or mutation controls. The page
and its assets fail closed with 404 outside local, development, and preview.

Resource authorization now treats access delegation as a distinct manage-only
operation: an editor can change event content but cannot list grants, promote
their own grant, or create an administrative invitation. Administrative
invitation acceptance revokes an older active view/edit grant before applying
manage. D1 authorization facts exclude revoked grants and archived ownership,
and no longer silently truncate ownership or grants after 500 rows. The account
shell keeps event-scoped invitees on `/account` while they complete onboarding,
and the speaker directory consumes named resource permissions instead of the
removed administrative-role response field.

## Updates — 2026-08-15

Resource authorization now separates account personas from administrative
authority. Organizer, Reviewer, and Speaker remain the only switchable
personas. Organizations and events record an immutable creator and an owner.
Organizer authority is organization-scoped: an organization owner or manager
administers every event in that organization. The shared API policy still
requires the Organizer persona, so an organization owner using the Speaker
persona cannot call organizer APIs. Reviewer authority remains assignment-
scoped and speaker authority remains participation/ownership-scoped.

New event creation and duplication record the authenticated user as owner and
do not create `event_admin` memberships. Speaker and reviewer participation
remains assignment-based. The account page and role switcher display these
concepts separately, with organization organizers outside the persona switcher.
The rebased fresh-install
baseline creates this ownership, grant, persona, and label schema directly;
the development deployment does not carry or apply a legacy upgrade ledger.

## Updates — 2026-08-14

Authentication and account roles: the sign-in page is now a minimal email,
password, and magic-link entry. Every account role set has one persistent default
role; password and magic-link sessions activate it and an unscoped sign-in opens
that role's dashboard. Authenticated role switching now updates
`session_active_roles` through a CSRF-protected server endpoint instead of relying
on browser storage. A role switch remains an account-persona choice only and does
not grant organization or event resource access.

Organizer Home now assumes one assigned organization and opens directly into its
overview using existing organization, metrics, events, and speaker APIs. It adds
no organization-creation API or control. The page shows organization context,
permission-gated event creation, real summary metrics, event cards, and recent
speaker activity, with a dedicated 390 by 844 responsive browser regression.


## Updates — 2026-08-13

CFP intake: first-time submitters stage file answers before any speaker record
exists (`cfp_staged_assets`); the speaker/person/membership graph is created
only when a submission succeeds, atomically with the file attachment, and a
revoked membership is never reactivated by submitting or accepting event-level
invitations (reactivation returns as a plain member). Staged uploads carry
per-user active quotas, an hourly creation quota, and dedicated rate-limiter
bindings; abandoned files expire after 24 hours via the scheduled handler.
Magic-link sign-in shows a packaged CSP-safe confirmation page on GET, consumes
the token only on POST, and restores the token if provisioning fails after
consumption.

Administration: organization administrators are now invitable (with
organization-level permission required to create, resend, or revoke such
invitations); events have a direct read endpoint, a paginated and
server-filtered listing (signed keyset cursors; view, search, and order bound into the
cursor), per-event proposal/review/schedule readiness signals, an aggregate metrics endpoint with recent speakers, and archive
semantics that preserve status and the original archive timestamp across
ordinary edits. Console entry points ("People", "Create event", event
navigation, organization-admin invitations) render only with the exact backing
permission for the selected organization or event.

Observability: `observability/manifest.json` now registers every API route,
document route, and asynchronous handler (cron steps, queue consumers,
workflow) with owners, budgets, and runbooks, enforced by tests; queue
consumers' missing structured logs are recorded there as a known gap.

Organization activity is backed by a minimal append-only ledger of successful
CRUD facts. The same domain transaction writes each fact and its `UNPROCESSED`
marker. An independently deployed activity Worker publishes pending identifiers
through a dedicated Queue; its single distributor invokes every applicable pure
projection builder and atomically commits all organization, event, organizer,
reviewer, and speaker rows together with the `PROCESSED` marker. Local Compose
runs the same Worker with a one-second development-only dispatcher loop;
Cloudflare uses a Cron trigger plus Queue consumer. The organization settings
page exposes only safe projected fields to exact organization owners and admins;
credentials, content, and cross-organization activity are never returned.
Activity, user, and proposal references use stable typed public identifiers
(`A…`, `U…`, and `P…`); internal resource identifiers remain in the private
resolver table used by the distributor.
## Event labels

- Organizers can create color-coded labels for an exact event from Schedule tools.
- Every label records its authenticated creator and owner as an individual resource.
- Label edits and archival require exact label ownership or an explicit edit/manage grant.
- Event managers can assign up to 20 active event labels to each accepted session.
- Published schedules display and search label names without exposing ownership metadata.

## Evaluation round assignment tooling

- The proposal inbox filters by `routed_track`. Track is written by the form's
  routing rules and already travelled on every row; only the control was
  missing. An event whose form routes nothing shows no filter at all.
- "Select submitted" means the proposals on screen, so a filtered table is how an
  organizer selects one track. "Clear selection" stays absolute. A proposal
  selected before the filter narrowed the table is still in the round, and the
  filter says so rather than dropping it silently.
- The round builder distributes assignments across reviewers: reviewers per
  proposal, and an optional maximum per reviewer. It fills the checkbox matrix
  and stops there. What is saved is still the explicit pair list, so a
  hand-corrected cell survives every later save, and there is no second
  generator on the server to keep in step.
- A configuration the reviewer pool cannot satisfy is refused where it is
  entered. Reviewers per proposal is clamped to the pool rather than promising
  reviews the round cannot produce, and a cap too low to cover the selection is
  reported with what to change instead of producing a proposal nobody reviews.
- Open rounds carry a reminder action on the ledger, sending to every reviewer
  with outstanding work. It reuses the existing per-reviewer endpoint, which
  derives the outstanding count server-side and folds each send into an hourly
  deterministic key, so a repeat click is not a repeat email. A reviewer who
  finishes mid-send is counted, not treated as a failure.

## Organizer proposal detail

- The organizer proposal view shows the submitter's company, resolved at read
  time from the speaker's account and falling back to the person record this
  organization curates. A submission has never stored a company of its own and
  still does not: no column, no backfill, and no second copy to drift.
- Both sources are tenant-scoped. `people` is unique per
  `(organization_id, user_id)`, so the join cannot multiply a submission into
  several rows, and a person record owned by another organization is never read.
- Speaker-facing responses build the same model without those joins, so the
  field serializes as `null` there. It is optional in the schema and never in
  `required`, so a client must treat `null` and a company it simply does not
  know as the same answer.

## Reviewer assignment boundary

- Reviewer is an account persona, not an organization or event membership role.
- Organizers add an existing Reviewer to a round by exact email; the API never
  lists or partially searches accounts and never returns the reviewer's email.
- An active evaluation assignment is the sole event/proposal authorization
  fact. Revoking it removes review and event-assignment visibility immediately.
- Reviewer invitations establish the Reviewer persona only. They do not grant
  organization membership, event membership, organizer access, or speaker access.

## Demo persona sign-in

- `/sign-in` and the landing page can offer one control per account persona
  (Organizer, Reviewer, Speaker) that signs in without a password.
- The capability requires both `APP_ENV=local` or `APP_ENV=development` and an
  explicit `DEMO_LOGIN_ENABLED=true`, matched case-insensitively and trimmed.
  Missing or unknown environments and absent, empty, `1`, or `yes` flags all
  fail closed.
- `POST /api/v1/auth/demo-sign-in` accepts a role and nothing else. The role to
  user-id mapping is server-owned configuration, so the endpoint cannot be used
  for arbitrary account impersonation.
- Demo sessions are created through the same helper as password sign-in, so
  cookie policy, CSRF binding, expiry, and authorization version are identical.
- A session row and its active-role row are written by one conditional insert
  pair, so a role revoked mid-request yields no session at all rather than a
  valid cookie with no persona. No cookie is issued unless the write landed.
- Signing into a demo persona while already authenticated revokes the previous
  session rather than leaving a valid row behind.
- `scripts/cloudflare_preflight.py` mirrors the runtime gate: it fails if demo
  sign-in is enabled outside `local` or `development`, or if any demo identity
  is unconfigured.
- Demo identities are referenced by user id, never display name, and the
  synthetic organizer holds a `manage` grant rather than resource ownership.
- See `docs/demo-accounts.md` for seeding, repair, and purge procedures.

## Evaluation round names

- A round name is unique among the draft and open rounds of one event. The
  comparison folds case and collapses whitespace, so `Round 1`, `round 1`, and
  `Round  1` are one name. The stored name keeps the organizer's own casing.
- A closed round keeps its name in the history and releases it for reuse, and a
  different event is a separate namespace.
- The rule is enforced twice, deliberately. The application compares real names
  with Python `casefold()` and returns the actionable 409; `0005` adds
  `evaluation_rounds.name_key` plus the partial unique index
  `uq_evaluation_rounds_live_name`, which makes the refusal atomic when two
  organizers submit at the same instant and both reads report the name free.
- The `0005` backfill uses SQLite `lower()`, which is ASCII-only, so a
  pre-existing row with non-ASCII case carries an approximate key until its next
  draft save rewrites it. The application guard covers that window.
- Historical duplicates among live rounds are renamed rather than deleted or
  left outside the index. The oldest row by `(created_at_ms, id)` keeps its
  name; every other row is suffixed with its own id. A rank suffix such as
  ` (2)` reads better and is unsafe: it can land on a name another live round
  already holds, and the index would then abort the upgrade after the renames.
- A round create or draft save the index refuses re-runs the name guard, so the
  loser of a concurrent submit gets the same rename guidance a caller the guard
  catches directly would get, not the generic conflict envelope.
