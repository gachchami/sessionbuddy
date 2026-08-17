import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "seed_platform.py"
spec = importlib.util.spec_from_file_location("seed_platform", SCRIPT)
seed_platform = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(seed_platform)


def test_seed_is_deterministic_and_cross_tenant():
    first = seed_platform.build_seed("test")
    assert first == seed_platform.build_seed("test")
    assert first != seed_platform.build_seed("other")
    assert len(first["organizations"]) == 2
    assert len(first["events"]) == 4
    assert "programs" not in first
    assert {row["role"] for row in first["event_memberships"]} == {
        "evaluator",
        "speaker",
    }
    assert any(row["status"] == "revoked" for row in first["organization_memberships"])
    assert any("revoked_at_ms" in row for row in first["sessions"])
    assert any("consumed_at_ms" in row for row in first["authentication_challenges"])
    assert any(
        row["expires_at_ms"] < seed_platform.BASE_MS for row in first["authentication_challenges"]
    )
    assert all(str(row["email"]).endswith("@example.test") for row in first["users"])
