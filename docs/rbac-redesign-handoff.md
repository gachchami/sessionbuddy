# RBAC and product redesign handoff

Last updated: 2026-08-14

This document is the continuation point for the SessionBuddy RBAC and UI redesign.
It records the intended product model, what has already been implemented, what is
still transitional, and the page-by-page approval workflow agreed with the product
owner.

## Product arc

SessionBuddy is being redesigned around a single account that can participate in
the product through one or more account-level roles:

- `organizer`
- `reviewer`
- `speaker`

A role describes how a person is using SessionBuddy. It is not itself a tenant or
resource membership. In particular, a user can be an Organizer before creating or
joining any organization.

Organizations, events, programs, submissions, reviews, speaker records, assets,
messages, and schedules are resources. Access to a particular resource must still
be granted and enforced by the server. Hiding a control in the UI is never an
authorization decision.

Historical-data compatibility is not currently required because the product is
still under construction. Schema changes should nevertheless remain additive
because applied D1 migrations are an immutable ledger.

## Active-role rule

A multi-role account may have several assigned roles, but exactly one role is
active in a session at a time.

The user chooses an account-level default role under `Roles and Access` on the
Profile page. Sign-in does not ask for a role: each new session starts with the
account default. The active role is stored per session, so the same account can
work as Organizer in one browser and Speaker in another. Changing the default
affects future sessions only; switching the current role affects only that session.

The person switches role from the account navigation. The active role controls:

- the dashboard destination;
- global navigation and page vocabulary;
- which resource memberships are presented for use;
- the role context sent to server authorization checks.

The intended role destinations are:

| Active role | Destination | Primary work |
| --- | --- | --- |
| Organizer | `/admin` | Organizations, events, programs, people, scheduling |
| Reviewer | `/reviews` | Assigned reviews and conflicts |
| Speaker | `/speaker` | Speaker onboarding, assets, messages, sessions |

The server remains authoritative. An active role narrows what the user is acting
as; it must never expand their permissions.

### Database representation and guarantees

This behavior is persisted and constrained in D1; it is not a browser-only UI
preference.

- `user_roles` contains the roles assigned to an account.
- `user_roles.is_default` identifies the account's default role.
- A partial unique index permits at most one default role per account.
- Database triggers reject an inactive role as the default.
- If the default role is revoked, a remaining active assigned role is selected
  deterministically; an account with no assigned roles has no default.
- `session_active_roles` contains one active role for one session and is keyed by
  `session_id`.
- Database triggers require the active session role to be actively assigned to
  the same user as the session.
- Revoking an assigned role removes matching live session-role contexts.

Because active roles are session-scoped, the same multi-role account can be an
Organizer in one browser and a Speaker in another. Switching one browser updates
only that browser's `session_active_roles` row. It does not update the account
default or another session.

The Profile page saves the account preference through
`PUT /api/v1/account/default-role`. The account menu switches the current session
through `PUT /api/v1/session/active-role`. Both endpoints verify on the server
that the requested role is an active assignment; the UI is not trusted for this.

Password and magic-link sign-in do not present a role selector. When they create
a session, they copy the account's default role into that new session's active-role
context and route to its dashboard. Changing the account default affects future
sessions only and does not silently change existing sessions.

Relevant migration:

- `0050_default_account_role.sql`

## Resource-consumption stories

Flows should be documented and designed in this compact form:

`SignIn -> ClickA_DoA -> ClickB_DoB -> Outcome`

### Universal account flow

`SignIn -> OpenAccount -> CompleteProfile -> ChooseActiveRole -> OpenRoleDashboard`

`OpenAccount -> UpdateNameCompanyDescriptionLinks -> SaveProfile -> ProfileSharedAcrossRoles`

`OpenAccount -> ChooseHeadshot -> PreviewHeadshot -> SaveHeadshot -> HeadshotSharedAcrossRoles`

`OpenAccount -> EnterNewPasswordAndConfirmation -> SaveProfile -> RevokeExistingSessions -> SignInAgain`

`OpenAccountMenu -> SelectAnotherAssignedRole -> ActivateOnlyThatRole -> OpenItsDashboard`

### Organizer without an organization

`SignIn -> OpenOrganizerDashboard -> SeeNoOrganizationState -> ClickCreateOrganization -> EnterOrganizationDetails -> OpenOrganizationWorkspace`

An Organizer without an organization is valid and must not receive a permission
error merely for opening the Organizer dashboard.

### Organizer with resources

`SignInAsOrganizer -> OpenHome -> SelectOrganization -> SelectEvent -> ManageEventResources`

`OpenPeople -> SeeOrganizationPeopleOrEmptyState -> InviteOrManagePeople`

Detailed flows for event creation, CFP, evaluation, speaker operations, and
scheduling should be written immediately before their pages are redesigned.

### Reviewer

`SignInAsReviewer -> OpenMyReviews -> SelectAssignedReview -> DeclareConflictOrEvaluate -> SubmitEvaluation`

