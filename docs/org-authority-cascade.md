# Organization authority: containment cascade

**Related record:** `org-collaborator-gap.md` defines the remaining
organization-administrator provisioning gap. The authority model itself is
settled:

> Organization admins get access to **all events in the organizations they administer**. They do **not** get speaker or reviewer flows.

This note verifies that the model is implementable safely, gives the change, and lists the four decisions that remain.

---

## 1. Why "cascade" and "no cascade" are both correct

The two words attach to different layers, which is why the docs read as contradictory.

- **Persona layer** — *no cascade.* Organizer authority must never open speaker or reviewer capabilities.
- **Resource layer** — *cascade.* Authority over an organization confers authority over the events it contains.

`policy.py` already implements the first, and the separation is structural rather than incidental:

```python
if actor.active_persona is Persona.ORGANIZER:
    if permission not in ORGANIZER_PERMISSIONS: return ...("permission_not_granted")
    if not _resource_authority(actor, permission, context): return ...("resource_access_required")

if actor.active_persona is Persona.REVIEWER:
    ...  roles = actor.event_roles.get((org, event)); Role.EVALUATOR in roles and context.evaluator_assigned

if actor.active_persona is Persona.SPEAKER:
    ...  roles = actor.event_roles.get((org, event)); context.resource_owner_user_id == actor.user_id
```

**`_resource_authority` is called from the organizer branch and nowhere else.** The reviewer branch gates on `event_roles` + `evaluator_assigned`; the speaker branch on `event_roles` + `resource_owner_user_id`. Neither consults grants or ownership at all.

Consequence: **a containment cascade inside `_resource_authority` is confined to `ORGANIZER_PERMISSIONS` by construction.** It cannot leak speaker or reviewer flows even in principle — not by policy discipline, but because those branches never reach the function being changed. `EVALUATION_SAVE`, `EVALUATION_OWN_READ`, and all seven `SPEAKER_*_OWN` permissions are absent from `ORGANIZER_PERMISSIONS`, so the organizer branch rejects them at the first line regardless of any resource fact.

A person who is both an org admin and a speaker at one of their own events still gets speaker flows — by switching active persona, through `event_roles`. That path is untouched.

---

## 2. The change

Today the lookup is flat — it checks only `authorization_resource_id` (`= resource_id or event_id or organization_id`):

```python
def _resource_authority(actor, permission, context) -> bool:
    resource_id = context.authorization_resource_id
    if resource_id in actor.owned_resource_ids:
        return True
    grants = actor.resource_grants.get(resource_id, frozenset())
    if permission is Permission.RESOURCE_ACCESS_MANAGE:
        return ResourceGrant.MANAGE in grants
    return bool(grants & {ResourceGrant.EDIT, ResourceGrant.MANAGE})
```

Replace with a walk over the containment chain — exact resource, then the containers that confer authority over it:

```python
def _authority_chain(context: ResourceContext) -> tuple[str, ...]:
    """Exact resource first, then each container that confers authority over it."""
    links: list[str] = []
    if context.resource_id:
        links.append(context.resource_id)
    if context.event_id:
        links.append(context.event_id)
    links.append(context.organization_id)
    return tuple(links)


def _resource_authority(actor, permission, context) -> bool:
    required = (
        {ResourceGrant.MANAGE}
        if permission is Permission.RESOURCE_ACCESS_MANAGE
        else {ResourceGrant.EDIT, ResourceGrant.MANAGE}
    )
    for link in _authority_chain(context):
        if link in actor.owned_resource_ids:
            return True
        if actor.resource_grants.get(link, frozenset()) & required:
            return True
    return False
```

Direction matters: authority flows **downward** only. An event grant still confers nothing at the organization level, so `docs/rbac-redesign-handoff.md:97` ("Event-admin membership never grants organization-wide administration") stays true, as does the org-admin escalation guard at `access.py:3174` — that guard builds an **org-only** context (`ResourceContext(event["organization_id"])`, no `event_id`), so its chain has one link and an event-level manager still cannot mint org admins.

---

## 3. Safety audit — the one question this rests on

