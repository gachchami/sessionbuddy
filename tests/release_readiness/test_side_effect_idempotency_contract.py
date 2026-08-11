"""CI guard for fanout and one-time-secret idempotency boundaries."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def _function(source: str, name: str) -> str:
    return source.split(f"async def {name}(", 1)[1].split("\n\n@", 1)[0]


def test_review_named_side_effect_routes_require_scoped_idempotency() -> None:
    contracts = (
        (
            "src/sessionbuddy/scheduling/router.py",
            "publish_agenda",
            '"event_id": event_id',
        ),
        (
            "src/sessionbuddy/evaluation/router.py",
            "add_round_submissions",
            '"round_id": round_id',
        ),
        (
            "src/sessionbuddy/competition/router.py",
            "create_accelevents_token",
            '"event_id": event_id',
        ),
    )
    for relative_path, function_name, scope_binding in contracts:
        implementation = _function(
            (ROOT / relative_path).read_text(encoding="utf-8"), function_name
        )
        assert 'Header(default=None, alias="Idempotency-Key")' in implementation
        assert "16 <= len(" in implementation or "_key(idempotency_key)" in implementation
        assert scope_binding in implementation
        assert "begin_idempotency" in implementation
        assert "complete_idempotency" in implementation


def test_ui_reuses_the_same_key_for_an_uncertain_retry() -> None:
    agenda = (ROOT / "src/sessionbuddy/static/agenda.js").read_text(encoding="utf-8")
    submissions = (ROOT / "src/sessionbuddy/static/admin_submissions.js").read_text(
        encoding="utf-8"
    )
    workspace = (ROOT / "src/sessionbuddy/static/event_workspace.js").read_text(
        encoding="utf-8"
    )

    assert "state.publishMutation.key" in agenda
    assert "state.addRoundMutation.key" in submissions
    assert "state.tokenMutation.key" in workspace
    assert '"idempotency-key"' in agenda
    assert '"idempotency-key"' in submissions
    assert '"idempotency-key"' in workspace


def test_one_time_secret_replay_never_persists_or_returns_plaintext() -> None:
    router = (ROOT / "src/sessionbuddy/competition/router.py").read_text(
        encoding="utf-8"
    )
    implementation = _function(router, "create_accelevents_token")

    assert "hash_token(token)" in implementation
    assert "cannot be replayed" in implementation
    assert 'resource_type="event_integration_token"' in implementation
    replay_branch = implementation.split("if replay is not None:", 1)[1].split(
        "now, token_id, token", 1
    )[0]
    assert "IntegrationTokenView(" not in replay_branch
