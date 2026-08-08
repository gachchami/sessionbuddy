import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "seed_foundation.py"
spec = importlib.util.spec_from_file_location("seed_foundation", SCRIPT)
seed_foundation = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(seed_foundation)


def test_seed_is_deterministic_and_cross_tenant():
    first = seed_foundation.build_seed("test")
    assert first == seed_foundation.build_seed("test")
    assert first != seed_foundation.build_seed("other")
    assert len(first["organizations"]) == 2
    assert len(first["events"]) == 4
    assert {row["role"] for row in first["event_memberships"]} == {
        "event_admin",
        "evaluator",
        "speaker",
    }
    assert any(row["status"] == "revoked" for row in first["organization_memberships"])
    assert any("revoked_at_ms" in row for row in first["sessions"])
    assert any("consumed_at_ms" in row for row in first["authentication_challenges"])
    assert any(
        row["expires_at_ms"] < seed_foundation.BASE_MS for row in first["authentication_challenges"]
    )
    assert all(str(row["email"]).endswith("@example.test") for row in first["users"])