Only explicitly assigned review resources should appear. Final evaluation
decisions remain immutable except through an audited correction workflow.

### Speaker

`SignInAsSpeaker -> OpenSpeakerPortal -> SelectEvent -> CompleteProfileAndTasks -> ManageAssetsAndMessages`

Event-specific speaker data remains event-scoped even though the account profile
and headshot are shared identity data.

## UI redesign and approval workflow

The redesign starts from the existing code and preserves useful functional logic.
Every page or navigation portion follows this sequence:

1. Inspect the current implementation and relevant RBAC/resource behavior.
2. Use the `frontend-design` skill to establish intentional hierarchy and responsive behavior.
3. Implement the page together with any required API and schema changes.
4. Add narrow unit, integration, accessibility, and browser tests.
5. Run the page at desktop and a real phone viewport (currently 390 by 844).
6. Show the page and screenshots to the product owner.
7. Wait for explicit approval before moving to the next page/navigation portion.

Do not treat a resized or cropped desktop screenshot as mobile verification.
Mobile browser tests must check horizontal overflow and important component bounds.

## Implemented foundation

### Password authentication

- Existing magic-link sign-in remains supported.
- Password credentials are stored separately from `users`.
- Passwords use peppered PBKDF2-HMAC-SHA256 with 600,000 iterations, random salt,
  constant-time verification, length validation, and basic common-password rejection.
- Password sign-in uses generic failures, fake verification for unknown accounts,
  rate limiting, account throttle state, secure session cookies, and audit records.
- Creating or changing a password revokes existing sessions.
- Password management is part of the unified profile form rather than a separate endpoint/page.

Relevant migrations:

- `0046_password_authentication_foundation.sql`
- `0048_registration_ready_profiles.sql`

### Account roles and sessions

- `user_roles` stores account-level Organizer, Reviewer, and Speaker assignments.
- `session_active_roles` stores at most one active role for a session.
- Revoking a role removes it from live session context.
- Sign-in activates the saved default role. A deterministic Organizer, Reviewer,
  then Speaker fallback is used only when an older/incomplete account has no
  explicit default yet.
- The user chooses one assigned default role on the Profile page; new password
  and magic-link sessions start with that role.
- Active roles are persisted per session, allowing different browsers to use
  different roles for the same account at the same time.
- `user0@example.test` is a local Organizer with no organization membership.

Relevant migration:

- `0047_account_roles_and_active_session_role.sql`
- `0050_default_account_role.sql`

### Unified account profile

The Account page and profile API currently support:

- first and last name;
- verified, read-only sign-in email;
- job title, company/team, and IANA time zone;
- shared description;
- Website, LinkedIn, and X HTTPS URLs;
- optional password creation/change;
- read-only account roles;
- private headshot upload, preview, replacement, retrieval, and removal.

Headshots accept JPG, PNG, or WebP up to 5 MB. The server checks the declared
media type, byte limit, file signature, CSRF/origin policy, ownership, and malware
scan before use. R2 object URLs are not exposed directly.

Relevant migration:

- `0049_profile_identity_details.sql`

### Shared navigation corrections

- The shell uses the SessionBuddy SVG brand asset rather than a generated `S` tile.
- Organizer navigation includes Home, Events, and People.
- The account control displays the active role name (`Organizer`) instead of the
  generic phrase `Active role`.
- The Account page shows `Roles and Access` and does not repeat `Organizer` twice.

## Current page status

### Sign-in

Implemented with password and magic-link paths and a SessionBuddy program-pass
visual direction. Relevant focused tests exist in
`tests/release_readiness/test_sign_in_design.py`.

### Account/Profile

The first version was approved, then reopened to add headshot, description, URLs,
brand/navigation fixes, and stronger mobile behavior. The latest revision includes:

- no persistent `Your account is up to date` load message;
- verified-email help directly below the email field;
- session-revocation help inside the password section;
- single-column phone layout;
- correctly sized headshot controls;
- three-column profile links on desktop and one column on phones;
- a real 390 by 844 Playwright regression test.

The latest revised Account page is awaiting final product-owner approval.

## Transitional gaps that must not be forgotten

1. Authorization still contains legacy organization/event role paths. Each
   capability must be migrated carefully so active-role narrowing and resource
   membership are both enforced on the server.
2. `/admin` currently assumes legacy organization/event administration in parts
   of its logic. It needs the Organizer-without-an-organization empty state.
3. The People destination is now visible to Organizers, but its page/API must gain
   the correct no-organization empty state rather than returning a legacy permission error.

## Verification recorded at this checkpoint

- Account responsive Playwright test passes at 390 by 844 with no horizontal overflow.
- The focused migration/security/profile/navigation suite passed (55 tests).
- The focused static UI/accessibility suite passed (36 tests after copy/alignment changes).
- Frontend TypeScript checking and Ruff passed.
- Embedded assets and OpenAPI were regenerated after API/UI changes.
- All database work was local; no remote Cloudflare database was modified.

## Next session

First, open `/account`, review the latest desktop and mobile revision, and obtain
explicit final approval. Do not start a new page before that approval.

