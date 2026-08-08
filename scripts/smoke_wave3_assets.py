"""Exercise the private speaker-asset lifecycle through a running local Worker."""

import argparse
import asyncio
import hashlib
import uuid
from urllib.parse import urljoin

from httpx import AsyncClient


async def run(base_url: str) -> None:
    origin = "http://localhost:8787"
    async with AsyncClient(base_url=base_url.rstrip("/"), timeout=30) as client:
        session = await client.post("/api/v1/demo/speaker-session")
        session.raise_for_status()
        csrf = session.json()["csrf_token"]
        portal = await client.get("/api/v1/speaker/portal")
        portal.raise_for_status()
        event_id = portal.json()["event"]["id"]
        submission_id = portal.json()["submissions"][0]["id"]
        async def upload_asset(
            *, kind: str, filename: str, content_type: str, content: bytes
        ) -> str:
            authorization = await client.post(
                f"/api/v1/speaker/events/{event_id}/upload-authorizations",
                headers={
                    "content-type": "application/json",
                    "origin": origin,
                    "x-csrf-token": csrf,
                    "idempotency-key": str(uuid.uuid4()),
                },
                json={
                    "kind": kind,
                    "submission_id": submission_id,
                    "task_id": None,
                    "filename": filename,
                    "content_type": content_type,
                    "byte_size": len(content),
                    "checksum_sha256": hashlib.sha256(content).hexdigest(),
                },
            )
            authorization.raise_for_status()
            upload = authorization.json()
            stored = await client.put(
                urljoin(f"{base_url.rstrip('/')}/", upload["upload_url"]),
                headers=upload["headers"],
                content=content,
            )
            stored.raise_for_status()
            completed = await client.post(
                f"/api/v1/speaker/events/{event_id}/upload-intents/"
                f"{upload['intent_id']}/complete",
                headers={
                    "content-type": "application/json",
                    "origin": origin,
                    "x-csrf-token": csrf,
                    "idempotency-key": str(uuid.uuid4()),
                },
                json={},
            )
            completed.raise_for_status()
            return str(completed.json()["state"])

        clean = b"\x89PNG\r\n\x1a\nSessionBuddy local asset smoke"
        clean_state = await upload_asset(
            kind="headshot", filename="speaker.png", content_type="image/png", content=clean
        )
        eicar = (
            b"X5O!P%@AP[4"
            + bytes([92])
            + b"PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        )
        rejected_state = await upload_asset(
            kind="supporting_document",
            filename="scanner-test.pdf",
            content_type="application/pdf",
            content=eicar,
        )
        assets = await client.get(f"/api/v1/speaker/events/{event_id}/assets")
        assets.raise_for_status()
        rows = assets.json()["data"]
        if clean_state != "clean":
            raise RuntimeError("ClamAV did not promote the clean asset")
        if rejected_state != "rejected":
            raise RuntimeError("ClamAV did not reject the EICAR test file")
        if not any(row["filename"] == "speaker.png" for row in rows):
            raise RuntimeError("promoted asset is missing from the safe metadata list")
        if any(row["filename"] == "scanner-test.pdf" for row in rows):
            raise RuntimeError("rejected asset leaked into the safe metadata list")
        print("Wave 3 asset smoke passed: clean promoted; EICAR rejected and quarantined")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    asyncio.run(run(args.base_url))


if __name__ == "__main__":
    main()
