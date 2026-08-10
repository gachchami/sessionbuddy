from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_release_gate_uses_disposable_d1_state_and_isolated_compose_project() -> None:
    script = (ROOT / "scripts/release_gate.sh").read_text()
    override = (ROOT / "compose.release.yaml").read_text()

    assert "mktemp -d .local/release-gate." in script
    assert "-p sessionbuddy-release-gate" in script
    assert '--persist-to "/workspace/$RELEASE_GATE_STATE"' in script
    assert "trap cleanup EXIT INT TERM" in script
    assert script.index("npm run worker:migrate") < script.index("up --detach worker")
    assert script.count("npm run worker:migrate") == 2
    assert "INSERT INTO instance_setup" in script
    assert script.index("INSERT INTO instance_setup") < script.index("up --detach worker")
    assert "ports: !reset []" in override
    assert "--persist-to" in override
    assert "name: sessionbuddy_wrangler-config" in override
