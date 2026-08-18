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


def test_source_wiring_ui_reuses_the_same_key_for_an_uncertain_retry() -> None:
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


def test_source_wiring_one_time_secret_replay_never_persists_or_returns_plaintext() -> None:
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


def test_source_wiring_inline_upload_completion_cannot_supersede_the_version_it_promotes() -> None:
    """The completion route is replayable, and its inline branch is a state machine.

    Two requests that both read scan_state='pending_upload' before either batch
    commits serialise into the same statements twice. On the second pass the
    version is already clean and current, so a supersede that does not exclude
    it demotes the very row the promote below is about to claim -- the promote
    then no-ops on scan_state and the asset is left with no current version.
    The EXISTS receipt guard does not catch this: the first pass's receipt
    satisfies it. Only the self-exclusion does, which is why the queue
    consumer's _clean_result_statements carries the same 'previous.id<>' term.
    """
    router = (ROOT / "src/sessionbuddy/speaker_operations/router.py").read_text(
        encoding="utf-8"
    )
    completion = _function(router, "complete_speaker_upload")

    supersede = completion.split("scan_state = 'superseded'", 1)[1].split('"""', 1)[0]
    assert "is_current = 1 AND scan_state = 'clean'" in supersede
    assert "id <> ?3" in supersede
    # Demoting is conditional on the promote below being certain to land, so the
    # supersede repeats every condition the promote checks. A receipt insert that
    # no-ops on an unrelated uniqueness conflict would otherwise retire the outgoing
    # version for an incoming one that never leaves 'scanning'.
    assert "receipt.verdict = 'clean'" in supersede
    assert "newer.generation > candidate.generation" in supersede
    # A spent intent is refused as well, so the two replay guards cannot drift.
    assert 'row["consumed_at_ms"] is not None' in completion

    boundary = (ROOT / "src/sessionbuddy/speaker_operations/asset_boundary.py").read_text(
        encoding="utf-8"
    )
    assert "previous.id<>?1" in boundary


def test_terminal_asset_verdicts_are_written_only_against_their_own_receipt() -> None:
    """Both verdicts, on both paths, require the stored receipt to agree.

    Four write sites reach a terminal scan_state -- promote and reject, queued and
    inline -- and each can be reached with the receipt insert no-oped by the job's
    UNIQUE key. Guarding only the promotes leaves 'rejected' writable against a
    stored 'clean', which is the same divergence in the direction nobody reports.
    The inline task completion carries the guard too, so a no-op promote cannot
    tell a speaker their upload was accepted.
    """
    boundary = (ROOT / "src/sessionbuddy/speaker_operations/asset_boundary.py").read_text(
        encoding="utf-8"
    )
    router = (ROOT / "src/sessionbuddy/speaker_operations/router.py").read_text(
        encoding="utf-8"
    )
    consumer = _function(boundary, "consume_scan_job")
    completion = _function(router, "complete_speaker_upload")

    queued_reject = consumer.split("scan_state='rejected'", 1)[1].split('"""', 1)[0]
    assert "receipt.verdict='malicious'" in queued_reject
    inline_reject = completion.split("scan_state = 'rejected'", 1)[1].split('"""', 1)[0]
    assert "receipt.verdict = 'malicious'" in inline_reject
    # Scoped to the generation and checksum the request was completing, as the
    # promote beside it already was.
    assert "AND generation = ?7 AND checksum_sha256 = ?3" in inline_reject

    clean_statements = boundary.split("def _clean_result_statements", 1)[1]
    assert clean_statements.count("receipt.verdict='clean'") == 2, (
        "the supersede and the promote must both require the receipt, or a "
        "receipt-less promote no-ops behind a supersede that already landed"
    )

    task = completion.split("UPDATE speaker_tasks SET state = 'completed'", 1)[1]
    assert "promoted.is_current = 1" in task
    assert "promoted.scan_state = 'clean'" in task

    # The response reports the row's real state, not the verdict it attempted.
    assert "state = _completion_state(" in completion
    assert "SELECT scan_state FROM speaker_asset_versions WHERE id = ?1" in completion
