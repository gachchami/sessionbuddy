"""Regression gate for staged CFP uploads, the provisioning revert, and the
magic-link confirmation page.

Covers the acceptance list from the staged-upload implementation spec:
no speaker graph before submission, no leak on abandonment, revoked users
stay revoked, cross-proposal file isolation, scan-mode gating, expiry purge,
and GET-does-not-consume magic links.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app
from sessionbuddy.cfp.staged_uploads import (
    MAX_STAGED_AUTHORIZATIONS_PER_HOUR,
    build_staged_claim,
    purge_expired_staged_assets,
)
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.speaker_operations.asset_boundary import ScanJob, ScanResult, consume_scan_job
from tests.schema import MIGRATIONS
from tests.security.test_production_identity_flow import (
    AllowingRateLimiter,
    CapturingQueue,
    SQLiteD1,
)

PROJECT_ROOT = Path(__file__).parents[2]
STATIC = PROJECT_ROOT / "src" / "sessionbuddy" / "static"

FORM_SCHEMA = {
    "fields": [
        {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
        {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
        {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
        {"key": "proposal_abstract", "type": "textarea", "label": "Abstract", "required": True},
        {"key": "paper", "type": "file", "label": "Paper", "required": True},
    ],
    "conditions": [],
}


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_deletes = False

    async def put(self, key: str, body: bytes) -> None:
        self.objects[key] = bytes(body)

    async def head(self, key: str):
        if key not in self.objects:
            return None
        return SimpleNamespace(size=len(self.objects[key]))

    async def get(self, key: str):
        return self.objects.get(key)

    async def delete(self, key: str) -> None:
        if self.fail_deletes:
            raise RuntimeError("simulated R2 outage")
        self.objects.pop(key, None)
        self.deleted.append(key)


def _seed_form(connection: sqlite3.Connection) -> None:
    now = 1_000_000
    connection.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES('org','Organization','active',?,?)",
        (now, now),
    )
    connection.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
            delivery_mode,description,status,created_at_ms,updated_at_ms)
           VALUES('event','org','Event',9999999999999,9999999999999999,'UTC','Online',
                  'virtual','Description','active',?,?)""",
        (now, now),
    )
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,
            status,published_at_ms,created_at_ms,updated_at_ms)
           VALUES('form','org','event',1,'event-cfp','Welcome',?,'published',?,?,?)""",
        (json.dumps(FORM_SCHEMA), now, now, now),
    )
    connection.commit()


def _environment(connection: sqlite3.Connection, **overrides):
    queue = CapturingQueue()
    values = {
        "APP_ENV": "local",
        "MALWARE_SCAN_MODE": "disabled",
        "DB": SQLiteD1(connection),
        "SESSION_HMAC_KEY": "s" * 32,
        "CSRF_HMAC_KEY": "c" * 32,
        "PASSWORD_PEPPER": "p" * 32,
        "UPLOAD_HMAC_KEY": "u" * 32,
        "RATE_LIMIT_HMAC_KEY": "r" * 32,
        "AUTH_RATE_LIMITER": AllowingRateLimiter(),
        "PUBLIC_RATE_LIMITER": AllowingRateLimiter(),
        "CFP_UPLOAD_AUTH_RATE_LIMITER": AllowingRateLimiter(),
        "CFP_UPLOAD_POLL_RATE_LIMITER": AllowingRateLimiter(),
        "PUBLIC_BASE_URL": "https://test",
        "ALLOWED_ORIGINS": "https://test",
        "COMMUNICATION_QUEUE": queue,
        "ASSETS": FakeBucket(),
    }
    values.update(overrides)
    return SimpleNamespace(**values), queue


@pytest.fixture
def cfp_environment():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    _seed_form(connection)
    environment, _queue = _environment(connection)
    yield connection, environment
    connection.close()


def _client(environment) -> AsyncClient:
    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    return AsyncClient(transport=ASGITransport(app=inject_environment), base_url="https://test")


def _magic_token(connection: sqlite3.Connection, email: str) -> str:
    row = connection.execute(
        """SELECT html_body FROM communication_messages
           WHERE recipient_email=? ORDER BY queued_at_ms DESC,id DESC LIMIT 1""",
        (email,),
    ).fetchone()
    assert row is not None
    match = re.search(r"/auth/verify\?token=([^\"<]+)", row[0])
    assert match is not None
    return match.group(1)


async def _sign_in(client: AsyncClient, connection: sqlite3.Connection, email: str) -> str:
    requested = await client.post(
        "/api/v1/auth/magic-links",
        json={"email": email, "form_slug": "event-cfp", "redirect_path": "/cfp/event-cfp"},
    )
    assert requested.status_code == 202
    token = _magic_token(connection, email)
    confirmed = await client.post(
        f"/auth/verify?token={token}",
        data={
            "first_name": "Test",
            "last_name": "Speaker",
            "job_title": "Engineer",
            "company": "Example",
            "password": "a private speaker passphrase",
            "password_confirmation": "a private speaker passphrase",
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    return session.json()["csrf_token"]


def _mutation_headers(csrf: str) -> dict[str, str]:
    return {
        "origin": "https://test",
        "x-csrf-token": csrf,
        "content-type": "application/json",
        "idempotency-key": "0123456789abcdef0123456789abcdef",
    }


async def _stage_file(
    client: AsyncClient, csrf: str, body: bytes = b"%PDF-1.4 staged", *, key: str | None = None
) -> str:
    headers = _mutation_headers(csrf)
    if key is not None:
        headers["idempotency-key"] = key
    authorized = await client.post(
        "/api/v1/cfp/forms/form/upload-authorizations",
        headers=headers,
        json={
            "kind": "supporting_document",
            "filename": "paper.pdf",
            "content_type": "application/pdf",
            "byte_size": len(body),
            "checksum_sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    assert authorized.status_code == 201, authorized.text
    authorization = authorized.json()
    uploaded = await client.put(
        authorization["upload_url"],
        headers={"content-type": "application/pdf"},
        content=body,
    )
    assert uploaded.status_code == 204, uploaded.text
    completed = await client.post(
        f"/api/v1/cfp/forms/form/upload-authorizations/{authorization['staged_id']}/complete",
        headers={**_mutation_headers(csrf), "idempotency-key": headers["idempotency-key"][::-1]},
        json={},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "staged"
    return authorization["staged_id"]


def _submission_payload(staged_id: str, title: str = "Staged proposal") -> dict[str, object]:
    return {
        "speaker_name": "Speaker",
        "speaker_email": "speaker@example.test",
        "proposal_title": title,
        "proposal_abstract": "Abstract",
        "answers": {
            "speaker_name": "Speaker",
            "speaker_email": "speaker@example.test",
            "proposal_title": title,
            "proposal_abstract": "Abstract",
            "paper": f"staged:{staged_id}",
        },
        "co_speakers": [],
    }


async def _submit(client: AsyncClient, csrf: str, staged_id: str, *, title: str = "Staged"):
    return await client.post(
        "/api/v1/forms/event-cfp/submissions",
        headers={
            **_mutation_headers(csrf),
            "idempotency-key": f"submission-{title[:90]}-0123456789abcdef",
            "x-public-session-id": "public-session-0123456789abcdef",
        },
        json=_submission_payload(staged_id, title=title),
    )


async def test_upload_authorization_creates_no_speaker_graph(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        staged_id = await _stage_file(client, csrf)

    for table in (
        "people",
        "event_speakers",
        "organization_memberships",
        "event_memberships",
        "speaker_assets",
        "speaker_asset_versions",
        "upload_intents",
    ):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table  # noqa: S608
    staged = connection.execute(
        "SELECT status,form_id,user_id FROM cfp_staged_assets WHERE id=?", (staged_id,)
    ).fetchone()
    assert staged["status"] == "staged"
    assert staged["form_id"] == "form"


async def test_successful_submission_claims_staged_file_atomically(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        staged_id = await _stage_file(client, csrf)
        submitted = await _submit(client, csrf, staged_id)
        assert submitted.status_code == 201, submitted.text
        submission = submitted.json()

    staged = connection.execute(
        "SELECT status,claimed_submission_id,object_key FROM cfp_staged_assets WHERE id=?",
        (staged_id,),
    ).fetchone()
    assert staged["status"] == "claimed"
    assert staged["claimed_submission_id"] == submission["id"]
    asset = connection.execute(
        """SELECT a.id,a.kind,a.submission_id,v.object_key,v.scan_state,v.is_current
           FROM speaker_assets a JOIN speaker_asset_versions v ON v.asset_id=a.id"""
    ).fetchone()
    assert asset["kind"] == "supporting_document"
    assert asset["submission_id"] == submission["id"]
    assert asset["object_key"] == staged["object_key"]
    assert asset["scan_state"] == "clean"
    assert asset["is_current"] == 1
    stored_answers = json.loads(
        connection.execute(
            "SELECT answers_json FROM submissions WHERE id=?", (submission["id"],)
        ).fetchone()[0]
    )
    intent_reference = stored_answers["paper"]
    assert intent_reference.startswith("upload:")
    assert connection.execute(
        "SELECT consumed_at_ms FROM upload_intents WHERE id=?",
        (intent_reference.removeprefix("upload:"),),
    ).fetchone()[0] is not None
    for table in ("people", "event_speakers", "organization_memberships", "event_memberships"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table  # noqa: S608
    assert connection.execute(
        "SELECT status FROM event_memberships WHERE user_id=(SELECT id FROM users)"
    ).fetchone()[0] == "active"


async def test_staged_file_cannot_appear_in_a_second_proposal(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        staged_id = await _stage_file(client, csrf)
        first = await _submit(client, csrf, staged_id, title="Proposal A")
        assert first.status_code == 201, first.text
        second = await _submit(client, csrf, staged_id, title="Proposal B")
        assert second.status_code == 422, second.text
    assert connection.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 1


async def test_revoked_user_cannot_regain_access_through_staged_uploads(cfp_environment) -> None:
    connection, environment = cfp_environment
    now = 2_000_000
    connection.execute(
        """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES('revoked-user','speaker@example.test','speaker@example.test','active',?,?)""",
        (now, now),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,revoked_at_ms,created_at_ms,updated_at_ms)
           VALUES('revoked-org','org','revoked-user','member','revoked',?,?,?)""",
        (now, now, now),
    )
    connection.execute(
        """INSERT INTO event_memberships
           (id,organization_id,event_id,user_id,role,status,revoked_at_ms,
            created_at_ms,updated_at_ms)
           VALUES('revoked-event','org','event','revoked-user','speaker','revoked',?,?,?)""",
        (now, now, now),
    )
    connection.commit()
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        staged_id = await _stage_file(client, csrf)
        # Staging never touched the membership rows...
        memberships = connection.execute(
            "SELECT id,status FROM organization_memberships UNION ALL "
            "SELECT id,status FROM event_memberships"
        ).fetchall()
        assert {row["id"] for row in memberships} == {"revoked-org", "revoked-event"}
        assert {row["status"] for row in memberships} == {"revoked"}
        # ...and submitting cannot resurrect them: the membership insert is a
        # no-op on conflict, so the submission-owner integrity trigger aborts
        # the whole batch for a still-revoked submitter.
        submitted = await _submit(client, csrf, staged_id)
        assert submitted.status_code == 409, submitted.text

    memberships = connection.execute(
        "SELECT id,status FROM organization_memberships UNION ALL "
        "SELECT id,status FROM event_memberships"
    ).fetchall()
    assert {row["id"] for row in memberships} == {"revoked-org", "revoked-event"}
    assert {row["status"] for row in memberships} == {"revoked"}
    assert connection.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 0
    assert connection.execute(
        "SELECT status FROM cfp_staged_assets WHERE id=?", (staged_id,)
    ).fetchone()[0] == "staged"


