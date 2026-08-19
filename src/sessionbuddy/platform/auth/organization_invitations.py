"""Organization-scoped invitations: identity proof is separate from access consent."""

# SQL interpolation below contains only fixed, reviewed predicates; all data is bound.
# ruff: noqa: S608

import hashlib
import json
from html import escape
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sessionbuddy.communications.queue_publish import publish_committed_messages
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch, IdempotencyRecord
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit

from .http import authenticate_request, browser_request_is_same_origin, database, guard_mutation
from .organization_grants import append_organization_manage_grant
from .session_factory import (
    confirm_session_established,
    establish_session_with_current_authorization_version,
    revoke_session,
    set_session_cookie,
)
from .tokens import generate_token, hash_token, normalize_email

router = APIRouter(tags=["organization invitations"])


@router.get("/organization-admin-invitations", include_in_schema=False)
async def invitation_page():
    from .access import _asset

    return Response(
        _asset("organization_invitation.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/organization-admin-invitations/assets/invitation.js", include_in_schema=False)
async def invitation_script():
    from .access import _asset

    return Response(_asset("organization_invitation.js"), media_type="text/javascript")


@router.get("/account/assets/organization-invitations.js", include_in_schema=False)
async def account_invitation_script():
    from .access import _asset

    return Response(_asset("account_organization_invitations.js"), media_type="text/javascript")


LIFETIME_MS = 72 * 60 * 60 * 1000
VERIFICATION_MS = 15 * 60 * 1000

# Used both by preflight and transaction predicates. Authority is never inferred
# from an email match, the lookup token, or a cached actor in an earlier request.
ELIGIBLE = """EXISTS(SELECT 1 FROM organizations o JOIN owned_resources r
 ON r.id=o.id AND r.resource_type='organization' JOIN users inviter
 ON inviter.id=i.invited_by_user_id
 WHERE o.id=i.organization_id AND o.status='active' AND r.status='active'
 AND inviter.status='active' AND inviter.deleted_at_ms IS NULL
 AND (r.owner_user_id=inviter.id OR EXISTS(SELECT 1 FROM resource_access_grants g
 WHERE g.resource_id=r.id AND g.user_id=inviter.id AND g.permission='manage'
 AND g.status='active')))"""
ADMIN = """EXISTS(SELECT 1 FROM owned_resources r WHERE r.id=i.organization_id
 AND r.resource_type='organization' AND r.status='active'
 AND (r.owner_user_id=?3 OR EXISTS(SELECT 1 FROM resource_access_grants g
 WHERE g.resource_id=r.id AND g.user_id=?3 AND g.permission='manage' AND g.status='active')))"""
SELECT = f"""SELECT i.*,o.name AS organization_name,{ELIGIBLE} AS eligible
 FROM organization_admin_invitations i JOIN organizations o ON o.id=i.organization_id"""


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=200)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        from .access import _validated_email

        return _validated_email(value)


class Lookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=512)


def fail(cause: str, status=409):
    messages = {
        "invitation_expired": "This invitation has expired. Ask an admin to invite you again.",
        "invitation_revoked": "This invitation was revoked.",
        "invitation_declined": "This invitation was declined.",
        "invitation_already_accepted": "This invitation has already been accepted.",
        "inviter_authority_lost": (
            "The admin who invited you no longer manages this organization. "
            "Ask a current admin to reissue the invitation."
        ),
        "already_admin": "This person is already an owner or admin.",
        "invitation_link_invalid": "This invitation link is no longer valid.",
    }
    raise HTTPException(
        status_code=status,
        detail={
            "metadata": {"cause": cause},
            "message": messages.get(
                cause, "The invitation could not be updated. Refresh and try again."
            ),
        },
    )


def state(row, now):
    if row["declined_at_ms"] is not None:
        return "declined"
    return (
        "expired" if row["status"] == "pending" and row["expires_at_ms"] <= now else row["status"]
    )


