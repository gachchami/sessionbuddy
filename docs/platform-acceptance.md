# platform foundation acceptance record

Date: 2026-08-08  
Scope: shared application foundation; no product journey or production identity provider

## Accepted locally

| Gate | Evidence |
| --- | --- |
| Cloudflare-compatible Python runtime | Docker gate starts the pinned Workerd/Pyodide runtime and imports the full Worker bundle. |
| Versioned API and OpenAPI | Explicit operation IDs and response models; deterministic checked-in OpenAPI; route/observability enumeration tests. |
| Error and security headers | Central safe envelopes for HTTP, validation, and not-found errors; CSP, frame, MIME, referrer, and permissions policies. |
| Passwordless primitives | 256-bit token generation, hash-only persistence boundary, single-use challenge schema, redirect attack rejection. |
| Session security | Versioned HMAC cookie, live D1 session protocol, idle/absolute expiry, revocation, user-state and authorization-version checks. |
| CSRF/origin boundary | Exact origin or same-origin referer, JSON media type, and session-bound CSRF proof are all required. |
| Central RBAC | Exhaustive role/permission matrix plus ownership, assignment, lifecycle, inactive-principal, and tenant denial tests. |
| Tenant persistence | Frozen authorized scopes, repeated organization/event SQL predicates, bounded keyset pagination, prepared values, composite constraints and indexes. |
| Mutation integrity | Atomic command batch composes idempotency, domain change, audit, outbox, and completion; provider errors are redacted. |
| Abuse control | Provider-neutral rate-limit protocol and deterministic fixed-window local adapter with recovery and subject-isolation tests. |
| Observability | Request ID, structured route timing, safe `Server-Timing`, route manifest/SLO/runbook registration, generic route benchmark output. |
| Human review surface | `/engine-room` exposes only an allow-listed synthetic status model and no tenant, identity, speaker, submission, or secret data. |
| Isolated workflow | Dependencies, lint, tests, migrations, Workerd smoke checks, and local benchmark run inside Docker. |

## Deliberately deferred to product capabilities

- Real email-provider delivery and account provisioning are introduced with the first passwordless product journey.
- Concrete FastAPI authentication dependencies are mounted with the first protected product route; they must call the accepted authentication pipeline rather than recreate it.
- Cloudflare's production rate-limit binding, Queues, Workflows, R2, and provider secrets are added only when their consuming feature exists.
- Cloudflare Access is optional defense in depth for operator surfaces. It is not application RBAC and is not activated because its checkout requires payment details and overage authorization.
- Remote load benchmarks remain paused by user direction. Deployments receive only minimal correctness smoke checks.

## Rule for CFP management and later

No feature may introduce its own cookie format, session rules, origin/CSRF checks, role checks, unscoped repository, error envelope, rate-limit shape, audit convention, or observability contract. A missing shared capability extends platform foundation through a reviewed change.
