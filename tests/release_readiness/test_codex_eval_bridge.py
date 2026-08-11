import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).parents[2]
BRIDGE = PROJECT_ROOT / "scripts" / "codex_eval_bridge.mjs"
LAUNCHER = PROJECT_ROOT / "scripts" / "run_sbek.sh"


def _fake_codex(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
if (args[0] === "login" && args[1] === "status") {
  process.stdout.write("Logged in using ChatGPT\\n");
  process.exit(0);
}
let input = "";
for await (const chunk of process.stdin) input += chunk;
const outputIndex = args.indexOf("-o");
if (outputIndex < 0 || !args[outputIndex + 1]) process.exit(2);
fs.writeFileSync(args[outputIndex + 1], JSON.stringify({ action: "done" }));
const ownDir = path.dirname(fileURLToPath(import.meta.url));
fs.writeFileSync(path.join(ownDir, "codex-record.json"), JSON.stringify({
  args,
  cwd: process.cwd(),
  envKeys: Object.keys(process.env).sort(),
  input,
}));
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


@contextmanager
def _running_bridge(tmp_path: Path) -> Iterator[tuple[str, str, Path, Path]]:
    eval_root = tmp_path / "eval"
    eval_root.mkdir()
    image = eval_root / "screen.png"
    image.write_bytes(b"synthetic-image")
    fake_codex = _fake_codex(tmp_path / "fake-codex.mjs")
    token_file = tmp_path / "token"
    ready_file = tmp_path / "ready.json"
    environment = {**os.environ, "SBEK_CODEX_BIN": str(fake_codex)}
    node = shutil.which("node")
    assert node is not None
    process = subprocess.Popen(  # noqa: S603 - fixed Node binary and repository script
        [
            node,
            str(BRIDGE),
            "--eval-root",
            str(eval_root),
            "--token-file",
            str(token_file),
            "--ready-file",
            str(ready_file),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--timeout-ms",
            "5000",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready_file.exists():
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            time.sleep(0.02)
        assert ready_file.exists(), "bridge did not become ready"
        port = json.loads(ready_file.read_text(encoding="utf-8"))["port"]
        token = token_file.read_text(encoding="utf-8").strip()
        yield f"http://127.0.0.1:{port}", token, eval_root, tmp_path / "codex-record.json"
    finally:
        process.terminate()
        process.wait(timeout=5)


def _post(url: str, token: str, payload: dict[str, object]) -> tuple[int, dict]:
    request = Request(  # noqa: S310 - fixed loopback test server
        f"{url}/v1/generate",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - loopback test server
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def _payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": "agent",
        "model": "gpt-5.6-sol",
        "system": "private system instructions",
        "prompt": "private turn prompt",
        "schema": {
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
            "additionalProperties": False,
        },
        "images": ["screen.png"],
    }
    payload.update(changes)
    return payload


def test_bridge_invokes_fixed_ephemeral_codex_contract_without_prompt_in_argv(
    tmp_path: Path,
) -> None:
    with _running_bridge(tmp_path) as (url, token, eval_root, record_path):
        status, response = _post(url, token, _payload())

    assert (status, response) == (200, {"output": {"action": "done"}})
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["args"][:9] == [
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-schema",
        record["args"][8],
    ]
    assert record["args"][-1] == "-"
    assert "private system instructions" not in " ".join(record["args"])
    assert "private turn prompt" not in " ".join(record["args"])
    assert record["args"][record["args"].index("-i") + 1] == str(
        (eval_root / "screen.png").resolve()
    )
    assert Path(record["cwd"]).name.startswith("sessionbuddy-codex-request-")
    assert Path(record["cwd"]) != eval_root
    assert json.loads(record["input"]) == {
        "protocol": "sessionbuddy-eval-v1",
        "kind": "agent",
        "system": "private system instructions",
        "prompt": "private turn prompt",
    }
    assert "SBEK_CODEX_BIN" not in record["envKeys"]
    assert not Path(record["args"][8]).exists()


def test_bridge_rejects_unauthorized_extra_fields_and_escaped_images(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    with _running_bridge(tmp_path) as (url, token, eval_root, _record_path):
        unauthorized, unauthorized_body = _post(url, "wrong-token", _payload())
        extra, extra_body = _post(url, token, _payload(unexpected=True))
        (eval_root / "escape.png").symlink_to(outside)
        escaped, escaped_body = _post(url, token, _payload(images=["escape.png"]))

    assert unauthorized == 401
    assert unauthorized_body == {
        "error": {"code": "unauthorized", "message": "Authentication is required"}
    }
    assert extra == 400
    assert extra_body["error"]["code"] == "invalid_request"
    assert escaped == 422
    assert escaped_body["error"]["code"] == "invalid_image"
    rendered = json.dumps([unauthorized_body, extra_body, escaped_body])
    assert token not in rendered
    assert str(outside) not in rendered


def _fake_docker(path: Path) -> Path:
    path.write_text(
        """#!/bin/sh
printf '%s\\n' "$@" >>"$FAKE_DOCKER_LOG"
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def _eval_checkout(path: Path, *, with_sessions: bool) -> Path:
    (path / "src").mkdir(parents=True)
    (path / "src" / "cli.ts").write_text("", encoding="utf-8")
    (path / "package.json").write_text("{}", encoding="utf-8")
    if with_sessions:
        auth = path / ".auth"
        auth.mkdir()
        for persona in ("organizer", "speaker", "reviewer"):
            (auth / f"sessionbuddy.test.{persona}.json").write_text("{}", encoding="utf-8")
    return path


def test_dry_run_never_checks_codex_or_starts_the_bridge(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    _fake_docker(fake_bin / "docker")
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\ntouch \"$CODEX_CALLED\"\nexit 1\n", encoding="utf-8")
    codex.chmod(0o700)
    called = tmp_path / "codex-called"
    eval_root = _eval_checkout(tmp_path / "eval", with_sessions=False)
    environment = {
        **os.environ,
        "CODEX_CALLED": str(called),
        "FAKE_DOCKER_LOG": str(docker_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SBEK_CODEX_BIN": str(codex),
        "SBEK_ROOT": str(eval_root),
        "TMPDIR": str(tmp_path),
    }

    completed = subprocess.run(  # noqa: S603 - fixed repository script under test
        [str(LAUNCHER), "--dry-run"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert not called.exists()
    assert not list(tmp_path.glob("sessionbuddy-codex-bridge.*"))


def test_live_wrapper_mounts_only_one_run_capability_and_cleans_bridge(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    _fake_docker(fake_bin / "docker")
    codex = _fake_codex(tmp_path / "fake-codex.mjs")
    eval_root = _eval_checkout(tmp_path / "eval", with_sessions=True)
    environment = {
        **os.environ,
        "FAKE_DOCKER_LOG": str(docker_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SBEK_CODEX_BIN": str(codex),
        "SBEK_ROOT": str(eval_root),
        "SBEK_SKIP_SESSION_CHECK": "1",
        "SBEK_TARGET_URL": "https://sessionbuddy.test",
        "TMPDIR": str(tmp_path),
    }

    completed = subprocess.run(  # noqa: S603 - fixed repository script under test
        [str(LAUNCHER), "--areas", "1"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    arguments = docker_log.read_text(encoding="utf-8")
    assert "SBEK_CODEX_BRIDGE_URL=http://host.docker.internal:" in arguments
    assert "/v1/generate" in arguments
    assert "SBEK_CODEX_BRIDGE_TOKEN_FILE=/run/sessionbuddy-codex/token" in arguments
    assert "/run/sessionbuddy-codex/token:ro" in arguments
    assert "ANTHROPIC_API_KEY" not in arguments
    assert "CLAUDE_CONFIG_DIR" not in arguments
    assert not list(tmp_path.glob("sessionbuddy-codex-bridge.*"))


def test_launcher_has_no_anthropic_or_codex_credential_mount_fallback() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")

    assert "ANTHROPIC_API_KEY" not in launcher
    assert "CLAUDE_CONFIG_DIR" not in launcher
    assert "SBEK_CLAUDE_AUTH_VOLUME" not in launcher
    assert '"$token_file:/run/sessionbuddy-codex/token:ro"' in launcher
    assert "SBEK_CODEX_BRIDGE_TOKEN_FILE=/run/sessionbuddy-codex/token" in launcher
    assert "codex_eval_bridge.mjs" in launcher
    assert "login status" in launcher
    assert "/.codex" not in launcher
    assert "trap cleanup_bridge EXIT" in launcher
    assert "trap 'exit 129' HUP" in launcher
    assert "trap 'exit 130' INT" in launcher
    assert "trap 'exit 143' TERM" in launcher
    assert "trap cleanup_bridge EXIT HUP INT TERM" not in launcher
