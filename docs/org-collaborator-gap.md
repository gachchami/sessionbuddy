# Design flaw: there is no way to invite an organizer

**Status:** confirmed, verified end-to-end from schema to console
**Severity:** blocks multi-organizer use entirely; deadlocks a zero-event organization

---

## 1. The claim, restated precisely

There are two org-level things a person might mean by "add an organizer," and SessionBuddy has neither:

| | Exists? | Where | What it actually does |
|---|---|---|---|
| **Invite** an organizer (provisions an account) | **No org-level route or UI at all** | — | — |
| **Grant** an existing account org access | Yes, `/account` → Organization settings | `account.js:154-200` | Requires the person to *already have an account*; 404s otherwise |

The only real invitation control in the product is the **"Invite someone"** dialog on `/admin/events/{event_id}/access` — an *event* page. The organization-level option is smuggled into it as a hidden `<option>`:

```html
<!-- access_admin.html:41 -->
<select name="role">
  <option value="speaker">Speaker assignment</option>
  <option value="evaluator">Reviewer assignment</option>
  <option value="event_admin">Can manage this event</option>
  <option value="organization_admin" hidden>Can manage this organization</option>
</select>
```

unhidden at runtime by `access_admin.js:92-93` when you hold org `owner|manage`. So the one control that can bring an organization administrator into the product lives behind an event, on a page called "Team & access" for *that event*, in a dialog headed "Invite someone."

---

## 2. Why the org-level grant form can't substitute

The `/account` panel looks like the right control — it isn't. Its own labels give it away:

```js
// account.js:172-174
emailLabel.textContent = "Account email";       // ← not "email address"
// account.js:189
grant.textContent = "Grant organization access";  // ← not "invite"
```

The endpoint behind it is a pure lookup with no provisioning branch:

```python
# platform/auth/access.py:3767-3772
user_id = await db.prepare(
    "SELECT id FROM users WHERE normalized_email=?1 AND status='active' LIMIT 1"
).bind(normalized).first("id")
if user_id is None:
    raise HTTPException(status_code=404)
```

No challenge issued, no email sent. The UI turns that 404 into:

> *"No active SessionBuddy account uses that email. Ask them to sign in once, then try again."* — `account.js:243-245`

**That instruction cannot be followed.** `request_magic_link` resolves three contexts — existing user, pending invitation, published CFP form — and silently no-ops otherwise:

```python
# access.py:4199-4209
if invitation is not None:      context, provisioning_context = invitation, "invitation"
elif submission_context is not None: context, provisioning_context = submission_context, "submission"
elif user is not None:          context, provisioning_context = user, "existing_user"
else:
    return GenericAccepted()     # 202, no challenge written, no email sent
```

Password sign-in 401s for the same reason (`access.py:4287-4331`). So the product tells you to have them sign in, and provides no way for them to sign in.

---

## 3. The deadlock

`identity_invitations.event_id` is `NOT NULL` and FK-bound to a real event:

```sql
-- migrations_baseline/0001_baseline.sql:667-688
CREATE TABLE "identity_invitations" (
  organization_id TEXT NOT NULL,
  event_id        TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('organization_admin','event_admin','evaluator','speaker')),
  ...
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id),
  UNIQUE (organization_id,event_id,normalized_email,role)
);
```

An organization-wide invitation is **physically un-insertable** without an event. And bootstrap explicitly supports creating an organization with no event (`access.py:815`, `:912`).

All four ways a `users` row can come into existence:

| Path | Event required? | Can an org owner initiate it for someone else? |
|---|---|---|
| `POST /api/v1/bootstrap` (`access.py:872`) | No | **No** — one-shot; the deployment key is destroyed by trigger on *any* org insert (`0001_baseline.sql:1669`) |
| Invitation acceptance (`access.py:4492`) | **Yes** | Yes — but needs ≥1 event |
| Public CFP registration (`access.py:4669`) | **Yes** (form → event FK) | No — speaker self-service |
| Co-speaker acceptance (`cfp/router.py:1013`) | **Yes** | No — speaker-initiated, yields `speaker` only |

