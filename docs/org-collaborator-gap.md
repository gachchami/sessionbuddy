# Organization collaborator provisioning

**Purpose:** Define the supported organization-administrator model and the
remaining account-provisioning gap.

**Status:** in-progress

**Authority:** The current schema, API contracts, authorization policy, and
executable tests outrank this record. `docs/product-status.md` is authoritative
for shipped capability status.

## Supported authority model

An organization has one owner and may have additional administrators. Both can
manage every event in that organization through organization-level authority.
There is no event-administrator role, individual event ownership, or
event-targeted administrative grant.

Speaker and evaluator relationships remain event-scoped participation. They do
not grant organization or event administration.

The only assignable organization grant is `manage`. Organization `view` and
`edit` grants are retired because organization-scoped operations require
administrative authority.

## Current provisioning paths

The event invitation API accepts `organization_admin` and, after acceptance,
creates organization-level `manage` authority. The invitation is nevertheless
stored under an event and its delivery, resend, and revocation operations remain
event-keyed.

The organization settings page can grant administrator access only to an
already-active SessionBuddy account. It does not provision a new account or
send an invitation. The event Team and access page exposes reviewer management,
not organization-administrator invitations.

Consequently, the authority model is implemented but first-time organization
collaborator provisioning is not yet represented honestly in the organization
UI or API. An organization with no event has no supported invitation anchor.

## Required completion

The organization workspace needs an organization-scoped invitation workflow
that can create, list, resend, and revoke administrator invitations without an
event identifier. It must:

- require organization access-management authority on the server;
- provision the invited account as an organization administrator with `manage`;
- keep invitation and communication records organization-scoped;
- support organizations that do not yet contain an event;
- preserve rate limits, audit records, challenge revocation, and non-disclosing
  authentication behavior; and
- clearly distinguish inviting a new administrator from granting access to an
  existing account.

Until that workflow exists, do not present the existing-account grant form as
an invitation control and do not reintroduce event administration as a
workaround.
