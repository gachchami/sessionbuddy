from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_release_gate_uses_disposable_d1_state_and_isolated_compose_project() -> None:
    script = (ROOT / "scripts/release_gate.sh").read_text()
    override = (ROOT / "compose.release.yaml").read_text()

    assert "mktemp -d .local/release-gate." in script
    assert "tr '[:upper:]' '[:lower:]'" in script
    assert 'RELEASE_GATE_PROJECT="sessionbuddy-release-gate-$RELEASE_GATE_SUFFIX"' in script
    assert '-p "$RELEASE_GATE_PROJECT"' in script
    assert "PW_WORKERS=${PW_WORKERS:-2}" in script
    assert "--max-failures=1" in script
    assert '--persist-to "/workspace/$RELEASE_GATE_STATE"' in script
    assert "trap cleanup EXIT INT TERM" in script
    assert script.index("npm run worker:migrate") < script.index("up --detach worker")
    assert script.count("npm run worker:migrate") == 2
    assert "INSERT INTO instance_setup" in script
    assert script.index("INSERT INTO instance_setup") < script.index("up --detach worker")
    assert override.count("ports: !reset []") == 3
    assert "mailpit:" in override
    assert "--persist-to" in override
    assert "name: sessionbuddy_wrangler-config" in override


def test_release_gate_rejects_noncanonical_fixture_assets() -> None:
    script = (ROOT / "scripts/release_gate.sh").read_text()

    assert "npm run fixtures:check-assets" in script
