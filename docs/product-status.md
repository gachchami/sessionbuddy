# SessionBuddy product status

This is the canonical capability-oriented status record. Numbered delivery phases
are no longer used as product or architecture terminology.

## Platform and Engine Room

Implemented: a public, role-oriented SessionBuddy homepage, Cloudflare Worker packaging, D1/R2 boundaries, guarded browser-based first-run setup for a named
administrator, magic-link and password identity, invitation and
submission-context provisioning, opaque sessions, RBAC, CSRF/origin protection,
rate limiting, structured API errors, safe shared browser error handling,
branded browser 404/500 recovery pages, request IDs, observability, containerized
development, registration-ready account profiles with private headshots and public links,
account-level roles with one active session role, browser-friendly expired-link recovery, and the read-only
`/engine-room` operator console.

The active-role and resource-consumption redesign is in progress. Its durable
handoff, intended user-story arc, completed UI work, and known transitional gaps
are recorded in `docs/rbac-redesign-handoff.md`.

Disposable local and development deployments may explicitly bypass the
magic-link confirmation and first-login profile redirect for automated
evaluation. Stored profile completion remains truthful, and staging and
production ignore the setting.

## CFP management

Implemented: program creation, browser-managed text, choice, checkbox, phone,
URL, image, and document questions; conditional display and answer-based
routing; open/close times and submission limits; branded public CFP rendering;
review-before-submit; confirmation email copy; authenticated speaker
registration; versioned drafts; owned submissions; admin submission listing;
tenant scoping; authenticated writes; and a persistent event-scoped CFP link
with copy, open, and submission-review actions. CFP-scoped sign-in grants the
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

The isolated Cloudflare development rehearsal is complete with 23 passing
preflight checks. Resend and direct-R2 credentials are configured, the
organization and first administrator were bootstrapped for the previous rehearsal.
The application no longer contains synthetic identities or data-seeding endpoints, and
fresh instances begin with no organizations, events, or speakers. Live organization/event
edits, invitation creation/revocation/acceptance, speaker ownership, conditional
draft restore and submission, and a direct R2 upload all pass. The run exposed
three integration defects—an asynchronous invitation reset, conditional draft and
submission-payload handling, and CSP blocking R2—and each is fixed, covered by a
regression, and deployed. The development environment intentionally bypasses
malware scanning; staging and production reject that bypass and require a scanner
endpoint/secret. No Cloudflare Container is configured. Applied D1 migration
filenames retain their original delivery-era names because migration identifiers
are immutable.

The requirement-by-requirement evidence and the remaining authenticated
development rehearsal are tracked in `delivery-completion-audit.md`.

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