def pending(row, now, *, authority=True):
    current = state(row, now)
    if current != "pending":
        fail("invitation_already_accepted" if current == "accepted" else f"invitation_{current}")
    if authority and not row["eligible"]:
        fail("inviter_authority_lost")


async def load(db, iid, org=None):
    row = row_mapping(await db.prepare(SELECT + " WHERE i.id=?1").bind(iid).first())
    if row is None or (org is not None and row["organization_id"] != org):
        raise HTTPException(status_code=404)
    return row


def guard(batch, db, iid, now):
    batch.add_statement(
        db.prepare("""INSERT INTO organization_admin_invitation_write_guards
     (id,invitation_id,applied_changes,created_at_ms) VALUES(?1,?2,changes(),?3)""").bind(
            new_id(), iid, now
        )
    )


def guarded_failure(exc):
    # Do not turn arbitrary DB failures into a successful duplicate response.
    cause = exc.__cause__
    return cause is not None and "org_admin_invitation_write_guard" in str(cause)


def consume(batch, db, iid, now):
    batch.add_statement(
        db.prepare("""UPDATE authentication_challenges SET consumed_at_ms=?2
     WHERE organization_admin_invitation_id=?1 AND consumed_at_ms IS NULL""").bind(iid, now)
    )


def audit(batch, request, row, action, actor, now, **metadata):
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=actor,
            organization_id=row["organization_id"],
            action=f"organization.admin_invitation.{action}",
            target_type="organization_admin_invitation",
            target_id=row["id"],
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata=metadata,
        )
    )


async def limited(request, subject, name="issue"):
    await enforce_rate_limit(
        request,
        binding_name="AUTH_RATE_LIMITER",
        policy=RateLimitPolicy(f"org.admin_invitation.{name}", limit=20, window_seconds=60),
        subject=subject,
    )


def append_email(batch, request, row, raw, now, *, challenge_id=None):
    base = str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "")).rstrip("/")
    parsed = urlparse(base)
    local = (
        getattr(request.scope.get("env"), "APP_ENV", None) == "local"
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
    )
    if (
        not parsed.netloc
        or parsed.username
        or parsed.password
        or (parsed.scheme != "https" and not local)
    ):
        fail("invitation_delivery_unavailable", 503)
    path = "/auth/verify" if challenge_id else "/organization-admin-invitations"
    key = (
        f"organization-admin-invitation-verify:{row['id']}:{challenge_id}"
        if challenge_id
        else f"organization-admin-invitation:{row['id']}:{new_id()}"
    )
    label = "Verify your email" if challenge_id else "View invitation"
    subject = (
        "Verify your email for SessionBuddy"
        if challenge_id
        else f"You are invited to administer {row['organization_name']} on SessionBuddy"
    )
    message = new_id()
    batch.add_statement(
        database(request)
        .prepare("""INSERT INTO communication_messages
      (id,organization_id,recipient_email,subject,html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
      VALUES(?1,?2,?3,?4,?5,?6,'queued',?7,?7)""")
        .bind(
            message,
            row["organization_id"],
            row["email"],
            subject,
            f'<p><a href="{escape(base + path + "#token=" + raw, quote=True)}">{label}</a></p>',
            key,
            now,
        )
    )
    return message


DELIVERIES = """WITH requested AS (
 SELECT json_extract(value,'$.id') AS id,
        json_extract(value,'$.organization_id') AS organization_id FROM json_each(?1)
)
SELECT requested.id AS invitation_id,m.status,m.last_error_code,m.updated_at_ms
FROM requested LEFT JOIN communication_messages m ON m.id=(
 SELECT message.id FROM communication_messages message
 WHERE message.organization_id=requested.organization_id AND message.event_id IS NULL
 AND message.deterministic_key >= 'organization-admin-invitation:' || requested.id || ':'
 AND message.deterministic_key < 'organization-admin-invitation:' || requested.id || ';'
 ORDER BY message.queued_at_ms DESC,message.id DESC LIMIT 1
)"""


