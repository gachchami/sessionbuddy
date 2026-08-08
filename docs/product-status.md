# SessionBuddy product status

This is the canonical capability-oriented status record. Numbered delivery phases
are no longer used as product or architecture terminology.

## Platform and Engine Room

Implemented: a public, role-oriented SessionBuddy homepage, Cloudflare Worker packaging, D1/R2 boundaries, guarded one-time
administrator bootstrap, passwordless email identity, invitation and
submission-context provisioning, opaque sessions, RBAC, CSRF/origin protection,
rate limiting, structured errors, request IDs, observability, containerized
development, browser-friendly expired-link recovery, and the read-only
`/engine-room` operator console.

## CFP management

Implemented: program creation, browser-managed text, choice, checkbox, phone,
URL, image, and document questions; conditional display and answer-based
routing; open/close times and submission limits; branded public CFP rendering;
review-before-submit; confirmation email copy; authenticated speaker
registration; versioned drafts; owned submissions; admin submission listing;
tenant scoping; and authenticated writes.

## Organization and event administration

Implemented: organization rename, event create/edit/archive, an authenticated
application shell with a profile/sign-out menu, organization and event hubs,
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
download grants, communications, reminders, and admin progress views.

## Scheduling

Implemented: conflict preview, atomic agenda edits, concurrency protection,
publication, branded list/day/week/track/room schedule views, a browser-local
attendee itinerary, embeddable public schedule and speaker views, versioned
calendar invitations, and a read-only Sessionboard-compatible feed for pulling
accepted speakers and sessions into Accelevents.

## Release readiness

Implemented and verified: fail-closed production configuration, accessibility
coverage, large-dataset seeding, database backup/restore checks, browser smoke
tests, API benchmarks, Lighthouse audits, Cloudflare packaging, remote D1
migrations, R2, Queues/DLQs, Workflow binding, core secret bindings, deployment,
health checks, browser-route checks, and the anonymous identity boundary.

The isolated Cloudflare development rehearsal is complete with 23 passing
preflight checks. Resend and direct-R2 credentials are configured, the
organization and first administrator are bootstrapped, and the authenticated
zero-event journey created the reusable rehearsal event. Live organization/event
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
