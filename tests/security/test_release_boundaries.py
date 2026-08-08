import ast
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app

ROOT = Path(__file__).resolve().parents[2]


def test_worker_queue_entrypoint_accepts_cloudflare_runtime_arguments() -> None:
    module = ast.parse((ROOT / "src" / "entry.py").read_text())
    default_class = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "Default"
    )
    queue_handler = next(
        node
        for node in default_class.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "queue"
    )

    assert [argument.arg for argument in queue_handler.args.args] == [
        "self",
        "batch",
        "_environment",
        "_context",
    ]


def test_r2_scanner_adapter_uses_fixed_length_stream_not_full_body_buffering() -> None:
    source = (
        ROOT / "src" / "sessionbuddy" / "speaker_operations" / "scanner_adapter.py"
    ).read_text()

    assert "FixedLengthStream" in source
    assert "pipeTo" in source
    assert "arrayBuffer" not in source


@pytest.mark.asyncio
async def test_missing_environment_never_enables_privileged_demo_sessions() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin = await client.post("/api/v1/demo/session")
        speaker = await client.post("/api/v1/demo/speaker-session")
        agenda = await client.post("/api/v1/demo/agenda-context?event_id=event")
    assert {admin.status_code, speaker.status_code, agenda.status_code} == {404}


@pytest.mark.asyncio
async def test_missing_environment_never_enables_local_private_upload_capability() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/uploads/intent/content?token=attacker",
            content=b"private",
            headers={"content-type": "application/pdf"},
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"