def invitation_view(row, delivery, now):
    reason = None
    if delivery and delivery["last_error_code"]:
        reasons = {
            "provider_unconfigured": "provider_unconfigured",
            "provider_unavailable": "provider_unavailable",
            "rejected": "rejected",
            "exhausted": "exhausted",
        }
        reason = reasons.get(str(delivery["last_error_code"]), "unknown")
    return {
        "id": row["id"],
        "organization_id": row["organization_id"],
        "organization_name": row["organization_name"],
        "email": row["email"],
        "status": state(row, now),
        "expires_at_ms": row["expires_at_ms"],
        "needs_reissue": state(row, now) == "pending" and not bool(row["eligible"]),
        "delivery": {
            "status": delivery["status"] if delivery and delivery["status"] else "none",
            "reason": reason,
            "updated_at_ms": delivery["updated_at_ms"] if delivery else None,
        },
    }


async def views(db, rows, now):
    if not rows:
        return []
    # One D1 round trip for the whole page. The prefix range uses the existing
    # tenant/key indexes and excludes verification emails. Keep the
    # tenant in every lookup; JSON binding avoids D1's positional-parameter cap.
    deliveries = result_rows(
        await db.prepare(DELIVERIES)
        .bind(
            json.dumps(
                [{"id": row["id"], "organization_id": row["organization_id"]} for row in rows]
            )
        )
        .all()
    )
    by_id = {delivery["invitation_id"]: delivery for delivery in deliveries}
    return [invitation_view(row, by_id.get(row["id"]), now) for row in rows]


async def view(db, row, now):
    return (await views(db, [row], now))[0]


async def idempotency(db, actor, route, key, payload, now, org=None):
    if not isinstance(key, str) or not 16 <= len(key) <= 255:
        raise HTTPException(400, "Idempotency-Key must contain between 16 and 255 characters")
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).digest()
    record = IdempotencyRecord(
        principal_key=actor,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        organization_id=org,
        expires_at_ms=now + 7 * 86400000,
    )
    old = row_mapping(
        await db.prepare("""SELECT i.request_fingerprint,r.response_json FROM idempotency_records i
      LEFT JOIN organization_admin_invitation_responses r ON r.idempotency_id=i.id
      WHERE i.principal_key=?1 AND i.route_key=?2 AND i.idempotency_key_hash=?3""")
        .bind(actor, route, record.key_hash)
        .first()
    )
    if old:
        if bytes(to_python(old["request_fingerprint"])) != fingerprint or not old["response_json"]:
            fail("idempotency_conflict")
        return record, json.loads(old["response_json"])
    return record, None


def complete(batch, db, record, result, now):
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="organization_admin_invitation",
        resource_id=result["id"],
        completed_at_ms=now,
    )
    batch.add_statement(
        db.prepare("""INSERT INTO organization_admin_invitation_responses
       (idempotency_id,response_json) VALUES(?1,?2)""").bind(record.id, json.dumps(result))
    )


async def manager(request, org, mutation=True):
    from .access import _organization_access_control_context

    return await _organization_access_control_context(request, org, mutation=mutation)


@router.get("/api/v1/admin/organizations/{organization_id}/admin-invitations")
async def list_admin_invitations(organization_id: str, request: Request):
    db, _, _ = await manager(request, organization_id, False)
    rows = result_rows(
        await db.prepare(
            SELECT + " WHERE i.organization_id=?1 ORDER BY i.created_at_ms DESC,i.id DESC LIMIT 100"
        )
        .bind(organization_id)
        .all()
    )
    return {"data": await views(db, rows, utc_now_ms())}


