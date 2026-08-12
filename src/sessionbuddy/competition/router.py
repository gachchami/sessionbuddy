import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from sessionbuddy.console import embedded_assets
from sessionbuddy.platform.auth import authenticate_request, generate_token, hash_token
from sessionbuddy.platform.auth.http import require_document_persona, require_permission
from sessionbuddy.platform.authorization import Permission, Persona, ResourceContext, ResourceGrant
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch, IdempotencyRecord
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms

from .models import (
    AdminSessionContentRestore,
    AdminSessionContentUpdate,
    AdminSessionContentView,
    AdminSpeakerTaskView,
    AdminSpeakerUpdate,
    IntegrationTokenCreate,
    IntegrationTokenView,
    OrganizationPersonEventAssociation,
    OrganizationSpeaker,
    OrganizationSpeakerList,
    OrganizationSpeakerParticipation,
    PublicEventList,
    PublicEventSummary,
    PublicSpeaker,
    PublicSpeakerGallery,
    ResourceCreate,
    ResourceList,
    ResourceView,
    SessionContentVersionView,
    SpeakerProfilePageView,
    SpeakerTarget,
    SpeakerTargetList,
    SpeakerTaskCreate,
)

competition_router = APIRouter()


@competition_router.get(
    "/api/v1/public/events", response_model=PublicEventList, tags=["public-program"]
)
async def list_public_events(request: Request) -> PublicEventList:
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT e.id,e.name,e.starts_at_ms,e.ends_at_ms,e.time_zone,
                      COALESCE(e.location,'') AS location,e.delivery_mode,
                      (SELECT f.slug FROM call_for_speaker_forms f
                       WHERE f.organization_id=e.organization_id AND f.event_id=e.id
                         AND f.status='published'
                       ORDER BY f.published_at_ms DESC,f.id DESC LIMIT 1) AS cfp_slug,
                      EXISTS(SELECT 1 FROM schedule_revisions r
                       WHERE r.organization_id=e.organization_id AND r.event_id=e.id
                         AND r.status='published') AS schedule_published,
                      (SELECT COUNT(*) FROM event_speakers es
                       WHERE es.organization_id=e.organization_id AND es.event_id=e.id
                         AND es.selection_status='accepted'
                         AND es.status!='withdrawn') AS speaker_count
               FROM events e WHERE e.status='active'
               ORDER BY e.starts_at_ms,e.id LIMIT 100"""
        )
        .all()
    )
    return PublicEventList(data=[PublicEventSummary.model_validate(row) for row in rows])


def _blob(value: object) -> bytes:
    converted = to_python(value)
    return converted if isinstance(converted, bytes) else bytes(converted)


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


def _db(request: Request):
    db = getattr(request.scope.get("env"), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


async def _managed_event(request: Request, event_id: str, *, mutation: bool):
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT id,organization_id,name,time_zone,accent_color,logo_url,
                      cover_image_url,website_url
               FROM events WHERE id=?1 AND status!='archived' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.EVENT_MANAGE,
        ResourceContext(str(row["organization_id"]), event_id),
        mutation=mutation,
    )
    return row, auth


@competition_router.get(
    "/admin/events/{event_id}/workspace", response_class=HTMLResponse, include_in_schema=False
)
async def event_workspace_page(event_id: str, request: Request) -> HTMLResponse:
    await require_document_persona(request, Persona.ORGANIZER)
    return HTMLResponse(_asset("event_workspace.html"), headers={"Cache-Control": "no-store"})


@competition_router.get(
    "/admin/workspace/assets/workspace.js", response_class=Response, include_in_schema=False
)
async def event_workspace_js() -> Response:
    return Response(_asset("event_workspace.js"), media_type="text/javascript")


@competition_router.get(
    "/admin/events/{event_id}/speaker-content",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def speaker_content_page(event_id: str, request: Request) -> HTMLResponse:
    await require_document_persona(request, Persona.ORGANIZER)
    return HTMLResponse(_asset("speaker_content.html"), headers={"Cache-Control": "no-store"})


@competition_router.get(
    "/admin/speaker-content/assets/speaker-content.js",
    response_class=Response,
    include_in_schema=False,
)
async def speaker_content_js() -> Response:
    return Response(_asset("speaker_content.js"), media_type="text/javascript")


@competition_router.get(
    "/events/{event_id}/speakers", response_class=HTMLResponse, include_in_schema=False
)
@competition_router.get(
    "/events/{event_id}/gallery", response_class=HTMLResponse, include_in_schema=False
)
@competition_router.get(
    "/embeds/events/{event_id}/speakers", response_class=HTMLResponse, include_in_schema=False
)
@competition_router.get(
    "/embeds/events/{event_id}/gallery", response_class=HTMLResponse, include_in_schema=False
)
async def public_speaker_gallery_page(event_id: str) -> HTMLResponse:
    return HTMLResponse(
        _asset("speaker_gallery.html"), headers={"Cache-Control": "public, max-age=60"}
    )


@competition_router.get(
    "/gallery/assets/gallery.js", response_class=Response, include_in_schema=False
)
async def speaker_gallery_js() -> Response:
    return Response(_asset("speaker_gallery.js"), media_type="text/javascript")


def _resource(row) -> ResourceView:
    return ResourceView(
        id=str(row["id"]),
        event_id=str(row["event_id"]),
        title=str(row["title"]),
        slug=str(row["slug"]),
        summary=str(row["summary"]),
        body_text=str(row["body_text"]),
        embed_url=str(row["embed_url"]) if row["embed_url"] is not None else None,
        status=str(row["status"]),
        sort_order=int(row["sort_order"]),
        version=int(row["version"]),
        updated_at_ms=int(row["updated_at_ms"]),
    )


@competition_router.get(
    "/api/v1/admin/events/{event_id}/resources",
    response_model=ResourceList,
    tags=["resources"],
)
async def list_admin_resources(event_id: str, request: Request) -> ResourceList:
    event, _ = await _managed_event(request, event_id, mutation=False)
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT id,event_id,title,slug,summary,body_text,embed_url,status,sort_order,
                      version,updated_at_ms FROM event_resources
               WHERE organization_id=?1 AND event_id=?2 AND status!='archived'
               ORDER BY sort_order,title,id LIMIT 200"""
        )
        .bind(event["organization_id"], event_id)
        .all()
    )
    return ResourceList(data=[_resource(row) for row in rows])


