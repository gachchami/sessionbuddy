# RBAC redesign handoff

Last updated: 2026-08-15

This file contains identity, role, membership, and authorization decisions only.
Product and page-design checkpoints are recorded in
`docs/product-design-checkpoint.md`.

## Product authorization model

One account may have one or more account-level roles:

- `organizer`
- `reviewer`
- `speaker`

A role describes how a person is using SessionBuddy. It is not a tenant or
resource membership. An Organizer can exist without an organization.

Organizations, events, programs, submissions, reviews, speaker records, assets,
messages, and schedules are resources. Access to each resource must be granted
and enforced by the server. UI visibility is never authorization.

## Active role

Exactly one assigned role is active in a session.

- Sign-in does not ask for a role.
- A new session starts with the account's default assigned role.
- The default role affects future sessions only.
- Switching role changes only the current session.
- The same account can use different active roles in different sessions.
- An active role narrows permissions and never expands them.

Role destinations:

| Active role | Destination |
| --- | --- |
| Organizer | `/admin` |
| Reviewer | `/reviews` |
| Speaker | `/speaker` |

The Profile API saves the default through `PUT /api/v1/account/default-role`.
The account menu switches the current session through
`PUT /api/v1/session/active-role`. Both verify that the requested role is an
active assignment.

## Database guarantees

- `user_roles` stores assigned roles.
- `user_roles.is_default` identifies the default role.
- A partial unique index permits at most one default role per account.
- Triggers reject an inactive role as default.
- Revoking the default deterministically selects another active assigned role.
- `session_active_roles` stores one active role per session.
- Triggers require the session role to be assigned to the session user.
- Revoking a role removes matching live session-role contexts.

Relevant migrations:

- `0047_account_roles_and_active_session_role.sql`
- `0050_default_account_role.sql`

## Authentication foundation

- Magic-link and password sign-in are supported.
- Passwords use peppered PBKDF2-HMAC-SHA256 with 600,000 iterations and random salt.
- Unknown-account verification is timing-safe.
- Sign-in uses generic failures, throttling, rate limits, secure cookies, and audit records.
- Creating or changing a password revokes existing sessions.
- Unknown or absent environment configuration fails closed.

Relevant migrations:

- `0046_password_authentication_foundation.sql`
- `0048_registration_ready_profiles.sql`

## Shared identity boundary

Account name, verified email, job title, company, time zone, description, links,
and headshot are shared identity data. Event-specific speaker information remains
event-scoped.

Private headshots require authenticated ownership checks and short-lived access.
Uploads remain quarantined until size, signature, media type, and malware checks
succeed. R2 object URLs are not public API values.

Relevant migration:

- `0049_profile_identity_details.sql`

## Resource authorization rules

- Organizer dashboard access does not require organization membership.
- Organization operations require organization-scoped permission.
- Event operations require organization/event scope and active membership.
- Event-admin membership never grants organization-wide administration.
- Reviewer pages expose only explicitly assigned review resources.
- Speaker resources remain event-scoped.
- Final evaluation decisions are immutable except through an audited correction workflow.
- Tenant and resource scope are enforced in server queries, not filtered after retrieval.

## Transitional authorization gaps

1. Legacy organization/event role paths must be migrated capability by capability.
2. Active-role narrowing and resource membership must both be checked server-side.
3. Organizer-without-organization and People empty states must not become permission errors.

## RBAC verification rule

Every protected workflow must test:

- unauthenticated rejection;
- wrong active-role rejection;
- missing membership rejection;
- cross-tenant isolation;
- permitted access at the correct scope;
- unchanged database state after a rejected mutation;
- audit, CSRF/origin, request-ID, and idempotency behavior where applicable.

## Continuation

Read this file for authorization decisions and
`docs/product-design-checkpoint.md` for the current page/workflow checkpoint.