Because authority now flows down from `context.organization_id`, any route that took `organization_id` from the **caller** while taking `event_id`/`resource_id` from elsewhere would become a cross-tenant escalation. Today that mistake is masked by the flat lookup.

I audited all **71** `ResourceContext(...)` construction sites plus every indirect builder (`_managed_event`, `_event_scope`, `_admin_event`, `_speaker_for_event`, `context_for_event`, `_organization_access_control_context`, `_speaker_profile_page`, `_owned_co_speaker_context`, `_create/_update_agenda_resource`, `_save_item`).

**Result: zero such sites.** Every context is one of two shapes:

- **org-only** — `ResourceContext(organization_id)` where the organization *is* the target (`access.py:1264, 2308, 2672, 3608`; `competition/router.py:427`; `console/router.py:115`). Chain length 1; the cascade is a no-op.
- **event/resource-scoped** — `organization_id` is read from the target's own row: `events.organization_id`, `submissions.organization_id`, `evaluation_rounds.organization_id`, `people.organization_id`, `call_for_speaker_forms.organization_id`. Never caller-supplied.

The only sub-resource site (`scheduling/router.py:822`, label PATCH) proves its triple consistent first — the label is fetched with the composite key `(organization_id, event_id, label_id)` at `:800-805` and 404s on mismatch before `LABEL_MANAGE` is evaluated. A label from event A with event B in the path 404s today and still 404s after.

The data layer independently re-binds the tenant on every organizer route spot-checked across all six capabilities — a malformed context could at worst cause a denial, never a cross-tenant read.

**Verdict: no site must be fixed before implementing the cascade.**

One latent item to clean up in the same change: `platform/auth/d1.py:171-174` accepts `resource_id` and silently discards it. It is currently unreferenced, but under a cascade it would upgrade a sub-resource check into an event-scoped one. Delete it or pass the argument through.

---

## 4. Four decisions

### 4a. Should the `edit` tier cascade? — **recommend no**

`policy.py:68` accepts `EDIT|MANAGE` for every organizer permission. With a flat chain-any cascade, an organization **`edit`** grantee would gain, on *every event in the org*: `COMMUNICATION_SEND` (mass-email every speaker in the tenant), `SUBMISSION_MANAGE` (including `record_submission_decision` — org-wide accept/reject), `EVALUATION_RESULTS_READ` (reviewer names, ratings, and `internal_comment`), `AGENDA_MANAGE` including publish (which fans out calendar invites), asset download grants, and `create_accelevents_token`.

That is not an "edit" tier. Make the cascade **tier-aware**: only ownership and `manage` cascade from a container; `edit` and `view` stay exact-resource.

```python
def _resource_authority(actor, permission, context) -> bool:
    exact_required = (
        {ResourceGrant.MANAGE}
        if permission is Permission.RESOURCE_ACCESS_MANAGE
        else {ResourceGrant.EDIT, ResourceGrant.MANAGE}
    )
    for index, link in enumerate(_authority_chain(context)):
        if link in actor.owned_resource_ids:
            return True
        # Only administration cascades inward from a container. An `edit`
        # grant authorizes its exact resource and nothing beneath it.
        required = exact_required if index == 0 else {ResourceGrant.MANAGE}
        if actor.resource_grants.get(link, frozenset()) & required:
            return True
    return False
```

This gives exactly the stated model — **org `manage` = organization admin = all events** — while org `edit` remains "can edit the organization record and create events," which is a coherent tier. It also resolves 4b for free.

### 4b. `RESOURCE_ACCESS_MANAGE` at the org link

With 4a, org owners and org-`manage` grantees gain `RESOURCE_ACCESS_MANAGE` on every event: per-event access grants, invitations, member revocation, and the archive/unarchive boundary. For an organization administrator this is right — it is how they recover an event whose owner left.

But it weakens three guards whose comments reserve them for "the exact owner/manager": `access.py:2871-2872` (event archive), `scheduling/router.py:559-561` (room/track archive), `scheduling/router.py:809-811` (label archive). Accept and rewrite those comments, or exempt `RESOURCE_ACCESS_MANAGE` from cascading — at the cost of the org admin losing per-event access recovery, which `ownership-recovery` + `ownership-transfer` only partly cover.