@competition_router.post(
    "/api/v1/admin/events/{event_id}/resources",
    response_model=ResourceView,
    status_code=201,
    tags=["resources"],
)
async def create_resource(
    event_id: str,
    body: ResourceCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ResourceView:
    if idempotency_key is None or not 16 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=400)
    event, auth = await _managed_event(request, event_id, mutation=True)
    db, now, resource_id = _db(request), utc_now_ms(), new_id()
    route = "POST /api/v1/admin/events/{event_id}/resources"
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    ).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND state='completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(idempotency_key.encode()).digest())
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        resource_id = str(replay["response_resource_id"])
        row = row_mapping(
            await db.prepare(
                """SELECT id,event_id,title,slug,summary,body_text,embed_url,status,
                          sort_order,version,updated_at_ms FROM event_resources WHERE id=?1"""
            )
            .bind(resource_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=409)
        return _resource(row)
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        route_key=route,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_resources
               (id,organization_id,event_id,title,slug,summary,body_text,embed_url,status,
                sort_order,created_by_user_id,created_at_ms,updated_at_ms,published_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?12,?13)"""
        ).bind(
            resource_id,
            event["organization_id"],
            event_id,
            body.title,
            body.slug,
            body.summary,
            body.body_text,
            body.embed_url,
            body.status,
            body.sort_order,
            auth.actor.user_id,
            now,
            now if body.status == "published" else None,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="event.resource.create",
            target_type="event_resource",
            target_id=resource_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            metadata={"status": body.status},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="event_resource",
        resource_id=resource_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    row = row_mapping(
        await db
        .prepare(
            """SELECT id,event_id,title,slug,summary,body_text,embed_url,status,sort_order,
                      version,updated_at_ms FROM event_resources WHERE id=?1"""
        )
        .bind(resource_id)
        .first()
    )
    return _resource(row)


@competition_router.get(
    "/api/v1/speaker/resources", response_model=ResourceList, tags=["speaker-portal"]
)
async def list_speaker_resources(request: Request) -> ResourceList:
    auth = await authenticate_request(request)
    scopes = result_rows(
        await _db(request)
        .prepare(
            """SELECT DISTINCT es.organization_id,es.event_id,p.user_id
               FROM people p JOIN event_speakers es
                 ON es.organization_id=p.organization_id AND es.person_id=p.id
               WHERE p.user_id=?1 AND es.selection_status IN ('submitted','accepted')"""
        )
        .bind(auth.actor.user_id)
        .all()
    )
    for scope in scopes:
        await require_permission(
            request,
            Permission.SPEAKER_TASK_READ_OWN,
            ResourceContext(
                str(scope["organization_id"]),
                str(scope["event_id"]),
                resource_owner_user_id=str(scope["user_id"]),
            ),
            mutation=False,
        )
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT r.id,r.event_id,r.title,r.slug,r.summary,r.body_text,r.embed_url,r.status,
                      r.sort_order,r.version,r.updated_at_ms
               FROM people p JOIN event_speakers es
                 ON es.organization_id=p.organization_id AND es.person_id=p.id
               JOIN event_resources r ON r.organization_id=es.organization_id
                 AND r.event_id=es.event_id AND r.status='published'
               WHERE p.user_id=?1 AND es.selection_status IN ('submitted','accepted')
               ORDER BY r.sort_order,r.title,r.id LIMIT 200"""
        )
        .bind(auth.actor.user_id)
        .all()
    )
    return ResourceList(data=[_resource(row) for row in rows])


@competition_router.get(
    "/api/v1/admin/events/{event_id}/speaker-targets",
    response_model=SpeakerTargetList,
    tags=["speaker-onboarding"],
)
async def list_speaker_targets(event_id: str, request: Request) -> SpeakerTargetList:
    event, _ = await _managed_event(request, event_id, mutation=False)
    rows = result_rows(
        await _db(request)
        .prepare(
            """WITH targets AS (
                 SELECT es.id AS event_speaker_id,p.id AS person_id,p.user_id,
                        COALESCE(u.email,'') AS email,
                        p.display_name,COALESCE(p.job_title,'') AS job_title,
                        COALESCE(p.company,'') AS company,
                        COALESCE(p.biography,'') AS biography,
                        COALESCE(p.location,'') AS location,p.links_json,p.version,
                        es.selection_status,
                        COALESCE(
                          -- Prefer the ACCEPTED submission; fall back to newest.
                          (SELECT s.proposal_title FROM submission_speakers ss
                            JOIN submissions s ON s.organization_id=ss.organization_id
                             AND s.event_id=ss.event_id AND s.id=ss.submission_id
                            JOIN accepted_sessions ac ON ac.organization_id=s.organization_id
                             AND ac.event_id=s.event_id AND ac.submission_id=s.id
                            WHERE ss.organization_id=es.organization_id
                             AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                            ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),
                          (SELECT s.proposal_title FROM submission_speakers ss
                            JOIN submissions s ON s.organization_id=ss.organization_id
                             AND s.event_id=ss.event_id AND s.id=ss.submission_id
                            WHERE ss.organization_id=es.organization_id
                             AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                            ORDER BY s.submitted_at_ms DESC LIMIT 1),
                          'No proposal') AS proposal_title
                 FROM event_speakers es JOIN people p
                   ON p.organization_id=es.organization_id AND p.id=es.person_id
                 LEFT JOIN users u ON u.id=p.user_id
                 WHERE es.organization_id=?1 AND es.event_id=?2
                 UNION ALL
                 SELECT i.id,NULL,NULL,i.email,
                        COALESCE(NULLIF(i.display_name,''),i.email),i.job_title,i.company,
                        '','','[]',1,'invited','Invitation pending'
                 FROM identity_invitations i
                 WHERE i.organization_id=?1 AND i.event_id=?2 AND i.role='speaker'
                   AND i.status='pending' AND i.expires_at_ms>?3
               )
               SELECT * FROM targets ORDER BY display_name,event_speaker_id LIMIT 500"""
        )
        .bind(event["organization_id"], event_id, utc_now_ms())
        .all()
    )
    return SpeakerTargetList(data=[_speaker_target(row) for row in rows])