async def test_editing_retains_existing_attachment_and_supports_replacement(
    cfp_environment,
) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        staged_id = await _stage_file(client, csrf)
        submitted = await _submit(client, csrf, staged_id)
        assert submitted.status_code == 201, submitted.text
        submission = submitted.json()
        original_reference = json.loads(
            connection.execute(
                "SELECT answers_json FROM submissions WHERE id=?", (submission["id"],)
            ).fetchone()[0]
        )["paper"]

        # An edit that does not re-attach a file must keep the stored upload.
        payload = _submission_payload(staged_id, title="Edited title")
        payload["answers"]["paper"] = ""
        payload["answers"]["proposal_title"] = "Edited title"
        payload["version"] = submission["version"]
        edited = await client.patch(
            f"/api/v1/forms/event-cfp/submissions/{submission['id']}",
            headers=_mutation_headers(csrf),
            json=payload,
        )
        assert edited.status_code == 200, edited.text
        kept = json.loads(
            connection.execute(
                "SELECT answers_json FROM submissions WHERE id=?", (submission["id"],)
            ).fetchone()[0]
        )
        assert kept["paper"] == original_reference
        assert kept["proposal_title"] == "Edited title"

        # Attaching a replacement claims the new staged file into the same slot.
        replacement_id = await _stage_file(
            client, csrf, body=b"%PDF-1.4 replacement", key="replacement-0123456789abcd"
        )
        payload["answers"]["paper"] = f"staged:{replacement_id}"
        payload["version"] = edited.json()["version"]
        replaced = await client.patch(
            f"/api/v1/forms/event-cfp/submissions/{submission['id']}",
            headers=_mutation_headers(csrf),
            json=payload,
        )
        assert replaced.status_code == 200, replaced.text

    final_reference = json.loads(
        connection.execute(
            "SELECT answers_json FROM submissions WHERE id=?", (submission["id"],)
        ).fetchone()[0]
    )["paper"]
    assert final_reference.startswith("upload:")
    assert final_reference != original_reference
    versions = connection.execute(
        """SELECT generation,scan_state,is_current FROM speaker_asset_versions
           ORDER BY generation"""
    ).fetchall()
    assert [tuple(row) for row in versions] == [(1, "superseded", 0), (2, "clean", 1)]
    assert connection.execute(
        "SELECT status,claimed_submission_id FROM cfp_staged_assets WHERE id=?",
        (replacement_id,),
    ).fetchone()["status"] == "claimed"
    assert connection.execute("SELECT COUNT(*) FROM speaker_assets").fetchone()[0] == 1