After approval, the next page is Organizer Home at `/admin`:

`SignInAsOrganizer -> OpenOrganizerHome -> SeeNoOrganizationWelcomeState -> ClickCreateOrganization`

Only design and implement the Organizer Home/navigation portion first. Show desktop
and mobile, run its tests, and wait for approval before implementing the Create
Organization flow.

## Pasteable continuation prompt

> Continue the SessionBuddy RBAC redesign from `docs/rbac-redesign-handoff.md`.
> Read that document and the repository `AGENTS.md` first. We are using one active
> account role per session, with resource access separately enforced by the server.
> Resume by showing me the latest Account page revision for final approval. Do not
> move to Organizer Home until I approve it. For every page/navigation portion,
> use the frontend-design skill, preserve useful existing logic, implement required
> API/schema changes with the UI, add and run tests, show desktop and real mobile
> screenshots, and wait for my approval before continuing.

## Organizer Events checkpoint — 2026-08-15

This checkpoint supersedes the older "Next session" instructions above for the
Organizer workflow. Sign-in, Organizer Home, shared navigation, role switching,
and the Organizer Events index have now been reviewed and implemented.

### Approved Events index

The approved `/admin/events` design is a compact operational list consistent
with the Organizer Home design:

- filter order is All, Active, Drafts, Past, with All selected initially;
- search and sorting are server-backed and the list is cursor-paginated;
- the event name is the single entry point to the event workspace;
- Date, Status, and CFP have dedicated columns;
- CFP shows one `Manage CFP` action with a quiet proposal count;
- Edit remains visible, while Duplicate is in an accessible More menu;
- raw inline link/status sentences were rejected and removed;
- mobile rows become structured cards with no horizontal overflow.

The page assumes the signed-in Organizer's one assigned organization. It does
not expose a create-organization workflow.

### Event creation and draft contract

- New-event actions are `Save draft` and `Create event`.
- Drafts remain private until activated.
- Drafts may store past dates when end is after start.
- Creating or activating an event requires its end time to remain in the future.
- Expired activation shows `Update the event dates before activating.`
- Editing an existing completed event remains possible for corrections.
- Duplicate opens the normal creation form prefilled from the source and creates
  nothing until the organizer submits it.
- Logo and cover retention use fresh event-owned assets; operational records are
  never copied.

The Create Event form, including branding and the representative public-header
preview, was explicitly approved before returning to the Events index.

### Verification at this checkpoint

- Full Python suite: 599 passed after the draft-date implementation.
- Events static/accessibility checks and frontend TypeScript checks passed.
- Events responsive Playwright: 2 passed after the first-principles row redesign.
- Desktop and 390px mobile browser review passed; mobile had no horizontal overflow.
- Embedded assets were regenerated and the local Worker was rebuilt.
- `git diff --check` passed.

### Next sessions

Continue in separate review sessions, one workflow at a time:

1. Event workspace: open an event from `/admin/events`, assess its landing page,
   navigation, information hierarchy, form validation, and runtime failures.
2. CFP: enter through the event's `Manage CFP` action, assess setup, publishing,
   public form, submissions, and failure workflows.

Do not redesign Event workspace and CFP together. Obtain approval for the Event
workspace first, then start the CFP session.

### Standing correctness rule

For every approved workflow, verify the rule at all three boundaries:

- **API:** authentication, RBAC, lifecycle validation, conflict handling, and a
  safe error response are enforced by the server.
- **UI:** controls, status, copy, recovery, and navigation accurately represent
  the server rule; UI visibility is never treated as authorization.
- **Database/query:** writes cannot bypass the lifecycle rule, reads cannot leak
  private or cross-tenant state, and failed transitions leave stored state unchanged.

Tests must cover the successful path, prohibited transition, unchanged database
state after failure, tenant/RBAC isolation, and the matching frontend state.

Current CFP invariant:

- draft events allow private CFP configuration and preview;
- publishing is rejected until the event is active with
  `Activate the event before publishing its CFP.`;
- public CFP reads, submission drafts, and submission creation require an active
  parent event, including when a previously published event is moved to draft;
- archived events do not expose the CFP workspace.

### Pasteable Event workspace continuation prompt

> Continue the SessionBuddy Organizer redesign from the "Organizer Events
> checkpoint" in `docs/rbac-redesign-handoff.md`. Start a focused Event workspace
> review by opening an event from `/admin/events`. Use the RBAC document and the
> frontend-design skill, preserve the approved shared navigation and Events index,
> list validation/runtime failures before implementation, show desktop and mobile,
> and wait for approval before moving to CFP.

### Pasteable CFP continuation prompt

> Continue the SessionBuddy Organizer redesign from the "Organizer Events
> checkpoint" in `docs/rbac-redesign-handoff.md`. Start the CFP workflow from an
> event's `Manage CFP` action. Use the RBAC document and frontend-design skill,
> assess setup, publication state, public submission experience, submissions,
> validation and runtime failures, then implement and verify only after approval.