@competition_router.get(
    "/api/v1/admin/organizations/{organization_id}/people",
    response_model=OrganizationSpeakerList,
    tags=["administration"],
)
async def list_organization_speakers(
    organization_id: str, request: Request
) -> OrganizationSpeakerList:
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(organization_id),
        mutation=False,
    )
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT p.id AS person_id,p.user_id,COALESCE(u.email,'') AS email,
                      COALESCE(u.public_profile_enabled,0) AS public_profile_enabled,
                      p.display_name,COALESCE(p.job_title,'') AS job_title,
                      COALESCE(p.company,'') AS company,
                      COALESCE(p.biography,'') AS biography,
                      COALESCE(p.location,'') AS location,p.links_json,p.version,
                      es.id AS event_speaker_id,es.event_id,e.name AS event_name,
                      es.selection_status,
                      -- Prefer the ACCEPTED submission; fall back to newest.
                      COALESCE((SELECT s.proposal_title FROM submission_speakers ss
                        JOIN submissions s ON s.organization_id=ss.organization_id
                         AND s.event_id=ss.event_id AND s.id=ss.submission_id
                        JOIN accepted_sessions ac ON ac.organization_id=s.organization_id
                         AND ac.event_id=s.event_id AND ac.submission_id=s.id
                        WHERE ss.organization_id=es.organization_id
                         AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                        ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),
                       (SELECT s.proposal_title FROM submission_speakers ss
                        JOIN submissions s ON s.organization_id=ss.organization_id
                         AND s.event_id=ss.event_id AND s.id=ss.submission_id
                        WHERE ss.organization_id=es.organization_id
                         AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                        ORDER BY s.submitted_at_ms DESC,s.id DESC LIMIT 1),
                        'No proposal') AS proposal_title
               FROM people p
               JOIN event_speakers es ON es.organization_id=p.organization_id
                 AND es.person_id=p.id
               JOIN events e ON e.organization_id=es.organization_id AND e.id=es.event_id
               LEFT JOIN users u ON u.id=p.user_id
               WHERE p.organization_id=?1 AND p.archived_at_ms IS NULL
                 AND e.status!='archived' AND es.status!='withdrawn'
                 AND (
                   EXISTS(SELECT 1 FROM owned_resources owned
                     WHERE owned.id=es.event_id AND owned.resource_type='event'
                       AND owned.status='active' AND owned.owner_user_id=?2)
                   OR EXISTS(SELECT 1 FROM resource_access_grants grant_access
                     JOIN owned_resources granted_resource
                       ON granted_resource.id=grant_access.resource_id
                      AND granted_resource.resource_type='event'
                      AND granted_resource.status='active'
                     WHERE grant_access.resource_id=es.event_id
                       AND grant_access.user_id=?2 AND grant_access.status='active'
                       AND grant_access.permission IN ('edit','manage'))
                 )
               ORDER BY p.display_name,p.id,e.starts_at_ms DESC,e.id,es.id LIMIT 5000"""
        )
        .bind(organization_id, authenticated.actor.user_id)
        .all()
    )
    people: dict[str, OrganizationSpeaker] = {}
    for row in rows:
        person_id = str(row["person_id"])
        person = people.get(person_id)
        if person is None:
            person = OrganizationSpeaker(
                person_id=person_id,
                user_id=str(row["user_id"]) if row["user_id"] is not None else None,
                public_profile_enabled=bool(row["public_profile_enabled"]),
                email=str(row["email"]),
                display_name=str(row["display_name"]),
                job_title=str(row["job_title"]),
                company=str(row["company"]),
                biography=str(row["biography"]),
                location=str(row["location"]),
                links=json.loads(str(row["links_json"])),
                version=int(row["version"]),
                organization_roles=["Speaker"], event_associations=[],
                participations=[],
            )
            people[person_id] = person
        person.participations.append(
            OrganizationSpeakerParticipation(
                event_id=str(row["event_id"]),
                event_name=str(row["event_name"]),
                event_speaker_id=str(row["event_speaker_id"]),
                selection_status=str(row["selection_status"]),
                proposal_title=str(row["proposal_title"]),
            )
        )
        person.event_associations.append(OrganizationPersonEventAssociation(
            event_id=str(row["event_id"]), event_name=str(row["event_name"]),
            role="Speaker", status=str(row["selection_status"]),
        ))
    organizer_rows = result_rows(await _db(request).prepare(
        """SELECT DISTINCT u.id AS user_id,u.email,u.public_profile_enabled,
                  COALESCE(NULLIF(u.display_name,''),u.email) AS display_name
           FROM users u WHERE u.status='active' AND (
             EXISTS(SELECT 1 FROM owned_resources o WHERE o.id=?1
               AND o.resource_type='organization' AND o.status='active' AND o.owner_user_id=u.id)
             OR EXISTS(SELECT 1 FROM resource_access_grants g
               JOIN owned_resources r ON r.id=g.resource_id AND r.resource_type='organization'
                AND r.status='active' WHERE g.resource_id=?1 AND g.user_id=u.id
                AND g.status='active' AND g.permission='manage'))
           ORDER BY display_name,u.id LIMIT 500"""
    ).bind(organization_id).all())
    by_user = {p.user_id: p for p in people.values() if p.user_id}
    for row in organizer_rows:
        user_id = str(row["user_id"])
        if user_id in by_user:
            if "Organizer" not in by_user[user_id].organization_roles:
                by_user[user_id].organization_roles.insert(0, "Organizer")
        else:
            people[f"organizer:{user_id}"] = OrganizationSpeaker(
                person_id="", user_id=user_id,
                public_profile_enabled=bool(row["public_profile_enabled"]),
                email=str(row["email"]),
                display_name=str(row["display_name"]), job_title="", company="",
                biography="", location="", links=[], version=1,
                organization_roles=["Organizer"], event_associations=[], participations=[])
    invitations = result_rows(await _db(request).prepare(
        """SELECT i.normalized_email,i.email,i.display_name,i.role,i.status,i.event_id,
                  e.name AS event_name,u.id AS user_id,
                  COALESCE(u.public_profile_enabled,0) AS public_profile_enabled
           FROM identity_invitations i
           JOIN events e ON e.id=i.event_id AND e.organization_id=i.organization_id
           LEFT JOIN users u ON u.normalized_email=i.normalized_email AND u.status='active'
           WHERE i.organization_id=?1 AND i.role IN ('speaker','evaluator')
             AND i.status IN ('pending','accepted') AND e.status!='archived'
           ORDER BY i.normalized_email,e.name,i.id LIMIT 1000"""
    ).bind(organization_id).all())
    by_email = {p.email.casefold(): p for p in people.values()}
    for row in invitations:
        email, role = str(row["email"]), ("Reviewer" if row["role"] == "evaluator" else "Speaker")
        person = by_email.get(email.casefold())
        if person is None:
            person = OrganizationSpeaker(
                person_id="", user_id=(str(row["user_id"]) if row["user_id"] else None),
                public_profile_enabled=bool(row["public_profile_enabled"]),
                email=email, display_name=str(row["display_name"] or email), job_title="",
                company="", biography="", location="", links=[], version=1,
                organization_roles=[role], event_associations=[], participations=[])
            people[f"invite:{row['normalized_email']}"] = person
            by_email[email.casefold()] = person
        elif role not in person.organization_roles:
            person.organization_roles.append(role)
        association = OrganizationPersonEventAssociation(
            event_id=str(row["event_id"]), event_name=str(row["event_name"]),
            role=role, status=str(row["status"]))
        if association not in person.event_associations:
            person.event_associations.append(association)
    return OrganizationSpeakerList(organization_id=organization_id, data=list(people.values()))


def _speaker_target(row) -> SpeakerTarget:
    return SpeakerTarget(
        event_speaker_id=str(row["event_speaker_id"]),
        person_id=str(row["person_id"]) if row["person_id"] is not None else None,
        user_id=str(row["user_id"]) if row["user_id"] is not None else None,
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        job_title=str(row["job_title"]),
        company=str(row["company"]),
        biography=str(row["biography"]),
        location=str(row["location"]),
        links=json.loads(str(row["links_json"])),
        version=int(row["version"]),
        selection_status=str(row["selection_status"]),
        proposal_title=str(row["proposal_title"]),
    )


async def _speaker_profile_page(
    person_id: str, request: Request, *, mutation: bool
) -> tuple[SpeakerProfilePageView, object]:
    authenticated = await authenticate_request(request)
    db = _db(request)
    person = row_mapping(
        await db.prepare(
            """SELECT p.id AS person_id,p.organization_id,p.user_id,
                      COALESCE(u.email,'') AS email,p.display_name,
                      COALESCE(p.job_title,'') AS job_title,
                      COALESCE(p.company,'') AS company,
                      COALESCE(p.biography,'') AS biography,
                      COALESCE(p.location,'') AS location,p.links_json,p.version
               FROM people p LEFT JOIN users u ON u.id=p.user_id
               WHERE p.id=?1 AND p.archived_at_ms IS NULL"""
        )
        .bind(person_id)
        .first()
    )
    if person is None:
        raise HTTPException(status_code=404)
    participation_rows = result_rows(
        await db.prepare(
            """SELECT es.event_id,e.name AS event_name,es.id AS event_speaker_id,
                      es.selection_status,
                      -- Prefer the ACCEPTED submission; fall back to newest.
                      COALESCE((SELECT s.proposal_title FROM submission_speakers ss
                        JOIN submissions s ON s.organization_id=ss.organization_id
                         AND s.event_id=ss.event_id AND s.id=ss.submission_id
                        JOIN accepted_sessions ac ON ac.organization_id=s.organization_id
                         AND ac.event_id=s.event_id AND ac.submission_id=s.id
                        WHERE ss.organization_id=es.organization_id
                         AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                        ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),
                       (SELECT s.proposal_title FROM submission_speakers ss
                        JOIN submissions s ON s.organization_id=ss.organization_id
                         AND s.event_id=ss.event_id AND s.id=ss.submission_id
                        WHERE ss.organization_id=es.organization_id
                         AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                        ORDER BY s.submitted_at_ms DESC,s.id DESC LIMIT 1),
                        'No proposal') AS proposal_title
               FROM event_speakers es JOIN events e
                 ON e.organization_id=es.organization_id AND e.id=es.event_id
               WHERE es.organization_id=?1 AND es.person_id=?2
                 AND es.status!='withdrawn' AND e.status!='archived'
               ORDER BY e.starts_at_ms DESC,e.id"""
        )
        .bind(person["organization_id"], person_id)
        .all()
    )
    if not participation_rows:
        raise HTTPException(status_code=404)
    owner = str(person["user_id"] or "") == authenticated.actor.user_id
    candidate = None
    if owner:
        candidate = next(
            (
                row
                for row in participation_rows
                if authenticated.actor.event_roles.get(
                    (str(person["organization_id"]), str(row["event_id"])), frozenset()
                )
            ),
            None,
        )
        permission = (
            Permission.SPEAKER_PROFILE_EDIT_OWN
            if mutation
            else Permission.SPEAKER_PROFILE_READ_OWN
        )
    else:
        candidate = next(
            (
                row
                for row in participation_rows
                if authenticated.actor.active_persona is Persona.ORGANIZER
                and (
                    str(row["event_id"]) in authenticated.actor.owned_resource_ids
                    or bool(
                        authenticated.actor.resource_grants.get(
                            str(row["event_id"]), frozenset()
                        )
                        & {ResourceGrant.EDIT, ResourceGrant.MANAGE}
                    )
                )
            ),
            None,
        )
        permission = Permission.SPEAKER_MANAGE
    if candidate is None or (mutation and not owner):
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        permission,
        ResourceContext(
            str(person["organization_id"]),
            str(candidate["event_id"]),
            resource_owner_user_id=str(person["user_id"] or ""),
        ),
        mutation=mutation,
    )
    participations = [
        OrganizationSpeakerParticipation(
            event_id=str(row["event_id"]),
            event_name=str(row["event_name"]),
            event_speaker_id=str(row["event_speaker_id"]),
            selection_status=str(row["selection_status"]),
            proposal_title=str(row["proposal_title"]),
        )
        for row in participation_rows
    ]
    view = SpeakerProfilePageView(
        person_id=person_id,
        user_id=str(person["user_id"]) if person["user_id"] is not None else None,
        email=str(person["email"]) if owner else "",
        display_name=str(person["display_name"]),
        job_title=str(person["job_title"]),
        company=str(person["company"]),
        biography=str(person["biography"]),
        location=str(person["location"]),
        links=json.loads(str(person["links_json"])),
        version=int(person["version"]),
        participations=participations,
        can_edit=owner,
    )
    return view, authenticated


@competition_router.get(
    "/api/v1/speaker-profiles/{person_id}",
    response_model=SpeakerProfilePageView,
    tags=["speaker-onboarding"],
)
async def get_speaker_profile_page(person_id: str, request: Request) -> SpeakerProfilePageView:
    profile, _ = await _speaker_profile_page(person_id, request, mutation=False)
    return profile


@competition_router.patch(
    "/api/v1/speaker-profiles/{person_id}",
    response_model=SpeakerProfilePageView,
    tags=["speaker-onboarding"],
)
async def update_own_speaker_profile_page(
    person_id: str, body: AdminSpeakerUpdate, request: Request
) -> SpeakerProfilePageView:
    profile, authenticated = await _speaker_profile_page(person_id, request, mutation=True)
    changed = row_mapping(
        await _db(request).prepare(
            """UPDATE people SET display_name=?1,job_title=?2,company=?3,biography=?4,
                      location=?5,links_json=?6,version=version+1,updated_at_ms=?7
               WHERE id=?8 AND user_id=?9 AND version=?10 RETURNING id"""
        ).bind(
            body.display_name, body.job_title or None, body.company or None,
            body.biography or None, body.location or None,
            json.dumps(body.links, separators=(",", ":")), utc_now_ms(), person_id,
            authenticated.actor.user_id, body.version,
        ).first()
    )
    if changed is None:
        raise HTTPException(status_code=409)
    updated, _ = await _speaker_profile_page(person_id, request, mutation=False)
    return updated


@competition_router.patch(
    "/api/v1/admin/events/{event_id}/speakers/{event_speaker_id}",
    response_model=SpeakerTarget,
    tags=["speaker-onboarding"],
)
async def update_admin_speaker(
    event_id: str, event_speaker_id: str, body: AdminSpeakerUpdate, request: Request
) -> SpeakerTarget:
    event, auth = await _managed_event(request, event_id, mutation=True)
    db, now = _db(request), utc_now_ms()
    changed = row_mapping(
        await db.prepare(
            """UPDATE people SET display_name=?1,job_title=?2,company=?3,biography=?4,
                      location=?5,links_json=?6,version=version+1,updated_at_ms=?7
               WHERE organization_id=?8 AND version=?9 AND id=(
                 SELECT person_id FROM event_speakers
                 WHERE id=?10 AND organization_id=?8 AND event_id=?11)
               RETURNING id"""
        )
        .bind(
            body.display_name,
            body.job_title or None,
            body.company or None,
            body.biography or None,
            body.location or None,
            json.dumps(body.links, separators=(",", ":")),
            now,
            event["organization_id"],
            body.version,
            event_speaker_id,
            event_id,
        )
        .first()
    )
    if changed is None:
        exists = await (
            db.prepare(
                """SELECT 1 AS found FROM event_speakers
                   WHERE id=?1 AND organization_id=?2 AND event_id=?3 LIMIT 1"""
            )
            .bind(event_speaker_id, event["organization_id"], event_id)
            .first("found")
        )
        raise HTTPException(status_code=409 if exists is not None else 404)
    audit = CommandBatch(db)
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="speaker.profile.admin_update",
            target_type="event_speaker",
            target_id=event_speaker_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(event["organization_id"]),
            event_id=event_id,
        )
    )
    await audit.execute()
    row = row_mapping(
        await db.prepare(
            """SELECT es.id AS event_speaker_id,p.id AS person_id,p.user_id,
                      COALESCE(u.email,'') AS email,
                      p.display_name,COALESCE(p.job_title,'') AS job_title,
                      COALESCE(p.company,'') AS company,COALESCE(p.biography,'') AS biography,
                      COALESCE(p.location,'') AS location,p.links_json,p.version,
                      es.selection_status,
                      COALESCE(
                        -- Prefer the ACCEPTED submission; fall back to newest.
                        (SELECT s.proposal_title FROM submission_speakers ss
                          JOIN submissions s ON s.organization_id=ss.organization_id
                           AND s.event_id=ss.event_id AND s.id=ss.submission_id
                          JOIN accepted_sessions ac ON ac.organization_id=s.organization_id
                           AND ac.event_id=s.event_id AND ac.submission_id=s.id
                          WHERE ss.organization_id=es.organization_id
                           AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                          ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),
                        (SELECT s.proposal_title FROM submission_speakers ss
                          JOIN submissions s ON s.organization_id=ss.organization_id
                           AND s.event_id=ss.event_id AND s.id=ss.submission_id
                          WHERE ss.organization_id=es.organization_id
                           AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id
                          ORDER BY s.submitted_at_ms DESC LIMIT 1),
                        'No proposal') AS proposal_title
               FROM event_speakers es JOIN people p ON p.organization_id=es.organization_id
                 AND p.id=es.person_id LEFT JOIN users u ON u.id=p.user_id
               WHERE es.id=?1 AND es.organization_id=?2 AND es.event_id=?3 LIMIT 1"""
        )
        .bind(event_speaker_id, event["organization_id"], event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return _speaker_target(row)


async def _session_content_view(
    db, organization_id: str, event_id: str, accepted_session_id: str
) -> AdminSessionContentView:
    current = row_mapping(
        await db.prepare(
            """SELECT ac.id,s.proposal_title,s.proposal_abstract,
                      ac.content_status,ac.version
               FROM accepted_sessions ac JOIN submissions s
                 ON s.organization_id=ac.organization_id AND s.event_id=ac.event_id
                AND s.id=ac.submission_id
               WHERE ac.organization_id=?1 AND ac.event_id=?2 AND ac.id=?3 LIMIT 1"""
        )
        .bind(organization_id, event_id, accepted_session_id)
        .first()
    )
    if current is None:
        raise HTTPException(status_code=404)
    history = result_rows(
        await db.prepare(
            """SELECT history.version,history.title,history.abstract,
                      history.content_status,history.created_at_ms,
                      COALESCE(u.display_name,u.email) AS changed_by
               FROM session_content_versions history JOIN users u
                 ON u.id=history.changed_by_user_id
               WHERE history.organization_id=?1 AND history.event_id=?2
                 AND history.accepted_session_id=?3
               ORDER BY history.version DESC LIMIT 50"""
        )
        .bind(organization_id, event_id, accepted_session_id)
        .all()
    )
    return AdminSessionContentView(
        accepted_session_id=str(current["id"]),
        title=str(current["proposal_title"]),
        abstract=str(current["proposal_abstract"]),
        content_status=str(current["content_status"]),
        version=int(current["version"]),
        history=[
            SessionContentVersionView(
                version=int(row["version"]),
                title=str(row["title"]),
                abstract=str(row["abstract"]),
                content_status=str(row["content_status"]),
                changed_by=str(row["changed_by"]),
                created_at_ms=int(row["created_at_ms"]),
            )
            for row in history
        ],
    )


@competition_router.get(
    "/api/v1/admin/events/{event_id}/sessions/{accepted_session_id}/content",
    response_model=AdminSessionContentView,
    tags=["session-content"],
)
async def get_admin_session_content(
    event_id: str, accepted_session_id: str, request: Request
) -> AdminSessionContentView:
    event, _ = await _managed_event(request, event_id, mutation=False)
    return await _session_content_view(
        _db(request), str(event["organization_id"]), event_id, accepted_session_id
    )


async def _save_session_content(
    event_id: str,
    accepted_session_id: str,
    request: Request,
    body: AdminSessionContentUpdate,
) -> AdminSessionContentView:
    event, auth = await _managed_event(request, event_id, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    session = row_mapping(
        await db.prepare(
            """SELECT ac.submission_id,ac.version,ac.content_status,
                      s.proposal_title,s.proposal_abstract
               FROM accepted_sessions ac JOIN submissions s
                 ON s.organization_id=ac.organization_id AND s.event_id=ac.event_id
                AND s.id=ac.submission_id
               WHERE ac.organization_id=?1 AND ac.event_id=?2 AND ac.id=?3 LIMIT 1"""
        )
        .bind(organization_id, event_id, accepted_session_id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404)
    if int(session["version"]) != body.version:
        raise HTTPException(status_code=409)
    if (
        body.title == str(session["proposal_title"])
        and body.abstract == str(session["proposal_abstract"])
        and body.content_status == str(session["content_status"])
    ):
        # Nothing changed: saving is idempotent. Without this guard every
        # no-op save (an untouched editor dialog, a restore of the current
        # version) minted a new version, silently shifting the numbering
        # users see in Content history — the eval run's apparent
        # "restore is off-by-one" was exactly this drift.
        return await _session_content_view(db, organization_id, event_id, accepted_session_id)
    now, next_version = utc_now_ms(), body.version + 1
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT OR IGNORE INTO session_content_versions
               (id,organization_id,event_id,accepted_session_id,version,title,abstract,
                content_status,changed_by_user_id,created_at_ms)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)"""
        ).bind(
            new_id(),
            organization_id,
            event_id,
            accepted_session_id,
            body.version,
            session["proposal_title"],
            session["proposal_abstract"],
            session["content_status"],
            auth.actor.user_id,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE submissions SET proposal_title=?1,proposal_abstract=?2,
                      updated_at_ms=?3
               WHERE organization_id=?4 AND event_id=?5 AND id=?6"""
        ).bind(
            body.title,
            body.abstract,
            now,
            organization_id,
            event_id,
            session["submission_id"],
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE accepted_sessions SET content_status=?1,version=version+1
               WHERE organization_id=?2 AND event_id=?3 AND id=?4 AND version=?5"""
        ).bind(body.content_status, organization_id, event_id, accepted_session_id, body.version)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO session_content_write_guards
               (id,accepted_session_id,applied_changes,created_at_ms)
               VALUES (?1,?2,changes(),?3)"""
        ).bind(new_id(), accepted_session_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO session_content_versions
               (id,organization_id,event_id,accepted_session_id,version,title,abstract,
                content_status,changed_by_user_id,created_at_ms)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)"""
        ).bind(
            new_id(),
            organization_id,
            event_id,
            accepted_session_id,
            next_version,
            body.title,
            body.abstract,
            body.content_status,
            auth.actor.user_id,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="session.content.update",
            target_type="accepted_session",
            target_id=accepted_session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={"version": next_version, "content_status": body.content_status},
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return await _session_content_view(db, organization_id, event_id, accepted_session_id)


@competition_router.patch(
    "/api/v1/admin/events/{event_id}/sessions/{accepted_session_id}/content",
    response_model=AdminSessionContentView,
    tags=["session-content"],
)
async def update_admin_session_content(
    event_id: str,
    accepted_session_id: str,
    body: AdminSessionContentUpdate,
    request: Request,
) -> AdminSessionContentView:
    return await _save_session_content(event_id, accepted_session_id, request, body)


@competition_router.post(
    "/api/v1/admin/events/{event_id}/sessions/{accepted_session_id}/content/restore",
    response_model=AdminSessionContentView,
    tags=["session-content"],
)
async def restore_admin_session_content(
    event_id: str,
    accepted_session_id: str,
    body: AdminSessionContentRestore,
    request: Request,
) -> AdminSessionContentView:
    event, _ = await _managed_event(request, event_id, mutation=True)
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT title,abstract,content_status FROM session_content_versions
               WHERE organization_id=?1 AND event_id=?2 AND accepted_session_id=?3
                 AND version=?4 LIMIT 1"""
        )
        .bind(
            event["organization_id"],
            event_id,
            accepted_session_id,
            body.history_version,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    restored = AdminSessionContentUpdate(
        title=str(row["title"]),
        abstract=str(row["abstract"]),
        content_status=str(row["content_status"]),
        version=body.current_version,
    )
    return await _save_session_content(event_id, accepted_session_id, request, restored)


@competition_router.post(
    "/api/v1/admin/events/{event_id}/speaker-tasks",
    response_model=AdminSpeakerTaskView,
    status_code=201,
    tags=["speaker-onboarding"],
)
async def create_custom_speaker_task(
    event_id: str,
    body: SpeakerTaskCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AdminSpeakerTaskView:
    if idempotency_key is None or not 16 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=400)
    event, auth = await _managed_event(request, event_id, mutation=True)
    db, now, task_id = _db(request), utc_now_ms(), new_id()
    route = "POST /api/v1/admin/events/{event_id}/speaker-tasks"
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    ).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND state='completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(idempotency_key.encode()).digest())
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        task_id = str(replay["response_resource_id"])
        row = row_mapping(
            await db.prepare(
                """SELECT id,event_speaker_id,pending_invitation_id AS invitation_id,
                          CASE WHEN event_speaker_id IS NULL THEN 'invitation'
                               ELSE 'event_speaker' END AS owner_type,
                          title,state,due_at_ms FROM speaker_tasks WHERE id=?1"""
            )
            .bind(task_id)
            .first()
        )
        return AdminSpeakerTaskView.model_validate(row)
    owner = row_mapping(
        await db.prepare(
            """SELECT id FROM event_speakers WHERE id=?1 AND organization_id=?2 AND event_id=?3
               AND selection_status IN ('submitted','accepted') LIMIT 1"""
        )
        .bind(body.event_speaker_id, event["organization_id"], event_id)
        .first()
    )
    pending_invitation_id: str | None = None
    if owner is None:
        pending = row_mapping(
            await db.prepare(
                """SELECT id FROM identity_invitations
                   WHERE id=?1 AND organization_id=?2 AND event_id=?3
                     AND role='speaker' AND status='pending' AND expires_at_ms>?4 LIMIT 1"""
            )
            .bind(body.event_speaker_id, event["organization_id"], event_id, now)
            .first()
        )
        if pending is None:
            raise HTTPException(status_code=404)
        pending_invitation_id = str(pending["id"])
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        route_key=route,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,pending_invitation_id,
                submission_id,task_type,title,
                help_text,destination_type,state,due_at_ms,form_schema_json,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,'custom',?7,?8,'custom','open',?9,?10,?11,?11)"""
        ).bind(
            task_id,
            event["organization_id"],
            event_id,
            body.event_speaker_id if owner is not None else None,
            pending_invitation_id,
            body.submission_id,
            body.title,
            body.help_text,
            body.due_at_ms,
            json.dumps(
                {"fields": [field.model_dump() for field in body.fields]},
                separators=(",", ":"),
            ),
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="speaker.task.create",
            target_type="speaker_task",
            target_id=task_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            metadata={"custom_fields": len(body.fields)},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="speaker_task",
        resource_id=task_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return AdminSpeakerTaskView(
        id=task_id,
        owner_type="event_speaker" if owner is not None else "invitation",
        event_speaker_id=body.event_speaker_id if owner is not None else None,
        invitation_id=pending_invitation_id,
        title=body.title,
        state="open",
        due_at_ms=body.due_at_ms,
    )


@competition_router.post(
    "/api/v1/admin/events/{event_id}/integrations/accelevents/tokens",
    response_model=IntegrationTokenView,
    status_code=201,
    tags=["integrations"],
)
async def create_accelevents_token(
    event_id: str,
    body: IntegrationTokenCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> IntegrationTokenView:
    event, auth = await _managed_event(request, event_id, mutation=True)
    if idempotency_key is None or not 16 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=400)
    route = "POST /api/v1/admin/events/{event_id}/integrations/accelevents/tokens"
    fingerprint = hashlib.sha256(
        json.dumps(
            {"event_id": event_id, "body": body.model_dump()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).digest()
    db = _db(request)
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id,state
               FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND organization_id=?4 AND event_id=?5 LIMIT 1"""
        )
        .bind(
            auth.actor.user_id,
            route,
            hashlib.sha256(idempotency_key.encode()).digest(),
            event["organization_id"],
            event_id,
        )
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        stored = row_mapping(
            await db.prepare(
                """SELECT id FROM event_integration_tokens
                   WHERE id=?1 AND organization_id=?2 AND event_id=?3
                     AND provider='accelevents' LIMIT 1"""
            )
            .bind(
                replay["response_resource_id"],
                event["organization_id"],
                event_id,
            )
            .first()
        )
        if replay["state"] == "completed" and stored is None:
            raise HTTPException(status_code=409)
        raise HTTPException(
            status_code=409,
            detail=(
                "This token-generation request already ran. Its secret is shown only in "
                "the original response and cannot be replayed. Do not regenerate it."
            ),
        )
    now, token_id, token = utc_now_ms(), new_id(), generate_token()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        route_key=route,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_integration_tokens
               (id,organization_id,event_id,provider,label,token_hash,status,created_by_user_id,
                created_at_ms) VALUES(?1,?2,?3,'accelevents',?4,?5,'active',?6,?7)"""
        ).bind(
            token_id,
            event["organization_id"],
            event_id,
            body.label,
            hash_token(token),
            auth.actor.user_id,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="integration.token.create",
            target_type="event_integration_token",
            target_id=token_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            metadata={"provider": "accelevents"},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="event_integration_token",
        resource_id=token_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "This token-generation request is already in progress or completed. "
                "Do not regenerate it."
            ),
        ) from exc
    return IntegrationTokenView(
        id=token_id, event_id=event_id, label=body.label, token=token, created_at_ms=now
    )


async def _integration_event(request: Request, event_id: str, token: str | None):
    if token is None or len(token) < 32:
        raise HTTPException(status_code=401)
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT t.id,t.organization_id,t.event_id,e.name,e.time_zone
               FROM event_integration_tokens t JOIN events e
                 ON e.organization_id=t.organization_id AND e.id=t.event_id
               WHERE t.token_hash=?1 AND t.event_id=?2 AND t.provider='accelevents'
                 AND t.status='active' AND e.status!='archived' LIMIT 1"""
        )
        .bind(hash_token(token), event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=401)
    await _db(request).prepare(
        "UPDATE event_integration_tokens SET last_used_at_ms=?1 WHERE id=?2"
    ).bind(utc_now_ms(), row["id"]).run()
    return row


def _iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z") if ms else None


@competition_router.post("/v1/event/{event_id}/sessions", tags=["integrations"])
async def sessionboard_compatible_sessions(
    event_id: str,
    request: Request,
    response: Response,
    x_access_token: str | None = Header(default=None, alias="x-access-token"),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "private, no-store"
    event = await _integration_event(request, event_id, x_access_token)
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT ac.id,s.proposal_title,s.proposal_abstract,ai.starts_at_ms,ai.ends_at_ms,
                      r.name AS room_name,t.name AS track_name
               FROM accepted_sessions ac JOIN submissions s ON s.id=ac.submission_id
               LEFT JOIN agenda_items ai ON ai.accepted_session_id=ac.id
                 AND ai.revision_id=(SELECT id FROM schedule_revisions
                   WHERE organization_id=ac.organization_id AND event_id=ac.event_id
                     AND status='published' LIMIT 1)
               LEFT JOIN event_rooms r ON r.id=ai.room_id
               LEFT JOIN event_tracks t ON t.id=ai.track_id
               WHERE ac.organization_id=?1 AND ac.event_id=?2
               ORDER BY ai.starts_at_ms,s.proposal_title"""
        )
        .bind(event["organization_id"], event_id)
        .all()
    )
    results = []
    for row in rows:
        speakers = result_rows(
            await _db(request)
            .prepare(
                """SELECT es.id,p.display_name,p.company,p.job_title,u.email
                   FROM accepted_sessions ac JOIN submission_speakers ss
                     ON ss.submission_id=ac.submission_id
                   JOIN event_speakers es ON es.id=ss.event_speaker_id
                   JOIN people p ON p.id=es.person_id LEFT JOIN users u ON u.id=p.user_id
                   WHERE ac.id=?1 ORDER BY ss.role DESC,p.display_name"""
            )
            .bind(row["id"])
            .all()
        )
        results.append(
            {
                "id": str(row["id"]),
                "friendly_id": str(row["id"]),
                "title": str(row["proposal_title"]),
                "description": str(row["proposal_abstract"]),
                "starts_at": _iso(int(row["starts_at_ms"])) if row["starts_at_ms"] else None,
                "ends_at": _iso(int(row["ends_at_ms"])) if row["ends_at_ms"] else None,
                "is_public": True,
                "room": {"name": str(row["room_name"])} if row["room_name"] else None,
                "tracks": ([{"name": str(row["track_name"])}] if row["track_name"] else []),
                "speakers": [
                    {
                        "id": str(speaker["id"]),
                        "friendly_id": str(speaker["id"]),
                        "full_name": str(speaker["display_name"]),
                        "email": str(speaker["email"] or ""),
                        "company_name": str(speaker["company"] or ""),
                        "title": str(speaker["job_title"] or ""),
                    }
                    for speaker in speakers
                ],
            }
        )
    return {
        "results": results,
        "pagination": {
            "currentPage": 1,
            "pageSize": len(results),
            "totalPages": 1,
            "totalResults": len(results),
        },
    }