async def test_lost_concurrent_edit_cannot_claim_its_staged_file(cfp_environment) -> None:
    """Two tabs race a PATCH: the loser's zero-row UPDATE must abort the batch.

    Reproduces the interleaving directly at the batch layer, because over HTTP
    the early version check already rejects the loser once the winner has
    committed. The write guard is what protects the true in-flight race.
    """
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        staged_id = await _stage_file(client, csrf)
        submitted = await _submit(client, csrf, staged_id)
        assert submitted.status_code == 201, submitted.text
        submission_id = submitted.json()["id"]
        loser_staged_id = await _stage_file(
            client, csrf, body=b"%PDF-1.4 loser", key="loser-0123456789abcdefghij"
        )

    db = environment.DB
    user_id = connection.execute("SELECT id FROM users").fetchone()[0]
    event_speaker_id = connection.execute(
        "SELECT event_speaker_id FROM submission_speakers WHERE role='primary'"
    ).fetchone()[0]
    versions_before = connection.execute(
        "SELECT COUNT(*) FROM speaker_asset_versions"
    ).fetchone()[0]
    claim = await build_staged_claim(
        db,
        organization_id="org",
        event_id="event",
        event_speaker_id=event_speaker_id,
        submission_id=submission_id,
        form_id="form",
        user_id=user_id,
        staged_ids=[loser_staged_id],
        now=5_000_000,
    )
    stale_update = db.prepare(
        """UPDATE submissions SET proposal_title=?1,version=version+1,updated_at_ms=?2
           WHERE id=?3 AND submitter_user_id=?4 AND status='submitted' AND version=?5"""
    ).bind("Loser title", 5_000_000, submission_id, user_id, 999)
    write_guard = db.prepare(
        """INSERT INTO submission_write_guards
           (id,submission_id,applied_changes,created_at_ms)
           VALUES(?1,?2,changes(),?3)"""
    ).bind("loser-guard", submission_id, 5_000_000)

    with pytest.raises(Exception, match="applied_changes"):
        await db.batch([stale_update, write_guard, *claim.statements])

    # The whole batch rolled back: nothing claimed, no versions added.
    assert connection.execute(
        "SELECT status FROM cfp_staged_assets WHERE id=?", (loser_staged_id,)
    ).fetchone()[0] == "staged"
    assert connection.execute(
        "SELECT COUNT(*) FROM speaker_asset_versions"
    ).fetchone()[0] == versions_before
    assert connection.execute(
        "SELECT proposal_title FROM submissions WHERE id=?", (submission_id,)
    ).fetchone()[0] != "Loser title"

    # And the router wires the guard between the UPDATE and the claim.
    router = (PROJECT_ROOT / "src" / "sessionbuddy" / "cfp" / "router.py").read_text(
        encoding="utf-8"
    )
    assert "[update_statement, write_guard, *claim_statements]" in router


