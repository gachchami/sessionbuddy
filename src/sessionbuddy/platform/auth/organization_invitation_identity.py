"""Non-consuming confirmation and atomic identity-only invitation registration."""

# Only fixed predicates are interpolated into SQL; user inputs are bound parameters.
# ruff: noqa: S608

from fastapi import HTTPException, Response
from fastapi.responses import RedirectResponse

from sessionbuddy.platform.db.commands import CommandBatch
from sessionbuddy.platform.db.d1 import PersistenceError, row_mapping
from sessionbuddy.platform.db.types import new_id, utc_now_ms

from .http import database, secret
from .organization_invitations import ELIGIBLE, guard, load, pending
from .passwords import PasswordPolicyError, hash_password
from .session_factory import (
    confirm_session_established,
    establish_identity_session,
    set_session_cookie,
)
from .tokens import hash_token


async def verify_organization_identity(request, token, values):
    from .access import SubmissionRegistration, _magic_link_page

    if len(token) < 32:
        return None
    db, now = database(request), utc_now_ms()
    challenge = row_mapping(
        await db.prepare("""SELECT * FROM authentication_challenges
      WHERE token_hash=?1 AND organization_admin_invitation_id IS NOT NULL""")
        .bind(hash_token(token))
        .first()
    )
    if challenge is None:
        return None
    row = await load(db, challenge["organization_admin_invitation_id"])
    pending(row, now)
    if challenge["consumed_at_ms"] is not None or challenge["expires_at_ms"] <= now:
        raise HTTPException(404, detail={"metadata": {"cause": "verification_expired"}})
    user = row_mapping(
        await db.prepare("SELECT id,status,deleted_at_ms FROM users WHERE normalized_email=?1")
        .bind(challenge["normalized_email"])
        .first()
    )
    if user and (user["status"] != "active" or user["deleted_at_ms"] is not None):
        raise HTTPException(404, detail={"metadata": {"cause": "account_unavailable"}})
    registration = None
    error = None
    verifier = None
    if not user and values.get("first_name"):
        try:
            registration = SubmissionRegistration(
                **{
                    k: values.get(k, "")
                    for k in (
                        "first_name",
                        "last_name",
                        "password",
                        "password_confirmation",
                        "job_title",
                        "company",
                    )
                }
            )
            verifier = hash_password(registration.password, secret(request, "PASSWORD_PEPPER"))
        except (ValueError, PasswordPolicyError):
            registration = None
            error = "Enter your name and matching passwords of at least 15 characters."
    if (not user and registration is None) or (
        user and values.get("organization_confirm") != "yes"
    ):
        page = _magic_link_page(
            token=token,
            registration_email=None if user else row["email"],
            error=error,
            values=values,
        )
        page = page.replace("Create your speaker account", "Create your account")
        page = page.replace(
            "then return to your proposal.", "then review and accept the organization invitation."
        )
        page = page.replace(
            "</form>", '<input type="hidden" name="organization_confirm" value="yes"></form>'
        )
        return Response(
            page,
            status_code=422 if error else 200,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
    uid = user["id"] if user else new_id()
    batch = CommandBatch(db)
    identity_predicate = (
        "EXISTS(SELECT 1 FROM users u WHERE u.id=?4 AND u.status='active' "
        "AND u.deleted_at_ms IS NULL "
        "AND u.normalized_email=authentication_challenges.normalized_email)"
        if user
        else "NOT EXISTS(SELECT 1 FROM users u WHERE "
        "u.normalized_email=authentication_challenges.normalized_email) AND ?4 IS NOT NULL"
    )
    batch.add_statement(
        db.prepare(f"""UPDATE authentication_challenges SET consumed_at_ms=?2
      WHERE id=?1 AND consumed_at_ms IS NULL AND expires_at_ms>?2 AND token_hash=?3
      AND {identity_predicate} AND EXISTS(SELECT 1 FROM organization_admin_invitations i
       WHERE i.id=authentication_challenges.organization_admin_invitation_id AND i.status='pending'
       AND i.expires_at_ms>?2 AND {ELIGIBLE})""").bind(challenge["id"], now, hash_token(token), uid)
    )
    guard(batch, db, row["id"], now)
    if not user:
        batch.add_statement(
            db.prepare("""INSERT INTO users
          (id,email,normalized_email,status,email_verified_at_ms,first_name,last_name,display_name,
           job_title,company,profile_completed_at_ms,created_at_ms,updated_at_ms)
          VALUES(?1,?2,?3,'active',?4,?5,?6,?7,?8,?9,?4,?4,?4)""").bind(
                uid,
                row["email"],
                row["normalized_email"],
                now,
                registration.first_name,
                registration.last_name,
                registration.first_name + " " + registration.last_name,
                registration.job_title,
                registration.company,
            )
        )
        batch.add_statement(
            db.prepare("""INSERT INTO password_credentials
          (user_id,verifier_phc,pepper_version,status,created_at_ms,updated_at_ms)
          VALUES(?1,?2,1,'active',?3,?3)""").bind(uid, verifier, now)
        )
    established = establish_identity_session(
        batch=batch, db=db, request=request, user_id=uid, now_ms=now
    )
    guard(batch, db, row["id"], now)
    try:
        results = await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(409, detail={"metadata": {"cause": "verification_changed"}}) from exc
    await confirm_session_established(results, established, db)
    response = RedirectResponse(
        "/organization-admin-invitations?invitation=" + row["id"], status_code=303
    )
    set_session_cookie(response, request, established.session_token)
    return response
