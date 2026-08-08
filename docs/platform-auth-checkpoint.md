# Production identity and CFP authentication checkpoint

The CFP journey now crosses the deployed authentication and authorization
boundary. No synthetic identities exist in the application. Fresh
instances use guarded initial setup and passwordless email verification.

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
- Cloudflare Rate Limiting bindings on passwordless sign-in and public submission,
  using keyed subject digests rather than raw identity or source values
- Standard `429 rate_limited` envelopes with a machine-readable `Retry-After` header
- Session introspection, atomic rotation, server-side logout revocation, and lifecycle audits
- Core production secrets installed through Wrangler; local secrets remain in the
  ignored `.dev.vars` file and are excluded from the Docker build context

## Runtime evidence

`tests/security/test_production_identity_flow.py` additionally verifies an
in-memory production-mode journey from one-time bootstrap through real admin
magic-link verification, event creation, speaker invitation acceptance, speaker
profile provisioning, draft save, owned submission, and portal retrieval.

The deployed Cloudflare preflight verifies the live sign-in/admin routes, exact
origin configuration, core secret inventory, migrated D1, and an anonymous `401`
from session introspection.

The live development rehearsal verified administrator sign-in and existing-user
speaker invitation acceptance without exposing either magic link. D1 shows one
accepted speaker invitation and active event-admin/speaker memberships; the
speaker portal returned only the actor-owned proposal and asset.

## Activation boundary

The production authenticator decision is resolved as passwordless email for the
MVP. The development Resend key/test sender is configured and has accepted a live
administrator message; a verified client domain remains a production-promotion
input for delivery to arbitrary recipients. The first administrator is not
configured through source or Wrangler variables. The approved organization and
named administrator can be supplied through `/setup` using a one-time Worker
secret. The command-line bootstrap remains available for automation and removes
its temporary token. Bootstrap creates no placeholder event; the verified
administrator creates the first event from the application.

An existing verified user can also accept a later event-role invitation. The
invitation takes precedence when issuing that user's next challenge, acceptance
adds only the invited role/ownership records, and the transition is audited.
