"""Serve the real SessionBuddy ASGI app for the Playwright harness.

The browser suite normally targets the Workerd dev server. That works, but it
is heavyweight for page-contract tests whose API traffic is fully mocked by
Playwright routes: those tests only need the real documents, styles, and
scripts. This script serves exactly that — the production ASGI app with an
in-memory D1 built from the migration ledger — so the harness can run with
nothing but Python and a browser:

    uv run python harness/serve_local.py --port 3000        # from the repo root
    cd harness && SESSIONBUDDY_BASE_URL=http://127.0.0.1:3000 npm test

Nothing here is a deployment path; state lives in memory and vanishes on
exit.
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
# The repository root too: _load_identity_flow_module() below loads
# tests/security/test_production_identity_flow.py by path, and that module does
# `from tests.schema import MIGRATIONS`. Without the root on sys.path the `tests`
# package is unimportable, this script dies at startup, and every Playwright request
# fails with ERR_CONNECTION_REFUSED -- which reads like a browser problem, not an
# import problem.
sys.path.insert(0, str(REPO_ROOT))

# ``harness/tests`` is a regular package and wins over the repository's namespace-style
# ``tests`` directory during normal import resolution. The production-flow shim imports
# ``tests.schema``, so register the repository tests namespace explicitly before loading it.
tests_package = ModuleType("tests")
tests_package.__path__ = [str(REPO_ROOT / "tests")]
sys.modules["tests"] = tests_package

import uvicorn  # noqa: E402

from sessionbuddy.api.app import app  # noqa: E402


def _load_identity_flow_module():
    # harness/tests/ is a regular package that shadows the repository's
    # namespace-package tests/ directory, so import the shared SQLite D1
    # shims by file path instead of by module name.
    import importlib.util

    path = REPO_ROOT / "tests" / "security" / "test_production_identity_flow.py"
    spec = importlib.util.spec_from_file_location("identity_flow_shims", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shims = _load_identity_flow_module()
MIGRATIONS = _shims.MIGRATIONS
AllowingRateLimiter = _shims.AllowingRateLimiter
CapturingQueue = _shims.CapturingQueue
SQLiteD1 = _shims.SQLiteD1


def build_environment(public_base_url: str) -> SimpleNamespace:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    return SimpleNamespace(
        APP_ENV="local",
        DB=SQLiteD1(connection),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
        RATE_LIMIT_HMAC_KEY="r" * 32,
        AUTH_RATE_LIMITER=AllowingRateLimiter(),
        PUBLIC_RATE_LIMITER=AllowingRateLimiter(),
        CFP_UPLOAD_AUTH_RATE_LIMITER=AllowingRateLimiter(),
        SPEAKER_UPLOAD_AUTH_RATE_LIMITER=AllowingRateLimiter(),
        HEADSHOT_UPLOAD_RATE_LIMITER=AllowingRateLimiter(),
        MAGIC_LINK_RECIPIENT_RATE_LIMITER=AllowingRateLimiter(),
        MAGIC_LINK_SOURCE_RATE_LIMITER=AllowingRateLimiter(),
        CFP_UPLOAD_POLL_RATE_LIMITER=AllowingRateLimiter(),
        PUBLIC_BASE_URL=public_base_url,
        ALLOWED_ORIGINS=public_base_url,
        COMMUNICATION_QUEUE=CapturingQueue(),
    )


def _bootstrap(application, connection) -> None:
    """Complete first-run setup so pages serve instead of redirecting to /setup.

    Uses the real guarded bootstrap API (with the migration-generated
    deployment key) rather than raw inserts, so the instance state matches a
    genuinely configured deployment.
    """
    import asyncio

    import httpx

    key = connection.execute(
        """SELECT deployment_key FROM instance_setup_credentials
           WHERE singleton_key='primary'"""
    ).fetchone()[0]

    async def run() -> None:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://serve-local") as client:
            response = await client.post(
                "/api/v1/bootstrap",
                headers={"x-bootstrap-token": str(key)},
                json={
                    "organization_name": "Harness Local",
                    "admin_name": "Harness Admin",
                    "admin_email": "harness-admin@example.com",
                },
            )
            assert response.status_code == 200, response.text

    asyncio.run(run())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument(
        "--public-base-url",
        help=(
            "Browser-visible origin used for generated links and mutation origin checks. "
            "Defaults to the bind host and port."
        ),
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Leave the instance unconfigured (serves /setup) for setup-flow tests.",
    )
    args = parser.parse_args()

    public_base_url = args.public_base_url or f"http://{args.host}:{args.port}"
    environment = build_environment(public_base_url)

    async def application(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    if not args.no_bootstrap:
        _bootstrap(application, environment.DB.connection)

    uvicorn.run(application, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
