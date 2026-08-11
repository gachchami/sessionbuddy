# SessionBuddy product status

This is the canonical capability-oriented status record. Numbered delivery phases
are no longer used as product or architecture terminology.

## Platform and Engine Room

Implemented: a public, role-oriented SessionBuddy homepage, Cloudflare Worker packaging, D1/R2 boundaries, guarded browser-based first-run setup that preserves the administrator's exact first and last name and shows an in-place first-link confirmation, magic-link and password identity, invitation and
submission-context provisioning, opaque sessions, RBAC, CSRF/origin protection,
rate limiting, structured API errors, safe shared browser error handling,
branded browser 404/500 recovery pages, request IDs, observability, containerized
development, registration-ready account profiles with private headshots and public links,
account-level roles with one active session role, browser-friendly expired-link recovery, and the read-only
`/engine-room` operator console.

Fresh D1 databases now install from one canonical `0001_baseline.sql`. Runtime,
test fixtures, release scripts, and baseline validation do not depend on the
historical migration directory; rebasing intentionally requires every existing
database to be recreated and provides no incremental compatibility path.

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
routing; open/close times and submission limits; branded public CFP rendering;
richer CFP descriptions with safe formatting; public important-date milestones;
review-before-submit; confirmation email copy; authenticated speaker
registration; versioned drafts; owned submissions; admin submission listing;
tenant scoping; authenticated writes; and a persistent event-scoped CFP link
using `/cfp/{event_key}/{slug}`, with legacy-link redirects plus copy, open, and
submission-review actions. Primary speakers can withdraw their own submitted
proposal before review begins; withdrawal is audited, repeat-safe, excluded from
review assignment, and read-only in the speaker portal. CFP-scoped sign-in grants the
speaker role for that event even when the email already belongs to an administrator,
and organizers can inspect the complete proposal, routing, and custom answers.

## Organization and event administration

Implemented: organization rename, event create/edit/archive, an authenticated
application shell with a profile/sign-out menu, organization and event hubs,
safe duplicate-as-draft confirmation with event-owned branding copies,
clickable event overviews and speaker directories, event-scoped navigation,
administrator/evaluator/speaker invitations, invitation revocation, membership
listing and role revocation, and verified invitation acceptance.

## Evaluation

Implemented: evaluation rounds, balanced assignments, blind review, conflict
declaration and reassignment, immutable final decisions, results, and audit
records. Acceptance creates the accepted session and default onboarding tasks;
rejection waives outstanding onboarding; either decision can queue a speaker
email with organizer-controlled copy.

## Speaker operations

Implemented: speaker portal, decision-aware status, default and custom form
tasks, profile management, organizer-published resources/wiki content with
allowlisted embeds, public branded speaker galleries, quarantined asset uploads,
fixed-length R2-to-scanner streaming, asynchronous malware scanning, private
download grants, cursor-paginated communications, reminders, and admin progress
views. Pending speaker invitations appear in the event roster without granting
speaker permissions before acceptance, and public profiles include each
published session's time, room, and track. A scheduled communication dispatcher
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
`manage` grant CRUD, immutable owner rows, audited event ownership transfer, and
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
personas. Organizations and events record an immutable creator and an owner;
another organizer receives an explicit `view`, `edit`, or `manage` grant on the
exact resource. Organization access does not automatically cascade to its
events. The shared API policy requires both the appropriate active session
persona and exact ownership/delegation, so an event owner using the Speaker
persona cannot call organizer APIs.

New event creation and duplication record the authenticated user as owner and
no longer create `event_admin` memberships. Administrative invitation
acceptance provisions an Organizer persona plus a resource grant; speaker and
reviewer participation remains assignment-based. The account page and role
switcher display these concepts separately, with named resources and
owner/manage facts outside the persona switcher. The rebased fresh-install
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
## Event labels

- Organizers can create color-coded labels for an exact event from Schedule tools.
- Every label records its authenticated creator and owner as an individual resource.
- Label edits and archival require exact label ownership or an explicit edit/manage grant.
- Event managers can assign up to 20 active event labels to each accepted session.
- Published schedules display and search label names without exposing ownership metadata.
