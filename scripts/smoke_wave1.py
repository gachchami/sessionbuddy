"""Exercise the Wave 1 CFP journey through a running local Worker."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener


def call(opener, base_url: str, path: str, *, method: str = "GET", body=None, headers=None):
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base URL must use HTTP or HTTPS")
    encoded = json.dumps(body).encode() if body is not None else None
    request = Request(  # noqa: S310 - scheme is allow-listed above
        f"{base_url.rstrip('/')}{path}",
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with opener.open(request, timeout=30) as response:  # noqa: S310 - validated above
        if response.status == 204:
            return response.status, None
        return response.status, json.load(response)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def rejected_status(
    opener, base_url: str, path: str, *, method: str = "POST", body=None, headers=None
) -> int:
    try:
        call(opener, base_url, path, method=method, body=body, headers=headers)
    except HTTPError as error:
        return error.code
    raise RuntimeError(f"request unexpectedly succeeded: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    anonymous = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    initial_context = {
        "organization_id": "11111111-1111-4111-8111-111111111111",
        "event_id": "22222222-2222-4222-8222-222222222222",
        "name": "Unauthorized synthetic program",
    }
    expect(
        rejected_status(
            anonymous,
            args.base_url,
            "/api/v1/admin/programs",
            body=initial_context,
            headers={"Origin": "http://localhost:8787", "Idempotency-Key": f"anon-{run_id}"},
        )
        == 401,
        "anonymous admin request did not fail closed",
    )

    status, session = call(opener, args.base_url, "/api/v1/demo/session", method="POST")
    expect(status == 200, "demo session failed")
    current_status, current = call(opener, args.base_url, "/api/v1/session")
    expect(
        current_status == 200 and current["session_id"] and current["csrf_token"],
        "session introspection failed",
    )
    context = {
        "organization_id": session["organization_id"],
        "event_id": session["event_id"],
    }
    admin = {
        "Origin": "http://localhost:8787",
        "X-CSRF-Token": session["csrf_token"],
    }
    expect(
        rejected_status(
            opener,
            args.base_url,
            "/api/v1/admin/programs",
            body={**context, "name": "Missing CSRF"},
            headers={"Origin": "http://localhost:8787", "Idempotency-Key": f"csrf-{run_id}"},
        )
        == 403,
        "missing CSRF proof was accepted",
    )
    expect(
        rejected_status(
            opener,
            args.base_url,
            "/api/v1/admin/programs",
            body={
                "organization_id": "99999999-9999-4999-8999-999999999999",
                "event_id": context["event_id"],
                "name": "Cross tenant",
            },
            headers={**admin, "Idempotency-Key": f"tenant-{run_id}"},
        )
        == 404,
        "cross-tenant identifier was not hidden",
    )
    status, program = call(
        opener,
        args.base_url,
        "/api/v1/admin/programs",
        method="POST",
        headers={**admin, "Idempotency-Key": f"program-{run_id}"},
        body={**context, "name": f"Wave 1 smoke {run_id[:8]}"},
    )
    expect(status == 201, "program creation failed")
    slug = f"wave1-smoke-{run_id}"
    status, form = call(
        opener,
        args.base_url,
        f"/api/v1/admin/programs/{program['id']}/forms/publish",
        method="POST",
        headers={**admin, "Idempotency-Key": f"publish-{run_id}"},
        body={"slug": slug, "welcome_text": "Synthetic local compatibility check."},
    )
    expect(status == 201 and form["version"] == 1, "form publication failed")
    submission_headers = {
        "Idempotency-Key": f"submission-{run_id}",
        "X-Public-Session-ID": f"public-{run_id}",
    }
    submission_body = {
        "speaker_name": "Synthetic Speaker",
        "proposal_title": "Observable Workers",
        "proposal_abstract": "Workerd, Pyodide, FastAPI, and D1 compatibility check.",
    }
    status, submission = call(
        opener,
        args.base_url,
        f"/api/v1/forms/{slug}/submissions",
        method="POST",
        headers=submission_headers,
        body=submission_body,
    )
    expect(status == 201, "submission failed")
    replay_status, replay = call(
        opener,
        args.base_url,
        f"/api/v1/forms/{slug}/submissions",
        method="POST",
        headers=submission_headers,
        body=submission_body,
    )
    expect(
        replay_status == 201 and replay["id"] == submission["id"],
        "submission replay was not idempotent",
    )
    status, listing = call(
        opener,
        args.base_url,
        f"/api/v1/admin/programs/{program['id']}/submissions",
    )
    expect(
        status == 200 and [item["id"] for item in listing["data"]] == [submission["id"]],
        "admin submission list did not contain exactly the created submission",
    )
    rotation_status, rotated = call(
        opener,
        args.base_url,
        "/api/v1/session/refresh",
        method="POST",
        body={},
        headers=admin,
    )
    expect(
        rotation_status == 200 and rotated["session_id"] != current["session_id"],
        "session rotation failed",
    )
    admin["X-CSRF-Token"] = rotated["csrf_token"]
    logout_status, _ = call(
        opener,
        args.base_url,
        "/api/v1/session/logout",
        method="POST",
        body={},
        headers=admin,
    )
    expect(logout_status == 204, "logout failed")
    expect(
        rejected_status(opener, args.base_url, "/api/v1/session", method="GET") == 401,
        "revoked session remained usable after logout",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "program_id": program["id"],
                "submission_id": submission["id"],
            }
        )
    )


if __name__ == "__main__":
    main()