**Net: an organization with zero events cannot add a second organizer at all.** The owner must create a throwaway event purely to obtain an `event_id` to hang an org-wide invitation on.

And the workaround leaves permanent residue. The org-level fact is filed under that arbitrary event forever — `list_invitations` filters `WHERE organization_id=?1 AND event_id=?2` (`access.py:3376`), so one unrelated event's Team page shows `co-owner@example.com · Can manage this organization · accepted`, and the invitation email lands in that event's communications log (`access.py:3315`). Revoke and resend are both event-keyed, so cleanup requires remembering which event you used.

---

## 4. Two more failures behind the first

Fixing only the invite route would not produce a working collaborator.

### 4a. Existing-event visibility for organization administrators — resolved

Acceptance grants an organization-scoped `manage` grant and an `organizer`
persona, without duplicating an event membership or grant for every event.
`policy.py` cascades that authority to events in the exact organization.
`list_events` now mirrors the policy: organization owners and active `manage`
grantees see events created by other organizers, including across cursor pages.
Event-only owners and `edit`/`manage` grantees still receive only their
explicitly authorized events, and foreign organizations remain
indistinguishable from missing resources.

### 4b. `view` and `edit` org grantees are locked out of the console entirely

The shell gates all organization navigation on the `manage` tier and bounces anyone else before a page renders:

```js
// app_shell.js:137-139
function canManageOrganization(session) {
  return (session.organization_access || []).some((item) => holds(item, ADMIN_PERMISSIONS));
}                                                    // ADMIN_PERMISSIONS = ["owner","manage"]

// app_shell.js:108-112
function organizerDestination(session) {
  if (canManageOrganization(session)) return "/admin";
  const event = eventsWithContentAccess(session)[0];
  return event ? `/admin/events/${...}` : null;
}

// app_shell.js:737-741
if (!dashboardDestination(session)) { renderSessionContractError("workspace"); return; }
```

→ *"Organizer access is unavailable because this session has no manageable organization or event."*

This is wrong on the server's own terms. `policy.py:68` authorizes `EDIT|MANAGE` for everything except `RESOURCE_ACCESS_MANAGE`, so an org-`edit` grantee **is** authorized for `ORGANIZATION_MANAGE` — create events, rename the org, org branding, speaker directory. The API would serve them a working workspace; the shell refuses to render it. The comment at `app_shell.js:93-99` claims to mirror `policy.py` and doesn't.

And `view` authorizes **zero** permissions anywhere in the policy — yet it is the first option in both grant dropdowns (`account.js:184`, `access_admin.html:22`). Granting "Can view" produces a user who can sign in and reach nothing.

---

## 5. A product decision you need to make first

The docs contradict each other, and the code follows neither consistently.

`docs/platform-decisions.md:15` (recorded as an accepted contract):
> "Organization admins receive organization-wide event permissions through policy without duplicated event rows."

`docs/requirements.md:271`:
> "Organization admin: all events in the organization."

`docs/product-status.md:208-211` (the reversal, never reconciled back):
> "Organization access does not automatically cascade to its events."

The code implements the third and tests pin it. So: **does organization authority cascade to that organization's events, or not?** Everything below branches on this answer, and it's a product question, not a bug.

- **Cascade** — one grant makes a real co-organizer. Simpler mental model, matches `requirements.md`. Cost: the "org admin who shouldn't see the sensitive event" case becomes unexpressible.
- **No cascade** (today) — per-event grants stay explicit. Cost: adding a co-organizer to an established org is an N-event chore with no bulk control, and `requirements.md` is wrong.

My read: cascade is what the product promises and what users expect from "Can manage this organization." If you keep no-cascade, the label has to change and you need a bulk "grant on all events" affordance.

---

## 6. Proposed fix, in order

**1. Make the invitation organization-scoped.**
Schema: `identity_invitations.event_id` → nullable; relax `UNIQUE (organization_id,event_id,normalized_email,role)` to handle NULL event_id (SQLite treats NULLs as distinct — use a partial index or a sentinel). Drop the composite FK for the org-scoped case. Per AGENTS.md this is a baseline edit, so purge and recreate dev databases and prove first-apply + repeat-no-op.

