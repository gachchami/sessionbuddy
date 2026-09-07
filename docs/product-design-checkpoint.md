# Product design checkpoint

Last updated: 2026-08-15

This file records approved product workflows, page designs, validation behavior,
and continuation points. RBAC and resource-authorization rules live separately in
`docs/rbac-redesign-handoff.md`.

## Working method

For each page or workflow:

1. Inspect the current implementation and API/resource behavior.
2. Establish intentional desktop and mobile hierarchy with the frontend-design skill.
3. List form-validation and runtime-failure workflows before implementation.
4. Implement required UI, API, and persistence/query changes together.
5. Add unit, integration, accessibility, and browser tests.
6. Verify desktop and a real 390 by 844 viewport, including horizontal overflow.
7. Obtain approval before moving to the next workflow.

## Approved shared experience

- Sign-in supports password and magic-link paths without a role selector.
- New sessions open the dashboard for the account's default role.
- Signed-in users visiting `/` are routed to the active-role dashboard.
- Organizer navigation contains Home and People. Home is the event ledger;
  event-specific destinations appear after opening an event.
- The account menu shows identity, assigned roles only, Account settings, and Sign out.
- Role switching occurs from the account menu and affects the current session only.
- Organizer pages assume the user's assigned organization; no create-organization UI is exposed.
- Event-facing schedule and deadline times use the event's IANA time zone and
  display a zone label. Personal activity times may use the viewer's local time
  only when the interface explicitly labels them as local.

## Organizer Home

Approved direction:

- organization identity and an organization selector when more than one is
  manageable;
- Create event and the event ledger as the primary job;
- a compact, manager-only Recent activity rail after the ledger;
- quiet access to organization settings;
- no separate global Events destination and no create-organization workflow.

## Organizer Home and events

Approved `/admin` event ledger:

- filter order: All, Active, Drafts, Past; All is the default;
- server-backed search, sorting, cursor pagination, and race-safe responses;
- event name is the single entry point to the event workspace;
- Date, Status, Program, and Actions are separate columns;
- Program shows publication state and links only when attention is needed;
- Settings and Duplicate remain visible secondary actions;
- no inline link/status sentence;
- mobile rows are structured cards without horizontal overflow.

### Create and edit event

- New-event actions are `Save draft` and `Create event`.
- Drafts remain private until activated.
- Draft dates may be in the past when end is after start.
- Creating or activating requires the event end to remain in the future.
- Expired activation says `Update the event dates before activating.`
- Existing completed events remain editable for corrections.
- The approved form includes minimal copy, inline optional labels, email defaults,
  branding uploads, and a representative shared public-header preview.
- A persistent embedded map was rejected because Location may contain a venue,
  city, free text, or meeting URL.

### Duplicate event

- Duplicate opens the normal create form prefilled from the source.
- Nothing is created until the organizer submits the form.
- The organizer may change all copied event fields.
- Retained branding is copied into fresh event-owned assets.
- CFP, slug, proposals, reviews, people, speaker operations, schedule, messages,
  audit history, and other operational records are not copied.

## Draft-event CFP invariant

- Draft events allow private CFP configuration and preview.
- Publishing is rejected until the event is active with
  `Activate the event before publishing its CFP.`
- Public CFP reads, submission drafts, and submission creation require an active
  parent event, including when a published event is later moved to draft.
- Archived events do not expose the CFP workspace.

## Event workspace

Implemented direction, awaiting final product approval:

- the overview is an event command page, not a duplicate navigation directory;
- event identity, dates, delivery, location, lifecycle status, time zone, and the
  public-page action form one quiet header;
- one phase-aware `Up next` panel owns the primary action;
- a live CFP with zero proposals recommends bringing in the first proposal, not
  reviewing an empty inbox;
- Proposals, Speakers, and Agenda provide the compact operational snapshot;
- the five real program stages remain as a compact status rail with linked
  destinations; state markers replace decorative step numbers;
- successful loading is silent; partial data failure gives a recoverable refresh
  message, while authentication, missing-event, and tenant checks remain server-backed;
- event navigation is narrower and grouped by Program, Speakers, Publish, and
  a quiet Manage utility; redundant `Current event` and public-schedule links
  are removed;
- the global top navigation and current-event sidebar retain separate scopes;
- desktop and 390 by 844 mobile layouts have no horizontal overflow, and the
  mobile navigation drawer fits all destinations within the viewport.

## Standing correctness rule

Every approved workflow must agree at three boundaries:

- **API:** authentication, RBAC, lifecycle validation, conflict handling, and safe errors.
- **UI:** controls, status, copy, recovery, and navigation accurately represent the rule.
- **Database/query:** rejected writes leave state unchanged; reads cannot leak private
  or cross-tenant state.

Tests cover the successful path, prohibited transition, unchanged state after
failure, tenant/RBAC isolation, and matching frontend behavior.

## Verification checkpoint

- Full Python suite passed with 599 tests after the Events/draft-date work.
- Events static, accessibility, and frontend TypeScript checks passed.
- Events responsive Playwright passed 2 tests after the row redesign.
- Desktop and 390px browser reviews passed without horizontal overflow.
- Embedded assets were regenerated and the local Worker rebuilt.
- `git diff --check` passed.

## Next sessions

1. Event workspace: assess its landing page, navigation, hierarchy, validation,
   and runtime failures. Obtain approval.
2. CFP: assess setup, publication states, public form, submissions, validation,
   and runtime failures in a separate session.

Do not redesign Event workspace and CFP together.

### Event workspace continuation prompt

> Continue from `docs/product-design-checkpoint.md`. Start a focused Event
> workspace review by opening an event from `/admin`. Also read
> `docs/rbac-redesign-handoff.md` for authorization rules. List validation and
> runtime failures before implementation, verify desktop and mobile, and wait for
> approval before moving to CFP.

### CFP continuation prompt

> Continue from `docs/product-design-checkpoint.md`. Start the CFP workflow from
> an event workspace's **CFP** navigation item or the Overview's primary CFP
> action. Also read `docs/rbac-redesign-handoff.md` for
> authorization rules. Assess setup, publication, public submission, submissions,
> validation, and runtime failures before implementation.