class _GatedD1(SQLiteD1):
    """SQLiteD1 that lets a test pause one request at its claim batch."""

    def __init__(self, connection) -> None:
        super().__init__(connection)
        self.row_read = None
        self.allow_claim_batch = None

    def prepare(self, sql: str):
        statement = super().prepare(sql)
        gate = self
        if "FROM submissions s JOIN call_for_speaker_forms" in sql and self.row_read is not None:
            original_first = statement.first

            async def first(column=None):
                result = await original_first(column)
                gate.row_read.set()
                return result

            statement.first = first
        return statement

    async def batch(self, statements):
        if self.allow_claim_batch is not None and any(
            "UPDATE submissions SET" in getattr(statement, "sql", "")
            for statement in statements
        ):
            await self.allow_claim_batch.wait()
        return await super().batch(statements)


async def test_two_tab_race_over_http_rolls_back_the_losing_claim(cfp_environment) -> None:
    """The full two-tab repro: both PATCHes read the same version before either
    commits; the loser's whole batch — including its staged claim — aborts."""
    import asyncio

    connection, environment_a = cfp_environment
    environment_b, _queue = _environment(connection, DB=_GatedD1(connection))
    async with _client(environment_a) as client_a, _client(environment_b) as client_b:
        csrf = await _sign_in(client_a, connection, "speaker@example.test")
        for cookie in client_a.cookies.jar:
            client_b.cookies.set(cookie.name, cookie.value, domain=cookie.domain)
        first_staged = await _stage_file(client_a, csrf)
        submitted = await _submit(client_a, csrf, first_staged)
        assert submitted.status_code == 201, submitted.text
        submission = submitted.json()
        staged_a = await _stage_file(
            client_a, csrf, body=b"%PDF-1.4 tab A", key="tab-a-0123456789abcdefghij"
        )
        staged_b = await _stage_file(
            client_b, csrf, body=b"%PDF-1.4 tab B", key="tab-b-0123456789abcdefghij"
        )

        environment_b.DB.row_read = asyncio.Event()
        environment_b.DB.allow_claim_batch = asyncio.Event()

        def payload(staged_id: str, title: str) -> dict[str, object]:
            body = _submission_payload(staged_id, title=title)
            body["version"] = submission["version"]  # both tabs saw the same version
            return body

        # Tab B reads the submission row first, then stalls before its batch.
        task_b = asyncio.create_task(
            client_b.patch(
                f"/api/v1/forms/event-cfp/submissions/{submission['id']}",
                headers=_mutation_headers(csrf),
                json=payload(staged_b, "Tab B title"),
            )
        )
        await asyncio.wait_for(environment_b.DB.row_read.wait(), 10)
        # Tab A commits while tab B is still in flight.
        response_a = await client_a.patch(
            f"/api/v1/forms/event-cfp/submissions/{submission['id']}",
            headers=_mutation_headers(csrf),
            json=payload(staged_a, "Tab A title"),
        )
        assert response_a.status_code == 200, response_a.text
        environment_b.DB.allow_claim_batch.set()
        response_b = await asyncio.wait_for(task_b, 10)
        assert response_b.status_code == 409, response_b.text

    stored = connection.execute(
        "SELECT proposal_title,answers_json FROM submissions WHERE id=?", (submission["id"],)
    ).fetchone()
    assert stored["proposal_title"] == "Tab A title"
    winner_reference = json.loads(stored["answers_json"])["paper"]
    assert winner_reference.startswith("upload:")
    assert connection.execute(
        "SELECT status,claimed_submission_id FROM cfp_staged_assets WHERE id=?", (staged_a,)
    ).fetchone()["status"] == "claimed"
    loser = connection.execute(
        "SELECT status,claimed_submission_id FROM cfp_staged_assets WHERE id=?", (staged_b,)
    ).fetchone()
    assert loser["status"] == "staged"
    assert loser["claimed_submission_id"] is None
    versions = connection.execute(
        "SELECT generation,scan_state,is_current FROM speaker_asset_versions ORDER BY generation"
    ).fetchall()
    assert [tuple(row) for row in versions] == [(1, "superseded", 0), (2, "clean", 1)]