@competition_router.post("/v1/event/{event_id}/speakers", tags=["integrations"])
async def sessionboard_compatible_speakers(
    event_id: str,
    request: Request,
    response: Response,
    x_access_token: str | None = Header(default=None, alias="x-access-token"),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "private, no-store"
    event = await _integration_event(request, event_id, x_access_token)
    public_base = str(request.base_url).rstrip("/")
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT DISTINCT es.id,p.display_name,p.job_title,p.company,p.biography,p.location,
                      p.links_json,u.email,p.created_at_ms,p.updated_at_ms
               FROM event_speakers es JOIN people p ON p.id=es.person_id
               LEFT JOIN users u ON u.id=p.user_id
               WHERE es.organization_id=?1 AND es.event_id=?2 AND es.selection_status='accepted'
               ORDER BY p.display_name,es.id"""
        )
        .bind(event["organization_id"], event_id)
        .all()
    )
    results = [
        {
            "id": str(row["id"]),
            "friendly_id": str(row["id"]),
            "full_name": str(row["display_name"]),
            "first_name": str(row["display_name"]).partition(" ")[0],
            "last_name": str(row["display_name"]).partition(" ")[2],
            "email": str(row["email"] or ""),
            "created_at": _iso(int(row["created_at_ms"])),
            "updated_at": _iso(int(row["updated_at_ms"])),
            "photo_url": (
                f"{public_base}/api/v1/public/events/{quote(event_id)}/speakers/"
                f"{quote(str(row['id']))}/headshot"
            ),
            "company_name": str(row["company"] or ""),
            "title": str(row["job_title"] or ""),
            "about": str(row["biography"] or ""),
            "location": str(row["location"] or ""),
            "links": json.loads(str(row["links_json"])),
        }
        for row in rows
    ]
    return {
        "results": results,
        "pagination": {
            "currentPage": 1,
            "pageSize": len(results),
            "totalPages": 1,
            "totalResults": len(results),
        },
    }


@competition_router.get(
    "/api/v1/public/events/{event_id}/speakers",
    response_model=PublicSpeakerGallery,
    tags=["public-program"],
)
async def public_speakers(event_id: str, request: Request) -> PublicSpeakerGallery:
    event = row_mapping(
        await _db(request)
        .prepare(
            """SELECT id,organization_id,name,time_zone,accent_color,logo_url,
                      cover_image_url,website_url
               FROM events WHERE id=?1 AND status='active' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT DISTINCT es.id,p.display_name,p.job_title,p.company,p.biography,p.location,
                      p.links_json,(EXISTS(SELECT 1 FROM speaker_assets a
                        JOIN speaker_asset_versions av
                        ON av.asset_id=a.id AND av.is_current=1 AND av.scan_state='clean'
                        WHERE a.event_speaker_id=es.id AND a.kind='headshot')
                        OR EXISTS(SELECT 1 FROM user_headshots uh WHERE uh.user_id=p.user_id))
                        AS has_headshot
               FROM event_speakers es JOIN people p ON p.id=es.person_id
               WHERE es.organization_id=?1 AND es.event_id=?2 AND es.selection_status='accepted'
               ORDER BY p.display_name,es.id"""
        )
        .bind(event["organization_id"], event_id)
        .all()
    )
    data = []
    for row in rows:
        sessions = result_rows(
            await _db(request)
            .prepare(
                """SELECT ac.id,s.proposal_title,ai.starts_at_ms,ai.ends_at_ms,
                          r.name AS room_name,COALESCE(t.name,'') AS track_name
                   FROM submission_speakers ss
                   JOIN accepted_sessions ac ON ac.submission_id=ss.submission_id
                   JOIN submissions s ON s.id=ss.submission_id
                   JOIN agenda_items ai ON ai.accepted_session_id=ac.id
                   JOIN schedule_revisions sr ON sr.id=ai.revision_id
                   JOIN event_rooms r ON r.id=ai.room_id
                   LEFT JOIN event_tracks t ON t.id=ai.track_id
                   WHERE ss.event_speaker_id=?1 AND ac.content_status='approved'
                     AND sr.status='published'
                   ORDER BY ai.starts_at_ms,s.proposal_title"""
            )
            .bind(row["id"])
            .all()
        )
        data.append(
            PublicSpeaker(
                id=str(row["id"]),
                display_name=str(row["display_name"]),
                job_title=str(row["job_title"] or ""),
                company=str(row["company"] or ""),
                biography=str(row["biography"] or ""),
                location=str(row["location"] or ""),
                links=[str(value) for value in json.loads(str(row["links_json"]))],
                headshot_url=(
                    f"/api/v1/public/events/{quote(event_id)}/speakers/{quote(str(row['id']))}/headshot"
                    if row["has_headshot"]
                    else None
                ),
                sessions=[
                    {
                        "id": str(item["id"]),
                        "title": str(item["proposal_title"]),
                        "starts_at_ms": int(item["starts_at_ms"]),
                        "ends_at_ms": int(item["ends_at_ms"]),
                        "room_name": str(item["room_name"]),
                        "track_name": str(item["track_name"]),
                    }
                    for item in sessions
                ],
            )
        )
    return PublicSpeakerGallery(
        event={
            "id": event_id,
            "name": str(event["name"]),
            "time_zone": str(event["time_zone"]),
            "accent_color": str(event["accent_color"] or "#3159d9"),
            "logo_url": str(event["logo_url"]) if event["logo_url"] else None,
            "cover_image_url": str(event["cover_image_url"]) if event["cover_image_url"] else None,
            "website_url": str(event["website_url"]) if event["website_url"] else None,
        },
        data=data,
    )


async def _stream_object(stored):
    body = getattr(stored, "body", stored)
    if isinstance(body, (bytes, bytearray, memoryview)):
        yield bytes(body)
        return
    reader_factory = getattr(body, "getReader", None)
    if callable(reader_factory):
        reader = reader_factory()
        try:
            while True:
                chunk = await reader.read()
                if bool(chunk.done):
                    break
                value = chunk.value
                converter = getattr(value, "to_py", None)
                yield bytes(converter() if callable(converter) else value)
        finally:
            release = getattr(reader, "releaseLock", None)
            if callable(release):
                release()
        return
    array_buffer = getattr(stored, "arrayBuffer", None)
    if callable(array_buffer):
        yield bytes(await array_buffer())
        return
    raise RuntimeError("unsupported public object body")


@competition_router.get(
    "/api/v1/public/events/{event_id}/speakers/{event_speaker_id}/headshot",
    response_class=StreamingResponse,
    tags=["public-program"],
)
async def public_speaker_headshot(
    event_id: str, event_speaker_id: str, request: Request
) -> StreamingResponse:
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT object_key,content_type,byte_size FROM (
                 SELECT av.object_key,av.content_type,av.byte_size,0 AS priority
                 FROM event_speakers es
                 JOIN events e ON e.organization_id=es.organization_id AND e.id=es.event_id
                 JOIN speaker_assets a ON a.organization_id=es.organization_id
                   AND a.event_id=es.event_id AND a.event_speaker_id=es.id
                   AND a.kind='headshot'
                 JOIN speaker_asset_versions av ON av.asset_id=a.id AND av.is_current=1
                   AND av.scan_state='clean'
                 WHERE es.id=?1 AND es.event_id=?2 AND es.selection_status='accepted'
                   AND e.status='active'
                 UNION ALL
                 SELECT uh.object_key,uh.content_type,uh.byte_size,1 AS priority
                 FROM event_speakers es
                 JOIN events e ON e.organization_id=es.organization_id AND e.id=es.event_id
                 JOIN people p ON p.id=es.person_id AND p.organization_id=es.organization_id
                 JOIN user_headshots uh ON uh.user_id=p.user_id
                 WHERE es.id=?1 AND es.event_id=?2 AND es.selection_status='accepted'
                   AND e.status='active'
               ) ORDER BY priority LIMIT 1"""
        )
        .bind(event_speaker_id, event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    bucket = getattr(request.scope.get("env"), "ASSETS", None)
    if bucket is None:
        raise HTTPException(status_code=503)
    stored = await bucket.get(str(row["object_key"]))
    if stored is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_object(stored),
        media_type=str(row["content_type"]),
        headers={
            "Cache-Control": "public, max-age=300",
            "Content-Length": str(row["byte_size"]),
        },
    )
