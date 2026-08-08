# Production identity and CFP authentication checkpoint

The CFP journey now crosses the deployed authentication and authorization
boundary. Local synthetic session adapters remain available only under
`APP_ENV=local`; product environments use guarded initial bootstrap and
passwordless email verification.

## Implemented

- Guarded one-time organization/event/administrator bootstrap using a temporary
  secret that is removed immediately after use
- Enumeration-resistant magic-link request and single-use, expiring verification
- Invitation-context provisioning for event administrators, evaluators, and speakers
- Published-form-context self-registration limited to the owning speaker
- HTTP-only opaque session cookie with a signed, versioned value
- Hash-only D1 session token storage with idle, absolute, revocation, user-status,
  and authorization-version validation
- Organization and event roles resolved from active D1 memberships
- Central named-permission authorization with deny-by-default behavior
- Origin, JSON media-type, and session-bound CSRF validation for admin mutations
- Tenant-scoped submission listing and bounded membership/list queries
- Actor identity attached to successful program and form audit events
- Cloudflare Rate Limiting bindings on local session creation and public submission,
  using keyed subject digests rather than raw identity or source values
- Standard `429 rate_limited` envelopes with a machine-readable `Retry-After` header
- Session introspection, atomic rotation, server-side logout revocation, and lifecycle audits
- Core production secrets installed through Wrangler; local secrets remain in the
  ignored `.dev.vars` file and are excluded from the Docker build context

## Runtime evidence

`scripts/smoke_cfp.py` verifies through Workerd/Pyodide/D1 that:

1. anonymous admin access returns `401`;
2. a cookie-authenticated mutation without CSRF returns `403`;
3. a cross-tenant identifier returns non-disclosing `404`;
4. an authorized organization administrator can create and publish a program;
5. a public proposal is idempotent and appears in the authorized admin list.
6. rotation revokes the old session and logout makes the cookie unusable.

`tests/security/test_production_identity_flow.py` additionally verifies an
in-memory production-mode journey from one-time bootstrap through real admin
magic-link verification, event creation, speaker invitation acceptance, speaker
profile provisioning, draft save, owned submission, and portal retrieval.

The deployed Cloudflare preflight verifies the live sign-in/admin routes, exact
origin configuration, core secret inventory, migrated D1, and an anonymous `401`
from session introspection.

## Activation boundary

The production authenticator decision is resolved as passwordless email for the
MVP. Full live delivery still requires the client's verified Resend sender/key.
The first administrator is not configured through source or Wrangler variables;
the client-approved email and event details are supplied once to
`scripts/bootstrap_cloudflare.py`, which generates, uses, and removes the
temporary bootstrap token without printing or storing it.