async def test_hourly_authorization_quota_counts_claimed_rows(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        user_id = connection.execute("SELECT id FROM users").fetchone()[0]
        now = utc_now_ms() - 60_000
        for index in range(MAX_STAGED_AUTHORIZATIONS_PER_HOUR):
            connection.execute(
                """INSERT INTO cfp_staged_assets
                   (id,organization_id,event_id,form_id,user_id,kind,object_key,
                    original_filename,content_type,byte_size,checksum_sha256,
                    upload_token_hash,status,expires_at_ms,created_at_ms,updated_at_ms)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"seed-{index}",
                    "org",
                    "event",
                    "form",
                    user_id,
                    "supporting_document",
                    f"staged/seed-{index}/0123456789abcdef",
                    "seed.pdf",
                    "application/pdf",
                    10,
                    bytes([index]) * 32,
                    bytes([index, 255]) * 16,
                    "rejected",
                    now + 86_400_000,
                    now,
                    now,
                ),
            )
        connection.commit()
        body = b"%PDF-1.4 over-quota"
        denied = await client.post(
            "/api/v1/cfp/forms/form/upload-authorizations",
            headers=_mutation_headers(csrf),
            json={
                "kind": "supporting_document",
                "filename": "paper.pdf",
                "content_type": "application/pdf",
                "byte_size": len(body),
                "checksum_sha256": hashlib.sha256(body).hexdigest(),
            },
        )
        assert denied.status_code == 429, denied.text
        assert denied.headers.get("retry-after") == "3600"
    assert connection.execute(
        "SELECT COUNT(*) FROM cfp_staged_assets"
    ).fetchone()[0] == MAX_STAGED_AUTHORIZATIONS_PER_HOUR


async def test_staged_endpoints_use_separate_rate_limit_buckets(cfp_environment) -> None:
    connection, _base_environment = cfp_environment

    class DenyingRateLimiter:
        async def limit(self, options: dict[str, str]) -> dict[str, bool]:
            assert options["key"]
            return {"success": False}

    # Authorization throttled by its own binding.
    environment, _queue = _environment(
        connection, CFP_UPLOAD_AUTH_RATE_LIMITER=DenyingRateLimiter()
    )
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        body = b"%PDF-1.4 throttled"
        denied = await client.post(
            "/api/v1/cfp/forms/form/upload-authorizations",
            headers=_mutation_headers(csrf),
            json={
                "kind": "supporting_document",
                "filename": "paper.pdf",
                "content_type": "application/pdf",
                "byte_size": len(body),
                "checksum_sha256": hashlib.sha256(body).hexdigest(),
            },
        )
        assert denied.status_code == 429
        assert connection.execute("SELECT COUNT(*) FROM cfp_staged_assets").fetchone()[0] == 0

    # Completion polling uses the (more generous) polling binding.
    environment, _queue = _environment(
        connection, CFP_UPLOAD_POLL_RATE_LIMITER=DenyingRateLimiter()
    )
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        polled = await client.post(
            "/api/v1/cfp/forms/form/upload-authorizations/missing/complete",
            headers=_mutation_headers(csrf),
            json={},
        )
        assert polled.status_code == 429


async def test_scan_gating_honors_malware_scan_mode(cfp_environment) -> None:
    connection, _local_environment = cfp_environment
    scan_queue = CapturingQueue()
    bucket = FakeBucket()
    environment, _queue = _environment(
        connection,
        APP_ENV="production",
        MALWARE_SCAN_MODE="required",
        ASSETS=bucket,
        ASSET_SCAN_QUEUE=scan_queue,
        CLOUDFLARE_ACCOUNT_ID="a" * 32,
        R2_BUCKET_NAME="assets",
        R2_ACCESS_KEY_ID="key",
        R2_SECRET_ACCESS_KEY="secret-value",  # noqa: S106
    )
    body = b"%PDF-1.4 scanned"
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        authorized = await client.post(
            "/api/v1/cfp/forms/form/upload-authorizations",
            headers=_mutation_headers(csrf),
            json={
                "kind": "supporting_document",
                "filename": "paper.pdf",
                "content_type": "application/pdf",
                "byte_size": len(body),
                "checksum_sha256": hashlib.sha256(body).hexdigest(),
            },
        )
        assert authorized.status_code == 201, authorized.text
        authorization = authorized.json()
        assert authorization["upload_url"].startswith("https://")
        staged_id = authorization["staged_id"]
        object_key = connection.execute(
            "SELECT object_key FROM cfp_staged_assets WHERE id=?", (staged_id,)
        ).fetchone()[0]
        await bucket.put(object_key, body)
        completed = await client.post(
            f"/api/v1/cfp/forms/form/upload-authorizations/{staged_id}/complete",
            headers=_mutation_headers(csrf),
            json={},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["state"] == "uploaded"
        assert len(scan_queue.messages) == 1

        class CleanScanner:
            async def scan(self, stored, *, job: ScanJob) -> ScanResult:
                return ScanResult(
                    provider_event_id=f"event:{job.job_id}", verdict="clean", engine="clamav"
                )

        disposition = await consume_scan_job(
            environment.DB, bucket, CleanScanner(), scan_queue.messages[0], now_ms=3_000_000
        )
        assert disposition.ack and disposition.reason == "clean"
        assert connection.execute(
            "SELECT status,scan_result_code FROM cfp_staged_assets WHERE id=?", (staged_id,)
        ).fetchone()["status"] == "staged"

        polled = await client.post(
            f"/api/v1/cfp/forms/form/upload-authorizations/{staged_id}/complete",
            headers=_mutation_headers(csrf),
            json={},
        )
        assert polled.json()["state"] == "staged"


async def test_malicious_staged_upload_is_rejected_by_the_async_scan(cfp_environment) -> None:
    connection, _local_environment = cfp_environment
    scan_queue = CapturingQueue()
    bucket = FakeBucket()
    environment, _queue = _environment(
        connection,
        APP_ENV="production",
        MALWARE_SCAN_MODE="required",
        ASSETS=bucket,
        ASSET_SCAN_QUEUE=scan_queue,
        CLOUDFLARE_ACCOUNT_ID="a" * 32,
        R2_BUCKET_NAME="assets",
        R2_ACCESS_KEY_ID="key",
        R2_SECRET_ACCESS_KEY="secret-value",  # noqa: S106
    )
    body = b"%PDF-1.4 malicious"
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        authorized = await client.post(
            "/api/v1/cfp/forms/form/upload-authorizations",
            headers=_mutation_headers(csrf),
            json={
                "kind": "supporting_document",
                "filename": "paper.pdf",
                "content_type": "application/pdf",
                "byte_size": len(body),
                "checksum_sha256": hashlib.sha256(body).hexdigest(),
            },
        )
        staged_id = authorized.json()["staged_id"]
        object_key = connection.execute(
            "SELECT object_key FROM cfp_staged_assets WHERE id=?", (staged_id,)
        ).fetchone()[0]
        await bucket.put(object_key, body)
        completed = await client.post(
            f"/api/v1/cfp/forms/form/upload-authorizations/{staged_id}/complete",
            headers=_mutation_headers(csrf),
            json={},
        )
        assert completed.json()["state"] == "uploaded"

        class MaliciousScanner:
            async def scan(self, stored, *, job: ScanJob) -> ScanResult:
                return ScanResult(
                    provider_event_id=f"event:{job.job_id}",
                    verdict="malicious",
                    engine="clamav",
                    signature_code="Eicar-Test",
                )

        await consume_scan_job(
            environment.DB, bucket, MaliciousScanner(), scan_queue.messages[0], now_ms=3_000_000
        )
        row = connection.execute(
            "SELECT status,scan_result_code FROM cfp_staged_assets WHERE id=?", (staged_id,)
        ).fetchone()
        assert row["status"] == "rejected"
        assert row["scan_result_code"] == "Eicar-Test"

        submitted = await _submit(client, csrf, staged_id)
        assert submitted.status_code == 422


async def test_expiry_purges_rows_and_r2_objects(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        abandoned_id = await _stage_file(client, csrf, key="abandoned-0123456789abcdef")
        claimed_id = await _stage_file(
            client, csrf, body=b"%PDF-1.4 kept", key="claimed-0123456789abcdefgh"
        )
        submitted = await _submit(client, csrf, claimed_id)
        assert submitted.status_code == 201, submitted.text

    bucket = environment.ASSETS
    connection.execute("UPDATE cfp_staged_assets SET expires_at_ms=created_at_ms+1")
    connection.commit()
    abandoned_key, claimed_key = (
        connection.execute(
            "SELECT object_key FROM cfp_staged_assets WHERE id=?", (staged_id,)
        ).fetchone()[0]
        for staged_id in (abandoned_id, claimed_id)
    )
    result = await purge_expired_staged_assets(
        environment.DB, bucket, 9_999_999_999_999, limit=50
    )
    assert result.deleted_rows == 2
    assert result.deleted_objects == 1
    assert result.delete_failures == 0
    assert connection.execute("SELECT COUNT(*) FROM cfp_staged_assets").fetchone()[0] == 0
    assert abandoned_key in bucket.deleted
    assert abandoned_key not in bucket.objects
    # The claimed object now belongs to the speaker-asset graph and survives.
    assert claimed_key in bucket.objects


async def test_purge_retries_rows_when_object_delete_fails(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        csrf = await _sign_in(client, connection, "speaker@example.test")
        await _stage_file(client, csrf)
    connection.execute("UPDATE cfp_staged_assets SET expires_at_ms=created_at_ms+1")
    connection.commit()
    bucket = environment.ASSETS
    bucket.fail_deletes = True
    result = await purge_expired_staged_assets(environment.DB, bucket, 9_999_999_999_999)
    assert result.delete_failures == 1
    assert result.deleted_rows == 0
    assert connection.execute("SELECT COUNT(*) FROM cfp_staged_assets").fetchone()[0] == 1


async def test_magic_link_get_renders_confirmation_without_consuming(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        requested = await client.post(
            "/api/v1/auth/magic-links",
            json={
                "email": "speaker@example.test",
                "form_slug": "event-cfp",
                "redirect_path": "/cfp/event-cfp",
            },
        )
        assert requested.status_code == 202
        token = _magic_token(connection, "speaker@example.test")

        page = await client.get(f"/auth/verify?token={token}")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert "<style>" not in page.text
        assert '<link rel="stylesheet" href="/product/assets/product.css' in page.text
        assert 'method="post"' in page.text
        assert "Create your speaker account" in page.text
        assert 'name="first_name"' in page.text
        assert 'name="password_confirmation"' in page.text
        assert connection.execute(
            "SELECT consumed_at_ms FROM authentication_challenges"
        ).fetchone()[0] is None

        incomplete = await client.post(f"/auth/verify?token={token}", follow_redirects=False)
        assert incomplete.status_code == 422
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

        confirmed = await client.post(
            f"/auth/verify?token={token}",
            data={
                "first_name": "Test",
                "last_name": "Speaker",
                "password": "a private speaker passphrase",
                "password_confirmation": "a private speaker passphrase",
            },
            follow_redirects=False,
        )
        assert confirmed.status_code == 303
        assert confirmed.headers["location"] == "/cfp/event-cfp"
        assert connection.execute(
            "SELECT consumed_at_ms FROM authentication_challenges"
        ).fetchone()[0] is not None

        replayed = await client.post(f"/auth/verify?token={token}", follow_redirects=False)
        assert replayed.status_code == 404


async def test_existing_speaker_magic_link_auto_continues(cfp_environment) -> None:
    connection, environment = cfp_environment
    async with _client(environment) as client:
        await _sign_in(client, connection, "returning-speaker@example.test")
        requested = await client.post(
            "/api/v1/auth/magic-links",
            json={
                "email": "returning-speaker@example.test",
                "form_slug": "event-cfp",
                "redirect_path": "/cfp/event-cfp",
            },
        )
        assert requested.status_code == 202
        token = _magic_token(connection, "returning-speaker@example.test")

        page = await client.get(f"/auth/verify?token={token}")
        assert page.status_code == 200
        assert "Signing you in…" in page.text
        assert 'data-auto-submit="true"' in page.text
        assert 'auth-link-confirm.js?v=2' in page.text
        assert "Create your speaker account" not in page.text
        assert connection.execute(
            "SELECT consumed_at_ms FROM authentication_challenges "
            "WHERE normalized_email=? ORDER BY created_at_ms DESC LIMIT 1",
            ("returning-speaker@example.test",),
        ).fetchone()[0] is None


async def test_passwordless_speaker_must_finish_registration(cfp_environment) -> None:
    connection, environment = cfp_environment
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES(?,?,?,?,?,?,?)""",
        (
            "legacy-passwordless-speaker",
            "legacy-speaker@example.test",
            "legacy-speaker@example.test",
            "active",
            now,
            now,
            now,
        ),
    )
    connection.commit()

    async with _client(environment) as client:
        requested = await client.post(
            "/api/v1/auth/magic-links",
            json={
                "email": "legacy-speaker@example.test",
                "form_slug": "event-cfp",
                "redirect_path": "/cfp/event-cfp",
            },
        )
        assert requested.status_code == 202
        token = _magic_token(connection, "legacy-speaker@example.test")

        page = await client.get(f"/auth/verify?token={token}")
        assert page.status_code == 200
        assert "Create your speaker account" in page.text
        assert 'name="password_confirmation"' in page.text
        assert "Signing you in…" not in page.text

        completed = await client.post(
            f"/auth/verify?token={token}",
            data={
                "first_name": "Legacy",
                "last_name": "Speaker",
                "job_title": "Engineer",
                "company": "Example",
                "password": "a private speaker passphrase",
                "password_confirmation": "a private speaker passphrase",
            },
            follow_redirects=False,
        )
        assert completed.status_code == 303
        assert completed.headers["location"] == "/cfp/event-cfp"

    profile = connection.execute(
        "SELECT first_name,last_name,profile_completed_at_ms FROM users WHERE id=?",
        ("legacy-passwordless-speaker",),
    ).fetchone()
    assert profile[0:2] == ("Legacy", "Speaker")
    assert profile[2] is not None
    assert connection.execute(
        "SELECT COUNT(*) FROM password_credentials WHERE user_id=? AND status='active'",
        ("legacy-passwordless-speaker",),
    ).fetchone()[0] == 1


async def test_local_https_magic_link_is_queued_for_local_mail_inbox(cfp_environment) -> None:
    connection, environment = cfp_environment
    environment.PUBLIC_BASE_URL = "https://localhost:8443"

    async with _client(environment) as client:
        requested = await client.post(
            "/api/v1/auth/magic-links",
            json={
                "email": "local-speaker@example.test",
                "form_slug": "event-cfp",
                "redirect_path": "/cfp/event-cfp",
            },
        )

    assert requested.status_code == 202
    message = connection.execute(
        "SELECT html_body,status FROM communication_messages WHERE recipient_email=?",
        ("local-speaker@example.test",),
    ).fetchone()
    assert message is not None
    assert 'href="https://localhost:8443/auth/verify?token=' in message[0]
    assert message[1] == "queued"


def test_magic_link_confirmation_page_is_packaged_and_csp_safe() -> None:
    page = (STATIC / "auth_link_confirm.html").read_text(encoding="utf-8")
    access = (
        PROJECT_ROOT / "src" / "sessionbuddy" / "platform" / "auth" / "access.py"
    ).read_text(encoding="utf-8")

    assert "<style>" not in page
    assert 'style="' not in page
    assert '__CONFIRM_ACTION__' in page
    assert '__REGISTRATION_FIELDS__' in page
    assert '__AUTO_SUBMIT_ATTRIBUTE__' in page
    assert '__CONFIRM_SCRIPT__' in page
    assert '<meta name="robots" content="noindex">' in page
    assert '_asset("auth_link_confirm.html")' in access
    assert 'data-auto-submit="true"' in access
    assert 'auth-link-confirm.js?v=2' in access
    interstitial = access.split("magic_link_interstitial", 1)[1].split("@access_router", 1)[0]
    assert "<style>" not in interstitial
    # Consume-on-success: a failed sign-in restores the challenge it consumed.
    assert "SET consumed_at_ms=NULL" in access


def test_provisioning_fallback_is_fully_reverted() -> None:
    router = (
        PROJECT_ROOT / "src" / "sessionbuddy" / "speaker_operations" / "router.py"
    ).read_text(encoding="utf-8")
    cfp = (PROJECT_ROOT / "src" / "sessionbuddy" / "cfp" / "router.py").read_text(encoding="utf-8")

    assert "_provision_cfp_uploader" not in router
    # Submission-time provisioning must never resurrect a revoked membership.
    create = cfp.split("async def create_submission", 1)[1].split("SUBMISSIONS_PAGE_LIMIT", 1)[0]
    statements = re.findall(
        r"INSERT INTO (?:organization|event)_memberships[\s\S]{0,400}?ON CONFLICT[^\"]+", create
    )
    assert len(statements) == 2
    for statement in statements:
        assert "DO NOTHING" in statement
        assert "revoked_at_ms=NULL" not in statement


def test_public_cfp_resets_file_state_between_proposals() -> None:
    script = (STATIC / "public_cfp.js").read_text(encoding="utf-8")

    assert "function resetProposalFiles()" in script
    # Opening/restoring a proposal and closing the completed form both reset
    # per-proposal file state.
    assert script.count("resetProposalFiles();") >= 2
    choose = script.split("function chooseSubmission", 1)[1].split("function ", 1)[0]
    assert "resetProposalFiles();" in choose
    submit_success = script.split("clearBrowserDraft();", 1)[1].split("} catch", 1)[0]
    assert "resetProposalFiles();" in submit_success
    assert "state.existingFiles" in script
    assert '"Existing file attached"' in script
    assert 'control.required = control.dataset.required === "true"' in script


def test_public_cfp_uploads_through_the_staged_endpoint() -> None:
    script = (STATIC / "public_cfp.js").read_text(encoding="utf-8")
    page = (STATIC / "public_cfp.html").read_text(encoding="utf-8")

    assert "/api/v1/cfp/forms/${encodeURIComponent(state.form.id)}/upload-authorizations" in script
    assert "staged:${authorization.staged_id}" in script
    assert "/api/v1/speaker/events/" not in script.split("function uploadAnswer", 1)[1].split(
        "async function uploadFiles", 1
    )[0]
    assert "public-cfp.js?v=24" in page


def test_sbek_helper_completes_the_confirmation_page() -> None:
    launcher = (PROJECT_ROOT / "scripts" / "run_sbek.sh").read_text(encoding="utf-8")
    helper = (PROJECT_ROOT / "scripts" / "sbek_auth_link.mjs").read_text(encoding="utf-8")

    assert "auth-link" in launcher
    assert "sbek_auth_link.mjs" in launcher
    assert 'form[action^="/auth/verify"]' in helper
    assert "storageState" in helper


def test_run_sbek_auth_explains_the_sessionbuddy_magic_link_capture(tmp_path: Path) -> None:
    eval_root = tmp_path / "eval"
    (eval_root / "src").mkdir(parents=True)
    (eval_root / "src" / "cli.ts").write_text("", encoding="utf-8")
    (eval_root / "package.json").write_text("{}", encoding="utf-8")

    target = "https://sessionbuddy-development.example.test"
    environment = {
        **os.environ,
        "SBEK_ROOT": str(eval_root),
        "SBEK_TARGET_URL": target,
    }

    completed = subprocess.run(  # noqa: S603 - fixed repository script under test
        [str(PROJECT_ROOT / "scripts" / "run_sbek.sh"), "auth", "organizer"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"{target}/sign-in" in completed.stderr
    assert "scripts/run_sbek.sh auth-link organizer '<magic-link-url>'" in completed.stderr


def test_sbek_launcher_matches_the_current_persona_contract() -> None:
    launcher = (PROJECT_ROOT / "scripts" / "run_sbek.sh").read_text(encoding="utf-8")
    checker = (PROJECT_ROOT / "scripts" / "check_sbek_sessions.mjs").read_text(
        encoding="utf-8"
    )

    assert 'required_personas="organizer speaker reviewer"' in launcher
    assert "check_sbek_config.mjs" in launcher
    assert (
        'dependencies_volume="${SBEK_NODE_MODULES_VOLUME:-sessionbuddy-sbek-node-modules-v2}"'
        in launcher
    )
    assert (
        'store_volume="${SBEK_PNPM_STORE_VOLUME:-sessionbuddy-sbek-pnpm-store-v2}"'
        in launcher
    )
    installer = (PROJECT_ROOT / "scripts" / "install_sbek_dependencies.sh").read_text(
        encoding="utf-8"
    )
    assert "node_modules/.sessionbuddy-sbek-lock.sha256" in installer
    assert "corepack pnpm install --frozen-lockfile --ignore-scripts" in installer
    assert "--store-dir=/pnpm-store" in installer
    assert '-v "$dependencies_volume:/eval/node_modules"' in launcher
    assert "corepack pnpm exec tsx" in launcher
    assert '--paste-link "$@"' not in launcher
    assert 'if [ "${1:-}" = "--reuse" ]' not in launcher
    assert "body?.account_roles" in checker
    assert "body?.active_role" in checker
    assert "item.permissions" in checker
    assert "organization_admin" not in checker
    assert "event_admin" not in checker
    assert "evaluator" not in checker


def test_sbek_config_preflight_requires_real_inboxes_and_switch_credentials(
    tmp_path: Path,
) -> None:
    target = "https://sessionbuddy-development.example.test"
    config = {
        "url": target,
        "personaEmails": {
            "organizer": "qa+organizer@example.test",
            "speaker": "qa+speaker@example.test",
            "speaker2": "qa+speaker2@example.test",
            "reviewer": "qa+reviewer@example.test",
        },
        "credentials": {
            "organizer": {
                "email": "qa+organizer@example.test",
                "password": "fixture-organizer-password",
            },
            "speaker": {
                "email": "qa+speaker@example.test",
                "password": "fixture-speaker-password",
            },
            "reviewer": {
                "email": "qa+reviewer@example.test",
                "password": "fixture-reviewer-password",
            },
        },
    }
    config_path = tmp_path / "evalconfig.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    checker = PROJECT_ROOT / "scripts" / "check_sbek_config.mjs"
    node = shutil.which("node")
    assert node is not None

    valid = subprocess.run(  # noqa: S603 - fixed repository script under test
        [node, str(checker), str(tmp_path), target],
        check=False,
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0, valid.stderr
    assert "fixture-organizer-password" not in valid.stdout

    config["personaEmails"]["reviewer"] = "replace-me@example.invalid"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    invalid = subprocess.run(  # noqa: S603 - fixed repository script under test
        [node, str(checker), str(tmp_path), target],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 2
    assert "reviewer needs an inbox address you control" in invalid.stderr
    assert "fixture-reviewer-password" not in invalid.stderr