@router.post("/api/v1/admin/organizations/{organization_id}/admin-invitations")
async def create_admin_invitation(
    organization_id: str,
    body: InvitationCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None),
):
    db, _, auth = await manager(request, organization_id)
    now, normalized = utc_now_ms(), normalize_email(body.email)
    record, replay = await idempotency(
        db,
        auth.actor.user_id,
        "org.invite",
        idempotency_key,
        [organization_id, normalized, body.display_name],
        now,
        organization_id,
    )
    if replay is not None:
        return replay
    await limited(request, auth.actor.user_id)
    existing = (
        await db.prepare("""SELECT u.id FROM users u JOIN owned_resources r ON r.id=?1
       WHERE u.normalized_email=?2 AND u.status='active' AND (r.owner_user_id=u.id OR EXISTS(
       SELECT 1 FROM resource_access_grants g WHERE g.resource_id=r.id AND g.user_id=u.id
       AND g.permission='manage' AND g.status='active'))""")
        .bind(organization_id, normalized)
        .first()
    )
    if existing:
        fail("already_admin")
    old = row_mapping(
        await db.prepare(
            SELECT + " WHERE i.organization_id=?1 AND i.normalized_email=?2 AND i.status='pending'"
        )
        .bind(organization_id, normalized)
        .first()
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    if old and state(old, now) == "pending":
        result = await view(db, old, now)
        result["cause"] = "duplicate_pending"
        complete(batch, db, record, result, now)
        await batch.execute()
        return result
    if old:
        batch.add_statement(
            db.prepare("""UPDATE organization_admin_invitations SET
          status='expired',expired_at_ms=?2,
         updated_at_ms=?2,version=version+1 WHERE id=?1 AND status='pending' AND
          expires_at_ms<=?2""").bind(
                old["id"], now
            )
        )
        guard(batch, db, old["id"], now)
        consume(batch, db, old["id"], now)
    iid, raw = new_id(), generate_token()
    org_name = (
        await db.prepare("SELECT name FROM organizations WHERE id=?1 AND status='active'")
        .bind(organization_id)
        .first("name")
    )
    if org_name is None:
        raise HTTPException(404)
    row = {
        "id": iid,
        "organization_id": organization_id,
        "organization_name": org_name,
        "email": body.email,
        "normalized_email": normalized,
        "status": "pending",
        "expires_at_ms": now + LIFETIME_MS,
        "declined_at_ms": None,
        "eligible": True,
    }
    batch.add_statement(
        db.prepare("""INSERT INTO organization_admin_invitations
       (id,organization_id,email,normalized_email,display_name,invited_by_user_id,token_hash,
        expires_at_ms,created_at_ms,updated_at_ms) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?9)""").bind(
            iid,
            organization_id,
            body.email,
            normalized,
            body.display_name,
            auth.actor.user_id,
            hash_token(raw),
            now + LIFETIME_MS,
            now,
        )
    )
    batch.add_statement(
        db.prepare(f"""UPDATE organization_admin_invitations AS i
      SET updated_at_ms=?2 WHERE id=?1 AND {ELIGIBLE}""").bind(iid, now)
    )
    guard(batch, db, iid, now)
    message = append_email(batch, request, row, raw, now)
    audit(batch, request, row, "create", auth.actor.user_id, now)
    result = await view(db, row, now)
    result["delivery"] = {"status": "queued", "reason": None, "updated_at_ms": now}
    complete(batch, db, record, result, now)
    try:
        await batch.execute()
    except PersistenceError as exc:
        # A racing creator may have committed first. Re-enter only when that
        # specific pending recipient now exists; unrelated DB errors stay errors.
        if "UNIQUE constraint failed" not in str(exc.__cause__):
            raise
        winner = db.prepare("""SELECT id FROM organization_admin_invitations
          WHERE organization_id=?1 AND normalized_email=?2 AND status='pending' AND
          expires_at_ms>?3""")
        if not await winner.bind(organization_id, normalized, now).first():
            raise
        return await create_admin_invitation(organization_id, body, request, idempotency_key)
    await publish_committed_messages(request, [message])
    return result


async def admin_action(org, iid, action, request, key):
    db, _, auth = await manager(request, org)
    now = utc_now_ms()
    row = await load(db, iid, org)
    record, replay = await idempotency(
        db, auth.actor.user_id, "org.invitation.action", key, [org, iid, action], now, org
    )
    if replay is not None:
        return replay
    if action == "reissue":
        if row["status"] != "pending":
            pending(row, now, authority=False)
    else:
        pending(row, now, authority=action == "resend")
    await limited(request, auth.actor.user_id)
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    raw, message = generate_token(), None
    if action == "resend":
        batch.add_statement(
            db.prepare(f"""UPDATE organization_admin_invitations AS i
         SET token_hash=?3,updated_at_ms=?2,version=version+1 WHERE id=?1 AND status='pending'
         AND expires_at_ms>?2 AND {ELIGIBLE}""").bind(iid, now, hash_token(raw))
        )
    else:
        batch.add_statement(
            db.prepare("""UPDATE organization_admin_invitations SET status='revoked',
          revoked_at_ms=?2,updated_at_ms=?2,version=version+1 WHERE id=?1 AND
          status='pending'""").bind(
                iid, now
            )
        )
    guard(batch, db, iid, now)
    consume(batch, db, iid, now)
    if action == "reissue":
        replacement = new_id()
        batch.add_statement(
            db.prepare("""INSERT INTO organization_admin_invitations
         (id,organization_id,email,normalized_email,display_name,invited_by_user_id,token_hash,
          expires_at_ms,created_at_ms,updated_at_ms)
         SELECT ?2,organization_id,email,normalized_email,display_name,?3,?4,?5,?6,?6
         FROM organization_admin_invitations WHERE id=?1 AND status='revoked'""").bind(
                iid, replacement, auth.actor.user_id, hash_token(raw), now + LIFETIME_MS, now
            )
        )
        guard(batch, db, replacement, now)
        batch.add_statement(
            db.prepare(
                "UPDATE organization_admin_invitations SET superseded_by_id=?2 "
                "WHERE id=?1 AND status='revoked'"
            ).bind(iid, replacement)
        )
        guard(batch, db, iid, now)
        batch.add_statement(
            db.prepare(f"""UPDATE organization_admin_invitations AS i
            SET updated_at_ms=?2 WHERE id=?1 AND {ELIGIBLE}""").bind(replacement, now)
        )
        guard(batch, db, replacement, now)
        row = {**row, "id": replacement, "expires_at_ms": now + LIFETIME_MS, "eligible": True}
    if action != "revoke":
        message = append_email(batch, request, row, raw, now)
    else:
        row = {**row, "status": "revoked"}
    audit(
        batch,
        request,
        row,
        action,
        auth.actor.user_id,
        now,
        **({"superseded_invitation_id": iid} if action == "reissue" else {}),
    )
    result = await view(db, row, now)
    if message:
        result["delivery"] = {"status": "queued", "reason": None, "updated_at_ms": now}
    complete(batch, db, record, result, now)
    try:
        await batch.execute()
    except PersistenceError as exc:
        if not guarded_failure(exc):
            raise
        pending(await load(db, iid, org), now, authority=action == "resend")
        fail("invitation_changed")
    if message:
        await publish_committed_messages(request, [message])
    return result


@router.post(
    "/api/v1/admin/organizations/{organization_id}/admin-invitations/{invitation_id}/resend"
)
async def resend(
    organization_id: str,
    invitation_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None),
):
    return await admin_action(organization_id, invitation_id, "resend", request, idempotency_key)


