from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_local_compose_exposes_mailpit_inbox_and_worker_dependency() -> None:
    compose = (ROOT / "compose.yaml").read_text()

    assert "axllent/mailpit:v1.30.0" in compose
    assert '"8025:8025"' in compose
    assert "mailpit:\n        condition: service_healthy" in compose


def test_local_compose_exposes_https_without_changing_worker_http_port() -> None:
    compose = (ROOT / "compose.yaml").read_text()

    assert "caddy:2.10.2-alpine" in compose
    assert '"8443:8443"' in compose
    assert '"https://localhost:8443"' in compose
    assert '"http://worker:8787"' in compose


def test_mailpit_is_configured_only_in_local_worker_vars() -> None:
    config = (ROOT / "wrangler.jsonc").read_text()
    local_vars, deployed = config.split('"env":', 1)

    assert '"MAILPIT_API_URL": "http://mailpit:8025"' in local_vars
    assert "MAILPIT_API_URL" not in deployed


def test_queue_consumer_requires_explicit_local_environment_for_mailpit() -> None:
    entry = (ROOT / "src" / "entry.py").read_text()

    assert 'app_env == "local" and mailpit_api_url and from_address' in entry
    assert "MailpitProvider(mailpit_api_url, from_address)" in entry


def test_auth_links_still_require_https_in_every_environment() -> None:
    access = (ROOT / "src" / "sessionbuddy" / "platform" / "auth" / "access.py").read_text()

    assert 'if base.startswith("https://"):' in access
    assert 'base.startswith("http://")' not in access