**2. Add `POST /api/v1/admin/organizations/{organization_id}/invitations`** (+ list/resend/revoke siblings), gated on `RESOURCE_ACCESS_MANAGE` against the org — mirroring `_organization_access_control_context` (`access.py:3605`). Reuse `_issue_invitation_link`; make the `communication_messages` row org-scoped with a null `event_id`.

**3. Give the organization a Team page in the workspace nav.**
Today org access lives at `/account` behind the avatar menu (`account.html:54-58`), gated at `account.js:526`. It belongs in the sidebar next to Events. Note the existing "People" entry (`app_shell.js:434`) points at the *speaker* directory and will be confused for it — rename one of them.

**4. Resolve the shell/policy divergence** (`app_shell.js:108-139`). At minimum let `edit` reach `/admin`, since the server already authorizes it. Then decide what `view` means — add a read-only branch in `policy.py` (`DASHBOARD_READ` + list/GET permissions), or remove the option from `ResourceGrant` and both dropdowns. Shipping a team feature whose *first* permission option is inert is worse than not shipping it.

**5. Make revocation safe.** `revoke_organization_access_grant` (`access.py:3814-3858`) revokes the grant but leaves the `user_roles` `'organizer'` row, so a removed collaborator keeps `active_role: "organizer"` with zero grants and hits the §4b wall on every page including `/account`. Revocation becomes routine the moment teams exist.

**6. Fix the sign-in dead end regardless.** `request_magic_link` returning a silent 202 for unknown emails is correct anti-enumeration behaviour, but the org-grant error copy must stop telling people to do the impossible. Once §2 exists, that copy becomes "Send them an invitation instead" with a link.

**Also worth doing in the same pass:** `_write_event_grant` (`access.py:2997-3067`) inserts `user_roles` but — unlike the org grant path (`access.py:3666-3677`) — never inserts an `organization_memberships` row. Since `request_magic_link` finds users via that membership join (`access.py:4172-4175`), an event-access grantee without one gets a silent no-op on every sign-in attempt.

---

## 7. Adjacent gaps that surface the moment teams are real

Not blockers for the fix, but they turn a working feature into a frustrating one:

- **Reviewer onboarding has the identical shape one level down.** Evaluators must already be an active `event_memberships` row with `role='evaluator'` for that exact event (`evaluation/router.py:369-381`, `:711-720`, plus an FK at `0001_baseline.sql:377`). There is no `POST /events/{id}/members` — only `GET` and `DELETE` (`access.py:3502-3526`). A returning reviewer must be re-invited by email for every new event. Fix with the same mechanism if you can.
- **No attribution anywhere.** `created_by_user_id`, `decided_by_user_id`, `granted_by_user_id` are all written and essentially never returned — exactly one attribution field exists in any response model (`competition/models.py:158`). "Who changed this" is rhetorical with one organizer and the first question asked with three.
- **`audit_events` is write-only.** One `INSERT` (`platform/db/commands.py:79`), no `SELECT`, no route — with indexes (`0001_baseline.sql:1298-1303`) built for queries nobody can issue. It's a ready-made org activity feed.
- **No organizer-facing notifications at all.** All 14 `communication_messages` inserts are speaker- or reviewer-directed; the template `kind` enum (`0001_baseline.sql:354`) has no organizer kind. Nobody is told when a proposal arrives. Already broken solo; a coordination failure with a team.
- **Conflict recovery remains inconsistent outside Event settings.** The routed
  Event settings editor now reconciles field-level conflicts with **Keep mine**
  and **Use latest** choices (`event_editor.js`). The CFP editor still branches
  on `error.code === "stale_conflict"`, which the server never emits
  (`api/app.py` maps all 409s to `"conflict"`), so two organizers editing one CFP
  get "The request conflicts with current state." `admin_submissions.js` has no
  dedicated 409 branch at all.
- **`organizations[0]` is assumed.** `admin_home.js:68` and `access.py:5147-5150` both take the first entry. Harmless while bootstrap guarantees one org; wrong the day someone is granted access to two.