@router.post(
    "/api/v1/admin/organizations/{organization_id}/admin-invitations/{invitation_id}/reissue"
)
async def reissue(
    organization_id: str,
    invitation_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None),
):
    return await admin_action(organization_id, invitation_id, "reissue", request, idempotency_key)


@router.delete("/api/v1/admin/organizations/{organization_id}/admin-invitations/{invitation_id}")
async def revoke(
    organization_id: str,
    invitation_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None),
):
    return await admin_action(organization_id, invitation_id, "revoke", request, idempotency_key)


async def lookup(request, token):
    if not browser_request_is_same_origin(request):
        raise HTTPException(403)
    source = request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "unknown"
    )
    await limited(request, source, "source")
    await limited(request, token, "resolve")
    row = row_mapping(
        await database(request)
        .prepare(SELECT + " WHERE i.token_hash=?1")
        .bind(hash_token(token))
        .first()
    )
    if not row:
        fail("invitation_link_invalid", 404)
    return row


@router.post("/api/v1/organization-admin-invitations/resolve")
async def resolve(body: Lookup, request: Request):
    row = await lookup(request, body.token)
    matches = None
    try:
        auth = await authenticate_request(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
    else:
        email = (
            await database(request)
            .prepare("SELECT normalized_email FROM users WHERE id=?1")
            .bind(auth.actor.user_id)
            .first("normalized_email")
        )
        matches = email == row["normalized_email"]
    if matches is False:
        # A valid lookup handle does not disclose invitation details to another account.
        return {"identity_matches": False}
    return {
        "id": row["id"],
        "organization_name": row["organization_name"],
        "expires_at_ms": row["expires_at_ms"],
        "status": state(row, utc_now_ms()),
        "needs_reissue": not bool(row["eligible"]),
        "identity_matches": matches,
    }


async def live_challenge(db, iid, now):
    return (
        await db.prepare("""SELECT id FROM authentication_challenges WHERE
          organization_admin_invitation_id=?1
      AND consumed_at_ms IS NULL AND expires_at_ms>?2 LIMIT 1""")
        .bind(iid, now)
        .first()
    )


@router.post("/api/v1/organization-admin-invitations/verification", status_code=202)
async def verification(body: Lookup, request: Request):
    row = await lookup(request, body.token)
    db, now = database(request), utc_now_ms()
    pending(row, now)
    if await live_challenge(db, row["id"], now):
        return {"status": "accepted"}
    count = (
        await db.prepare("""SELECT COUNT(*) AS n FROM authentication_challenges
       WHERE organization_admin_invitation_id=?1 AND created_at_ms>?2""")
        .bind(row["id"], now - 3600000)
        .first("n")
    )
    if count >= 3:
        fail("verification_rate_limited", 429)
    cid, raw = new_id(), generate_token()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(f"""INSERT INTO authentication_challenges
       (id,normalized_email,token_hash,purpose,provisioning_context,redirect_path,expires_at_ms,
        created_at_ms,organization_id,organization_admin_invitation_id)
       SELECT ?1,i.normalized_email,?2,'verify_email','invitation',?3,MIN(?4,i.expires_at_ms),?5,
              i.organization_id,i.id FROM organization_admin_invitations i
       WHERE i.id=?6 AND i.token_hash=?7 AND i.status='pending' AND
          i.expires_at_ms>?5 AND {ELIGIBLE}
       AND NOT EXISTS(SELECT 1 FROM authentication_challenges c WHERE
          c.organization_admin_invitation_id=i.id
           AND c.consumed_at_ms IS NULL AND c.expires_at_ms>?5)""").bind(
            cid,
            hash_token(raw),
            "/organization-admin-invitations?invitation=" + row["id"],
            now + VERIFICATION_MS,
            now,
            row["id"],
            hash_token(body.token),
        )
    )
    guard(batch, db, row["id"], now)
    message = append_email(batch, request, row, raw, now, challenge_id=cid)
    try:
        await batch.execute()
    except PersistenceError as exc:
        if not guarded_failure(exc):
            raise
        fresh = await lookup(request, body.token)
        pending(fresh, now)
        if await live_challenge(db, row["id"], now):
            return {"status": "accepted"}
        fail("invitation_changed")
    await publish_committed_messages(request, [message])
    return {"status": "accepted"}


