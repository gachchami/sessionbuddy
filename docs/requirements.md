# Sessionbuddy — Performance-first product requirements

## 1. Purpose and source of truth

Sessionbuddy is an open-source replacement for the subset of Sessionboard used by the AI Engineer events team. It covers the path from call-for-speakers through speaker onboarding, review, scheduling, and event operations.

This document is the implementation and acceptance-testing contract. When sources disagree, use this priority:

1. The six firm requirements in the competition brief and its author comments.
2. The supplied walkthrough video and screenshots for workflow intent, not visual parity.
3. The engineering decisions in this document.

Sources reviewed on 2026-08-08:

- [Competition brief and comments](https://docs.google.com/document/d/1rBHJtiNKHv4i43tdf2Rm0sDEYuIcajhmAPoBKR_Az-A/edit?tab=t.0)
- [Sessionboard walkthrough](https://www.youtube.com/watch?v=vUuK4Knl7oc) (approximately 9:55)
- The repository and its private `harness/` test scaffolding

The brief explicitly says exact design cloning is unnecessary, the six requirements below are firm, and everything else is negotiable or best-effort. Comments further mark AI review as very optional and waive Accelevents, portal wiki/resources, and public embeds for the competition.

## 2. Product outcome and priorities

An event team must be able to:

1. Publish a routed call-for-speakers form.
2. Collect a complete speaker and proposal record without spreadsheet re-entry.
3. Review and score submissions.
4. Accept speakers, collect outstanding assets through tasks, and communicate automatically.
5. Build a conflict-free agenda quickly.
6. See onboarding progress in real time.

Priority order for trade-offs:

1. Correctness, authorization, and no cross-event data leakage.
2. Perceived and measured speed.
3. Completion of the six end-to-end MVP journeys.
4. Accessibility and operational reliability.
5. Additional features and visual polish.

The product must not trade responsiveness for feature breadth. Deferred features must not add synchronous work to MVP request paths.

## 3. Scope vocabulary

- **MVP / P0:** required for a valid first deployment and the release-level acceptance scenario.
- **P1:** first post-MVP improvements after the MVP is measured and stable.
- **P2:** advanced or waived features; build only after P0 and P1 budgets pass.
- **Not planned:** outside this product unless requirements change.

## 4. API, security, and RBAC Foundation Gate

No feature work may be considered integration-ready until this gate passes. Feature agents must consume these shared controls; they must not implement independent authentication, authorization, tenant scoping, API errors, or audit conventions.

### 4.1 Versioned API contract

- All application features use the versioned JSON API under `/api/v1`; UI-only back doors to domain data are prohibited.
- OpenAPI is generated from or verified against the implementation and checked in CI. Undocumented endpoints and schema drift fail CI.
- Requests and responses use stable opaque identifiers, ISO 8601 timestamps with explicit offsets, consistent validation errors, and documented status codes.
- List endpoints use bounded cursor pagination, allow-listed sorting, and server-side filtering. They never return an unbounded event dataset.
- Retriable create and action endpoints accept an idempotency key scoped to the authenticated principal, route, and event.
- State-changing endpoints validate content type, input size, and a strict server-side schema. Unknown security-sensitive fields are rejected.
- Rate-limit responses use `429`, include machine-readable retry information, and do not reveal account existence.

### 4.2 Authentication and session security

- MVP authentication uses single-use, short-lived passwordless email links generated from cryptographically secure random values and stored only as hashes.
- Login responses do not disclose whether an email address is registered.
- Redirect targets are restricted to allow-listed local application paths.
- Sessions use rotated, signed, secure, HTTP-only, same-site cookies with idle and absolute expiry. Logout and administrator revocation invalidate the server-side session.
- State-changing cookie-authenticated requests receive CSRF protection. CORS is deny-by-default and explicitly allow-lists trusted origins when needed.
- Authentication, verification, refresh, and upload-signing endpoints have principal/IP-aware rate limits and abuse telemetry.
- Secrets come from managed environment bindings, never source control, logs, client bundles, preview fixtures, or error responses.

### 4.3 Centralized authorization model

Authorization evaluates authenticated identity, organization membership, event membership, permission, and record ownership before domain logic runs:

```text
authenticate
  -> resolve active organization and event membership
  -> authorize permission and record ownership
  -> execute a tenant-scoped query or mutation
  -> audit sensitive actions
```

The request's `organizationId`, `eventId`, `userId`, or resource ID is lookup input, never proof of access. Every protected database query includes its authorized organization/event boundary. For cross-tenant and unauthorized-record access, APIs use the documented non-disclosing response consistently.

MVP permissions:

| Capability | Organization admin | Event admin | Evaluator | Speaker |
| --- | --- | --- | --- | --- |
| Manage organization and memberships | Allowed | Denied | Denied | Denied |
| Manage assigned event settings and programs | Allowed | Assigned events | Denied | Denied |
| Manage forms, submissions, speakers, tasks, and agenda | Allowed | Assigned events | Denied | Own records only where explicitly permitted |
| Read submission for evaluation | Allowed | Assigned events | Assigned submissions only | Own connected submissions only |
| Save/finalize evaluation | Denied unless separately assigned | Denied unless separately assigned | Assigned submissions and open round only | Denied |
| Read evaluation results/internal notes | Allowed | Assigned events | Own evaluation only | Denied |
| Read or replace speaker assets | Allowed | Assigned events | Denied | Own authorized assets only |
| Send communications | Allowed | Assigned events | Denied | Denied |
| View operational dashboard | Allowed | Assigned events | Denied | Denied |

Policies are centralized, deny by default, and accept a named permission plus resource context. Handlers may narrow access further but may not bypass the shared policy layer. Database constraints and query scoping provide defense in depth.

### 4.4 Security controls and auditability

- Rich text is sanitized against an allow-list on write and safely rendered on read.
- Upload signing verifies tenant, ownership, purpose, type, and size. Objects remain private and use unguessable keys; download authorization is checked before issuing a short-lived URL.
- Security headers include a restrictive Content Security Policy, frame policy, MIME sniffing protection, referrer policy, and least-privilege permissions policy.
- Sensitive mutations record actor, organization, event, action, target, result, timestamp, and correlation ID without storing secrets or unnecessary private content.
- Database changes and outbound effects use a transactional outbox. Consumers are idempotent and authenticate webhook/provider callbacks.
- Logs and traces redact tokens, cookies, email-link secrets, form answers, private asset URLs, and message bodies.
- Dependencies and deployed configuration receive automated vulnerability and secret scanning in CI.

### 4.5 Foundation acceptance gate

All of the following must pass locally and in an isolated preview before feature branches are integrated:

1. OpenAPI validation, representative contract tests, and API error-shape tests pass.
2. Anonymous requests cannot reach protected endpoints.
3. Expired, reused, tampered, and open-redirect login links are rejected without account disclosure.
4. Revoked sessions, invalid CSRF requests, disallowed origins, and oversized/malformed inputs are rejected.
5. Organization and event administrators cannot cross their authorized tenant boundary by changing any URL, body, or query identifier.
6. Evaluators cannot enumerate or access unassigned submissions, other evaluations, or closed-round mutations.
7. Speakers cannot enumerate or access another person's profile, submission, task, or asset.
8. Public endpoints never serialize private contacts, evaluations, internal notes, tasks, or private asset locations.
9. Idempotent retries create one domain record and one outbox effect.
10. Rate limits activate predictably and recovery does not require application restart.
11. Audit events identify the correct actor, tenant, action, target, and result for sensitive operations.
12. Tenant-scoped large-list queries are indexed, bounded, and meet the applicable performance budget.

## 5. MVP scope: the six firm capabilities

### 5.1 Custom call-for-speakers forms

Administrators can create, preview, open, close, and copy a public URL for a program-specific form.

MVP fields:

- Short text, long text, email, phone, URL
- Single choice, multiple choice, dropdown, consent checkbox
- Headshot/image upload and supporting document upload
- Standard proposal fields: title, abstract, format, category, and primary speaker

For each field, administrators can set label, help text, required state, order, active state, and choices where applicable.

Conditional logic and routing are P0, not roadmap items:

- A choice can show or hide later fields or sections.
- A rule can route a submission to a category, track, or review queue.
- Hidden fields are excluded from client and server required-field validation.
- The builder rejects cycles, missing targets, and ambiguous routing rules before publication.
- The active published form is an immutable version. Editing creates a draft version and never changes already submitted answers.

Public submission behavior:

- A visitor sees welcome text, completes the form, reviews it, and submits once.
- The form displays not-yet-open and closed states based on the event time zone.
- Server validation is authoritative; inline errors preserve recoverable input.
- Every final submission uses an idempotency key. Retries create neither a second record nor a second confirmation.
- A submitter can save a draft and resume before the deadline.
- A successful submission appears immediately in the admin list and speaker portal.

### 5.2 Self-service speaker portal and assets

Passwordless email sign-in is the MVP authentication method. Links are single-use, short-lived, and return users only to an allow-listed application path.

The portal provides:

- **Home:** event identity, deadlines, and outstanding tasks.
- **Submissions:** the signed-in person's proposals and current status.
- **Profile:** name, email, job title, company, biography, location, links, and headshot.
- **Assets:** slides and supporting documents attached to an accepted session or task.
- **Tasks:** required onboarding work, due date, state, and destination action.

Rules:

- A speaker sees only their own profile, connected submissions, assets, and tasks.
- Email is normalized for matching, but account linking requires verified ownership.
- Profile edits update the shared speaker profile. Submission-specific answers remain immutable snapshots unless explicitly edited.
- Uploads use direct signed upload URLs, are private by default, have type and size allow-lists, and are scanned asynchronously before staff download.
- Replacing an asset retains an audit entry and makes the latest clean version current.
- Completing the underlying action automatically completes its task.

### 5.3 Automated speaker communications and calendar delivery

MVP communication types:

- Submission confirmation
- Acceptance/rejection decision
- Task assignment and due-date reminder
- Schedule confirmation or change
- Direct admin-to-speaker email

Requirements:

- Templates support escaped variables for event, speaker, submission, task, deadline, and schedule.
- Admins preview recipients and rendered content before a manual or bulk send.
- Transactional sends are queued after the database commit and never block submission or scheduling UI.
- A deterministic message key prevents duplicate delivery.
- Delivery attempts, provider ID, state, and last error are visible to administrators.
- Reminder schedules are durable, cancellable, and recomputed when a due date changes.
- Accepted speakers receive an RFC 5545 `.ics` invitation that works with Google Calendar, Outlook, and Apple Calendar. Stable UIDs and increasing sequence numbers update an existing event rather than creating duplicates.
- Bulk recipients never see other recipients' email addresses.

Use Cloudflare Workflows for durable reminder/schedule orchestration and Queues for asynchronous delivery. Resend is the default email provider unless a later operational decision selects another provider.

### 5.4 Submission evaluation and scoring

Administrators can create an evaluation round, select submissions, define a rubric, assign evaluators, monitor completion, and make a decision.

MVP rubric fields:

- Numeric rating with configurable minimum and maximum
- Single-choice recommendation
- Long-text internal comment
- Required or optional state and evaluator-only guidance

Rules:

- Evaluators can open only assigned submissions.
- Evaluations can be saved as drafts and cannot be finalized with missing required answers.
- Individual evaluations are immutable after the round closes unless an admin reopens them with an audit reason.
- Aggregate scores use one documented calculation and retain individual source records.
- Evaluation completion and acceptance/rejection are separate states.
- Decision email is an explicit confirmed action; saving a decision never sends accidentally.
- Multiple sequential review rounds are supported by the data model; the MVP UI needs one active round at a time.

AI-assisted scoring or summarization is P2 and must never make or send a decision autonomously.

### 5.5 Drag-and-drop agenda with conflict detection and views

Admins can place accepted sessions on a schedule, move them by drag-and-drop, edit them with a keyboard-accessible form, and view the result by list, day, week, track, or room.

Each agenda item has a session, event date, start and end time, event time zone, room, optional track, and draft/published revision.

Rules:

- Only accepted sessions are schedulable.
- Start precedes end; default scheduling stays within event dates.
- The server rejects, rather than merely warns about, overlapping use of the same room.
- The server rejects overlapping sessions for the same speaker.
- Track overlap is allowed unless the track is configured as exclusive.
- Conflict checks run during drag preview for instant feedback and again transactionally on save.
- Optimistic updates roll back visibly when the server rejects a stale or conflicting move.
- Draft edits do not affect a published schedule revision.
- A calendar change queues an updated `.ics` invitation only after save succeeds.

The MVP must include a responsive read-only schedule view for event staff and speakers. A public website embed is P2.

### 5.6 Real-time onboarding dashboard

The event dashboard answers one operational question immediately: who is blocked and what do they still owe?

It includes:

- Counts of speakers complete, incomplete, overdue, and due soon
- A filterable list of speaker, session, missing tasks, due date, and last activity
- Filters for program, task type, completion state, and deadline
- Direct links to the speaker/session and a reminder action
- Submission counts by state and evaluation completion as secondary cards

“Real time” means a successful task/profile/asset update is reflected for other connected admin clients within 5 seconds without a full page reload. Reconnect falls back to a fresh snapshot; correctness must not depend on receiving every push event.

## 6. Supporting MVP capabilities

### 6.1 Tenancy and roles

Core entities are Organization, Event, Program, User, Person, Submission, FormVersion, Task, Asset, EvaluationRound, Evaluation, AgendaRevision, AgendaItem, Message, and AuditEvent.

Roles:

- Organization admin: all events in the organization.
- Event admin: the assigned event only.
- Evaluator: assigned evaluation content only.
- Speaker/submitter: their own connected records only.

Every protected server query and mutation enforces organization, event, and role scope. Client-side hiding is not authorization. Public identifiers are non-sequential. Status changes, assignment changes, schedule publication, asset replacement, and manual communication are audited with actor and timestamp.

### 6.2 Basic event configuration

Admins can create an event and program with name, start/end, IANA time zone, location or delivery mode, description, logo, and accent color. End cannot precede start. Event switching clears event-scoped client state and cache keys.

### 6.3 Admin lists

Submissions, speakers, evaluations, and tasks support server-side cursor pagination, sorting, search, and relevant filters. Opening and returning from detail preserves filters. No MVP list endpoint may fetch an entire event dataset.

### 6.4 API

The UI consumes the same versioned JSON API available for approved integrations. It provides consistent validation errors, stable opaque IDs, cursor pagination, idempotency on retriable writes, and machine-readable rate-limit responses. An OpenAPI document is generated and checked in CI.

## 7. Explicitly deferred roadmap

The following items must not block MVP. Items marked waived or optional in the source are kept here so they are not accidentally promoted back into launch scope.

### P1 — hardening and workflow depth

- Balanced automatic evaluator assignment and conflict-of-interest declarations
- Multiple simultaneously active evaluation rounds
- Bulk status changes with preview, dry run, and confirmation
- Communication history filters and resend tooling
- Saved admin views and CSV export
- Richer audit log viewer and configurable retention
- Offline-friendly form draft recovery
- Advanced dashboard drill-down and operational alerts
- Airtable operational mirror/import after its rate limits and failure behavior are tested

### P2 — advanced and source-marked optional/waived

- AI-assisted review, scoring suggestions, or evaluation summaries (**very optional**)
- One-way Accelevents integration (**waived**)
- Speaker-portal resource/wiki pages and arbitrary HTML embeds (**waived**)
- Embeddable public speaker gallery and schedule itinerary (**waived**, impressive if completed)
- Public agenda theme editor and dynamic iframe resizing
- Automatic schedule generation or optimization
- Multilingual forms and portals
- Payments/submission fees, SMS, sponsors/exhibitors, awards/certificates
- Full marketing suite, website CMS, advanced attribution, and broad integration marketplace

Any P2 integration runs asynchronously and may not increase P0 page or mutation latency. Arbitrary portal HTML is prohibited until a sandboxing and content-security-policy design is approved.

## 8. Performance requirements

Performance is a release gate, not a polish phase.

### 8.1 User-facing service-level objectives

Measured at the 75th and 95th percentiles on production-like data and expected launch concurrency:

| Journey | p75 | p95 | Notes |
| --- | ---: | ---: | --- |
| Public form or portal initial useful render | 1.0 s | 2.0 s | Warm edge, mid-tier mobile network |
| Admin dashboard/list useful render | 1.2 s | 2.0 s | 10,000 submissions, 2,000 speakers |
| Filter/search response | 300 ms | 750 ms | Server response plus visible update |
| Form/task/profile mutation acknowledgment | 500 ms | 1.5 s | Excludes upload bytes and email delivery |
| Agenda move save | 300 ms | 750 ms | Includes authoritative conflict check |
| Real-time dashboard propagation | 2 s | 5 s | Successful committed change to other client |

Public pages target Core Web Vitals at the 75th percentile: LCP at most 2.5 s, INP at most 200 ms, and CLS at most 0.1.

### 8.2 Load envelope

The MVP is verified with at least:

- 10,000 submissions, 2,000 speakers, 50,000 tasks, and 2,000 agenda items in one event
- 100 concurrent public form readers, 25 concurrent submitters, and 25 admin/evaluator users
- A burst of 50 final submissions per minute without duplicates or lost work

These are validation assumptions, not infrastructure limits. Replace them when actual launch estimates are known.

### 8.3 Design constraints

- Render public and read-heavy views at the Cloudflare edge where authorization permits.
- Cache only event-scoped, versioned public data; never share personalized responses.
- Use bounded, indexed database queries and cursor pagination. CI reviews query plans for large-list paths.
- Avoid request waterfalls: page loaders fetch independent summary data concurrently and stream noncritical panels.
- Images use explicit dimensions, responsive variants, and lazy loading below the fold.
- Upload clients write directly to object storage with signed URLs.
- Email, scanning, calendar generation, exports, and integration sync are asynchronous.
- Instrument server timing, database duration, cache state, queue delay, and client Web Vitals.
- Set per-route latency/error alerts and retain traces for slow requests without recording private form content.

### 8.4 Observability and benchmark history

Observability is part of the Foundation Gate. It must answer both “is the system healthy?” and “did this change make a specific API or page slower?” without collecting private form content.

API instrumentation:

- Every request receives a correlation/request ID and records environment, deployment version, route template, method, response class, duration, response bytes, D1 statement count/time/rows read, cache state, queue-publication time, and authorization outcome category.
- Responses include a safe `Server-Timing` header for total application, authentication/authorization, database, and serialization durations. It contains durations and stable metric names, never tenant IDs or private values.
- Metrics use route templates such as `/api/v1/events/{event_id}` rather than raw URLs. Organization/event identifiers are omitted or irreversibly transformed when a tenant dimension is operationally necessary.
- Errors and requests exceeding their route budget are always traced; healthy traffic is sampled at a documented rate.

Browser instrumentation:

- Capture LCP, INP, CLS, TTFB, first contentful paint, navigation type, route template, device class, and deployment version.
- Record route transitions and the API calls on the critical path so page regressions can be attributed to browser, network, Worker, or D1 time.
- Sampling excludes full URLs, user identity, form values, speaker data, and asset names. Telemetry failure never blocks or delays user interaction.

Benchmark workflow and retention:

- First-party benchmark scripts exercise named API endpoints and page journeys against the deterministic small and 10k-event seeds.
- CI runs a fast smoke benchmark and stores a machine-readable artifact containing commit, environment, dataset version, concurrency, cold/warm state, p50/p75/p95/p99, error rate, throughput, response bytes, query count/time, and Web Vitals.
- Staging runs the full Section 8 load envelope and compares results with the most recent accepted baseline and a rolling history.
- Reports distinguish cold-start from warm performance and desktop from mid-tier mobile conditions.
- A greater than 10% regression in a gated metric, any missed p95 target, an unindexed hot query, or a material error-rate increase blocks promotion unless a waiver has an owner, reason, and expiry.
- Production dashboards show API latency/error rate, page Web Vitals, D1 failures/time/rows read, queue age/retries, Workflow lateness, and real-time propagation delay by deployment version.
- Benchmark artifacts and aggregate time series follow an approved operational retention policy; raw sensitive request or response bodies are never retained.

### 8.5 Human-ready operational consoles

Human-readable consoles are a P0 operational capability. They are not a replacement for structured telemetry, and Sessionbuddy must not build a general-purpose observability platform. The implementation should configure the chosen managed observability surface and provide narrowly scoped application views only where domain context materially shortens recovery.

Minimum dashboards:

1. **Release health:** deployment version, traffic, error rate, API p50/p75/p95/p99, page Web Vitals, cold-start/import failures, and comparison with the accepted baseline.
2. **API explorer:** route-template latency, throughput, error class, response size, D1 duration/query count/rows read, cache state, and slow-trace links. It supports environment, deployment, route, method, and time filters.
3. **Page performance:** LCP, INP, CLS, TTFB, route-transition duration, critical-path API time, device class, and browser family for each page template.
4. **Async operations:** outbox age, queue depth/oldest message, retry and dead-letter counts, Workflow lateness/failure, email/provider status, and replay links restricted to authorized operators.
5. **Security and access:** authentication failures, rate-limit activity, CSRF/origin failures, authorization-denial trends, session revocations, and suspicious cross-tenant probes without exposing raw credentials, emails, or request bodies.
6. **Storage and real time:** D1 failure/latency/rows-read trends, migration version, R2 quarantine mismatches, WebSocket connection failures, and dashboard propagation delay.

Console requirements:

- Default views answer “what changed?”, “who is affected?”, “where is time spent?”, and “what is the safe next action?” within five minutes.
- Every chart shows units, time range, environment, deployment version, sample size, and whether data is sampled.
- Dashboards link from an aggregate anomaly to a redacted trace or correlated job without constructing queries from private identifiers.
- Access is least privilege and audited. Product event admins do not automatically receive infrastructure or cross-tenant telemetry.
- Consoles never expose form answers, biographies, message bodies, tokens, cookies, signed URLs, raw emails/IPs, asset names, or unrestricted SQL.
- Missing/stale telemetry is visibly distinct from a healthy zero value.
- Dashboard definitions, alert thresholds, and runbook links are version-controlled where the provider supports it.

### 8.6 Required debugging methodology

Every production issue and performance regression uses the same evidence chain:

1. Identify environment, deployment version, affected route/page template, time window, and user-visible symptom.
2. Reproduce with synthetic or redacted data locally or in an isolated preview; never copy production private content into development.
3. Follow the correlation ID from browser navigation through API request, authorization, D1 work, outbox/queue, Workflow, and provider callback as applicable.
4. Split latency using browser Navigation Timing, Web Vitals, `Server-Timing`, Worker spans, and D1 query metadata before proposing a fix.
5. Compare the affected measurement with the accepted benchmark using the same seed, concurrency, viewport/device, and cold/warm conditions.
6. Verify query plans, query count, response bytes, cache behavior, bundle changes, queue age, and error/retry state.
7. Add or strengthen an automated regression test and benchmark before changing production behavior.
8. Validate the fix in preview/staging, record before/after evidence, deploy progressively, and confirm the production signal recovers.
9. Document root cause, detection gap, corrective action, owner, and follow-up deadline for material incidents.

Debug endpoints and tools must be disabled in production unless explicitly authenticated, authorized, rate-limited, audited, and proven not to disclose tenant or secret data. Production debugging must use existing redacted telemetry rather than ad hoc request-body logging.

### 8.7 Instrumentation contract for every future API and page

No new API route, background consumer, Workflow, or user-facing page is done until it registers the standard instrumentation and dashboard dimensions.

For every API route:

- Stable route template and owning feature
- Request/correlation ID propagation
- Total, authorization, database, domain, and serialization timings where applicable
- Status class, response bytes, D1 query count/time/rows read, and cache state
- Route-specific SLO and benchmark scenario
- Redaction test, error-path test, and dashboard/alert coverage

For every browser page or route transition:

- Stable page template and owning feature
- LCP, INP, CLS, TTFB, and route-transition capture where applicable
- Critical-path API association through correlation and `Server-Timing`
- Desktop and mid-tier mobile benchmark journey
- Loading/error/empty-state measurements and telemetry-failure isolation

For every asynchronous handler:

- Versioned job type, correlation and deterministic idempotency key
- Queue delay, execution time, attempt, outcome, and safe error code
- Dead-letter/recovery signal and an authorized runbook

The shared observability package owns field names, cardinality limits, redaction, sampling, and exporters. Feature agents may add allow-listed low-cardinality dimensions but may not create incompatible logging formats or include arbitrary request/domain values.

## 9. Technical baseline

The brief gives bonus weight to Cloudflare infrastructure, Airtable persistence, speed, and an API. The default implementation uses:

- Cloudflare Workers for the web application and versioned API
- Cloudflare D1 as the transactional source of truth
- Cloudflare R2 for headshots, slides, and supporting documents
- Cloudflare Queues for email, scanning, and integration work
- Cloudflare Workflows for durable reminders and scheduled communications
- Cloudflare Durable Objects or an equivalent event-scoped channel for dashboard fan-out, with snapshot reconciliation
- Resend for transactional email
- OpenAPI for the integration contract

D1 is chosen over Airtable for the authoritative MVP write path because form idempotency, transactional conflict checks, indexed pagination, and predictable latency are mandatory. Airtable remains a P1 asynchronous operational mirror: queue changes, batch them, expose sync lag/failures, and never read Airtable during a user request. If the team later requires Airtable as the system of record, it must first pass the same load, consistency, backup, and recovery gates.

Source hosting may use GitHub or Forge; it has no runtime impact and is not a product requirement.

## 10. Security, privacy, reliability, and accessibility

- Production uses HTTPS, secure HTTP-only cookies, CSRF protection where applicable, and a proven identity implementation.
- Rich text is sanitized. Uploads are private, validated, scanned, and served through authorized or expiring URLs.
- Public responses never contain private emails, evaluations, internal notes, task details, or unscanned assets.
- Rate limits protect authentication, submission, upload signing, and communication endpoints.
- Database commits and outbound side effects use an outbox/idempotent consumer pattern.
- Data survives deploys and restarts. Automated backups have a quarterly restore test with recorded recovery time and point.
- Failure of email, scanning, or dashboard push does not roll back valid domain data; failures are retriable and visible.
- MVP workflows target WCAG 2.1 AA, are keyboard operable, use programmatic labels/errors, and never use color alone.
- Drag-and-drop has an equivalent form/keyboard interaction.
- Current and previous major Chrome, Edge, Firefox, and Safari versions are supported.

## 11. Local-first verification and deployment workflow

No change goes directly from implementation to production.

### 11.1 One-time local setup

The repository must provide checked-in setup instructions, `.env.example`, schema migrations, and deterministic seed commands. Secrets stay outside source control. Cloudflare services use local Wrangler bindings or documented emulators; email uses a captured test inbox/provider mode.

The existing private harness remains useful for independent acceptance checks:

```bash
cd harness
uv sync
uv run pytest
uv run python seed.py --submissions 10000 --output .local/seed-10k.json
npm install
npx playwright install chromium
SESSIONBUDDY_BASE_URL=http://127.0.0.1:3000 npm test
```

The application repository must add equivalent first-party seed/load commands; MVP acceptance must not depend on ignored private files.

### 11.2 Required local checks

Run before opening a deployment change:

1. Formatting, linting, type checking, unit tests, and migration checks.
2. API/integration tests against isolated local storage.
3. Authorization matrix tests, including cross-event and unassigned-evaluator denial.
4. End-to-end tests for all six MVP journeys at desktop and mobile widths.
5. Axe checks for public form, portal tasks, evaluator form, dashboard, and agenda editor.
6. Idempotency tests for final submission, outbound email, reminders, and calendar updates.
7. Schedule race test: two clients attempt conflicting moves; exactly one succeeds.
8. Load tests using the 10k-event seed and the Section 8 concurrency envelope.
9. Bundle-size and Core Web Vitals budgets.
10. Backup/restore smoke test for schema or persistence changes.

### 11.3 Preview and staging

- Every merge request gets an isolated Cloudflare preview with synthetic data and no production secrets.
- Database migrations first run against a disposable copy and include a compatible rollback or roll-forward plan.
- Staging runs the release-level scenario, security smoke tests, load smoke test, queue retry test, and email/calendar delivery to test accounts.
- Compare staging p75/p95 results to the last accepted baseline. A regression greater than 10% or any missed Section 8 p95 budget blocks promotion unless explicitly waived with an owner and expiry.

### 11.4 Production release

- Deploy progressively, run synthetic form/dashboard/agenda checks, and watch error rate, tail latency, queue age, and D1 failures.
- Roll back application code on a breached error/latency threshold; use forward-compatible migrations so rollback remains safe.
- After release, run the public smoke and accessibility checks against production and record the performance result.

## 12. Release-level acceptance scenario

MVP is complete only when a production-like environment passes this scenario:

1. An admin creates an event and program.
2. The admin publishes a form with a required upload, conditional question, and category route.
3. A new speaker signs in by email, saves a draft, submits once under a retry, and receives one confirmation.
4. The routed submission appears in the correct admin queue and only in that speaker's portal.
5. Evaluators see only their assignments, save drafts, finalize valid rubrics, and produce the documented aggregate.
6. The admin accepts the session; the speaker receives one decision message and onboarding tasks.
7. The speaker updates their biography and headshot, uploads slides, and the dashboard reflects completed/missing work within 5 seconds.
8. A scheduled reminder is delivered once; rescheduling updates the same calendar event.
9. The admin drags the session onto the agenda. Room and speaker conflicts are rejected, and list/day/week/track/room views agree.
10. A keyboard-only user can complete the form, task, evaluation, and agenda-edit alternatives.
11. The same scenario meets the Section 8 budgets on the large seed dataset.
12. No public, evaluator, or speaker response exposes another event, private contact data, internal comments, or unauthorized assets.

## 13. Definition of done

A feature is done when:

- Its acceptance criteria and failure states are automated at the appropriate layer.
- Server authorization and event isolation tests pass.
- Loading, empty, success, retry, and failure states are implemented.
- Side effects are idempotent and observable.
- Keyboard and automated accessibility checks pass.
- Its large-dataset query is indexed, bounded, and within the performance budget.
- Logging and tracing omit secrets and private content.
- Operator-facing recovery instructions exist for non-self-healing failures.
- Documentation and the OpenAPI contract are updated.
- Preview, staging, and production verification steps in Section 11 pass.

## 14. Production inputs and resolved defaults

Implementation-changing MVP defaults are resolved in `platform-decisions.md`:

1. Upload kinds, MIME types, and byte ceilings are fixed; development may record
   an audited scan bypass, while staging/production require an authenticated
   scanner and fail closed.
2. Evaluation uses arithmetic means; equal scores remain ties for an authorized
   human decision.
3. Room, speaker, event-bound, and exclusive-track conflicts are not overrideable.
4. The Cloudflare application uses Python/FastAPI/Pydantic with a compiled
   React/Vite frontend.

The following remain client/provider production-promotion inputs rather than
unspecified application behavior:

1. Verified Resend sender domain, reply-to policy, API key, and test-account ownership.
2. Actual peak traffic and data volume for replacing the provisional load envelope.
3. Data-retention schedule and approved legal/privacy consent wording.
4. Production scanner service ownership, timeout, endpoint, and secret. The
   application behavior on absence/failure is already fixed as quarantined and
   fail-closed.