Two bounds survive either way: event-level actors still cannot escalate anyone to org admin (§2), and ownership cannot be seized — `_event_ownership_control_context` (`access.py:3948-3977`) requires literal ownership, and `_write_event_grant` refuses to touch the event owner.

### 4c. Does the chain stop at an archived event?

Nothing currently stops it. Most context builders already filter `status != 'archived'` (`_event_scope`, `_admin_event`, `context_for_event`, `competition._managed_event`, `cfp` workspace/publish/list, `evaluation._event_organization_id`), so those routes are unreachable for archived events regardless. But these are reachable and unfiltered: `get_event` (`access.py:1770`, deliberate — "archived events stay readable"), `update_event` (`:2846`, needed to un-archive), `upload_event_logo`/`cover` (`:2426`, `:2521`), `duplicate_event` (`:1838`), `_managed_event(include_archived=True)` for all event access-grant routes, **all of `evaluation/router.py`** (rounds key off `evaluation_rounds`, never joining `events.status`), and `cfp/router.py:611`.

Either accept it and document those as intentionally reachable, or add `event_status` to `ResourceContext` and terminate the chain. Accepting is defensible — org admins are exactly who needs to reach an archived event — but it should be a stated choice.

### 4d. Confidentiality within the tenant

`EVALUATION_RESULTS_READ` under the cascade means every org admin reads reviewer deliberations — `evaluator_name`, `rating`, `recommendation`, `internal_comment` (`evaluation/router.py:1823-1852`) — for every event in the org. Within-tenant, not cross-tenant, and arguably correct for an org admin. State it explicitly rather than discovering it.

**Not a concern:** `SUBMISSION_READ_FOR_EVALUATION` is in both the organizer and reviewer sets, but its single consumer (`GET /api/v1/evaluator/assignments`) binds `a.evaluator_user_id = <caller>` *before* the permission check (`evaluation/router.py:1241`). The permission is a per-row re-assertion, not the selector — an org admin with no assignments still gets an empty list. Blind-review masking and conflict declaration are reviewer-facing and unaffected.

---

## 5. Implementation checklist

1. Implement the tier-aware chain in `policy.py` (4a).
2. Settle 4b and 4c; update the three "exact owner/manager" comments accordingly.
3. **Fix three Python-side duplicates of the flat check**, or the UI will disagree with the policy — these become *stricter* than the server, so org admins get 404s and wrong `can_manage` flags rather than extra access:
   - `scheduling/router.py:208-218` — `_can_manage_resource` / `_can_manage_event` (drives the `can_manage` flag on labels)
   - `competition/router.py:586-600` — pre-filter in `_speaker_profile_page`; org-only grantees 404 despite policy allowing `SPEAKER_MANAGE`
   - `access.py:1303-1317` — `_has_event_access_in_organization`, gates `list_events` and `organization_metrics`
4. Delete or fix `platform/auth/d1.py:171-174`.
5. Tests to invert or rewrite:
   - `tests/security/test_authorization.py:125` `test_organization_ownership_does_not_cascade_to_event` — direct contradiction
   - `test_authorization.py:83-88` `test_label_management_requires_the_exact_label_resource` — assertion inverts
   - `tests/security/test_resource_control_plane.py:200-204`, `:216-221`, `:418-422` — three 404s become 200s
   - `tests/release_readiness/test_access_recovery_ui.py:46` — plus the copy it asserts, `static/account.js:210`
6. **Add** the negative test that pins the root: an org-`manage` grantee in org B must still get 404 on an event in org A. Nothing covers this today, and it is the invariant the cascade depends on.
7. Docs — `docs/resource-ownership-schema.sql:132-135` is the sharpest, since it ships in the baseline migration text and says grants "never cascade to child resources," naming this exact example. Then `docs/product-status.md:208-212` (three claims), and add tier + archive rules to `docs/platform-decisions.md:15` and `docs/requirements.md:271` — both of which already describe the cascade and become accurate.

---

## 6. What this does *not* fix

The cascade makes an organization administrator useful once they exist. It
does not provision one. Organization `view` and `edit` grants and the
event-administrator model are now retired; the independent remaining gap is an
organization-scoped invitation workflow. `org-collaborator-gap.md` owns that
contract.
