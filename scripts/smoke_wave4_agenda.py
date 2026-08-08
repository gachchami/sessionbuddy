#!/usr/bin/env python3
# ruff: noqa: S101,S310
"""Exercise the Wave 4 agenda journey against a running Worker."""

import argparse
import json
import uuid
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

EVENT_ID = "22222222-2222-4222-8222-222222222222"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(path: str, *, method: str = "GET", body=None, csrf=None, key=None):
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.update({"Content-Type": "application/json", "Origin": "http://localhost:8787"})
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if key:
            headers["Idempotency-Key"] = key
        request = Request(args.base_url + path, data=data, headers=headers, method=method)
        with opener.open(request) as response:
            return response.status, json.loads(response.read())

    call("/api/v1/demo/speaker-session", method="POST", body={})
    _, admin = call("/api/v1/demo/session", method="POST", body={})
    csrf = admin["csrf_token"]
    call(
        f"/api/v1/demo/agenda-context?event_id={EVENT_ID}",
        method="POST",
        body={},
        csrf=csrf,
    )
    _, agenda = call(f"/api/v1/admin/events/{EVENT_ID}/agenda")
    assert agenda["rooms"]
    existing = agenda["items"][0] if agenda["items"] else None
    start = existing["start_at_ms"] if existing else agenda["event"]["starts_at_ms"] + 3_600_000
    candidate = {
        "item_id": existing["id"] if existing else None,
        "session_id": (
            existing["session_id"] if existing else agenda["unscheduled_sessions"][0]["session_id"]
        ),
        "start_at_ms": start,
        "end_at_ms": existing["end_at_ms"] if existing else start + 45 * 60_000,
        "room_id": existing["room_id"] if existing else agenda["rooms"][0]["id"],
        "track_id": existing["track_id"] if existing else agenda["tracks"][0]["id"],
        "version": existing["version"] if existing else 0,
    }
    _, preview = call(
        f"/api/v1/admin/events/{EVENT_ID}/agenda/preview",
        method="POST",
        body=candidate,
        csrf=csrf,
    )
    assert preview == {"valid": True, "conflicts": []}
    if existing:
        item = existing
    else:
        _, item = call(
            f"/api/v1/admin/events/{EVENT_ID}/agenda/items",
            method="POST",
            body=candidate,
            csrf=csrf,
            key=str(uuid.uuid4()),
        )
    stale = dict(
        candidate,
        item_id=item["id"],
        version=max(0, item["version"] - 1),
        room_id=agenda["rooms"][-1]["id"],
    )
    try:
        call(
            f"/api/v1/admin/events/{EVENT_ID}/agenda/items/{item['id']}",
            method="PATCH",
            body=stale,
            csrf=csrf,
            key=str(uuid.uuid4()),
        )
    except HTTPError as error:
        assert error.code == 409
    else:
        raise AssertionError("stale agenda move was accepted")
    call(
        f"/api/v1/admin/events/{EVENT_ID}/agenda/publish",
        method="POST",
        body={"revision_id": agenda["revision"]["id"], "version": agenda["revision"]["version"]},
        csrf=csrf,
    )
    _, schedule = call(f"/api/v1/events/{EVENT_ID}/schedule")
    assert len(schedule["items"]) == 1
    print("Wave 4 smoke passed: preview, atomic save, stale rejection, publish, schedule read")


if __name__ == "__main__":
    main()