async def account_action(iid, request, response, key, action):
    auth = await authenticate_request(request)
    guard_mutation(request, auth.session_id)
    db, now = database(request), utc_now_ms()
    row = await load(db, iid)
    user = row_mapping(
        await db.prepare("SELECT normalized_email,status FROM users WHERE id=?1")
        .bind(auth.actor.user_id)
        .first()
    )
    if user is None or user["normalized_email"] != row["normalized_email"]:
        fail("invitation_identity_mismatch", 404)
    if user["status"] != "active":
        fail("account_unavailable", 404)
    record, replay = await idempotency(
        db,
        auth.actor.user_id,
        "org.invitation.response",
        key,
        [iid, action],
        now,
        row["organization_id"],
    )
    if replay is not None:
        return replay
    pending(row, now, authority=action == "accept")
    for _ in range(2):
        already = (
            bool(
                await db.prepare(
                    "SELECT i.id FROM organization_admin_invitations i WHERE i.id=?1 AND " + ADMIN
                )
                .bind(iid, now, auth.actor.user_id)
                .first()
            )
            if action == "accept"
            else False
        )
        batch = CommandBatch(db)
        batch.begin_idempotency(record, now)
        eligibility = f" AND {ELIGIBLE}" if action == "accept" else ""
        if action == "accept":
            eligibility += f" AND {ADMIN}" if already else f" AND NOT {ADMIN}"
        batch.add_statement(
            db.prepare(f"""UPDATE organization_admin_invitations AS i
          SET status=?4,accepted_at_ms=CASE WHEN ?4='accepted' THEN ?2 END,
          accepted_by_user_id=CASE WHEN ?4='accepted' THEN ?3 END,
          revoked_at_ms=CASE WHEN ?4='revoked' THEN ?2 END,
          declined_at_ms=CASE WHEN ?4='revoked' THEN ?2 END,updated_at_ms=?2,version=version+1
          WHERE id=?1 AND status='pending' AND expires_at_ms>?2
          AND EXISTS(SELECT 1 FROM users u WHERE u.id=?3 AND u.status='active'
            AND u.deleted_at_ms IS NULL
            AND u.normalized_email=i.normalized_email) {eligibility}""").bind(
                iid, now, auth.actor.user_id, "accepted" if action == "accept" else "revoked"
            )
        )
        guard(batch, db, iid, now)
        established = None
        if action == "accept" and not already:
            append_organization_manage_grant(
                batch,
                db,
                organization_id=row["organization_id"],
                user_id=auth.actor.user_id,
                granted_by_user_id=row["invited_by_user_id"],
                now=now,
            )
        consume(batch, db, iid, now)
        audit(batch, request, row, action, auth.actor.user_id, now, already_admin=already)
        result = {
            "id": iid,
            "status": "accepted" if action == "accept" else "declined",
            "workspace_path": "/admin" if action == "accept" else "/account",
            "already_admin": already,
        }
        complete(batch, db, record, result, now)
        if action == "accept":
            # A concurrent grant can invalidate the old cookie even when our
            # branch needs no grant/version write. Always return a current session.
            established = establish_session_with_current_authorization_version(
                batch=batch,
                db=db,
                request=request,
                user_id=auth.actor.user_id,
                role="organizer",
                now_ms=now,
            )
            guard(batch, db, iid, now)
            revoke_session(batch, db, auth.session_id, "persona_replaced", now)
        try:
            results = await batch.execute()
        except PersistenceError as exc:
            if not guarded_failure(exc):
                raise
            pending(await load(db, iid), now, authority=action == "accept")
            if action == "accept":
                continue
            fail("invitation_changed")
        if established:
            await confirm_session_established(results, established, db)
            set_session_cookie(response, request, established.session_token)
        return result
    fail("invitation_changed")


@router.post("/api/v1/account/organization-invitations/{invitation_id}/{action}")
async def respond(
    invitation_id: str,
    action: Literal["accept", "decline"],
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None),
):
    return await account_action(invitation_id, request, response, idempotency_key, action)
