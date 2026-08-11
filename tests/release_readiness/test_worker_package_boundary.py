from pathlib import Path

import pytest

from scripts.validate_worker_package import validate


def test_worker_package_boundary_accepts_application_modules(tmp_path: Path) -> None:
    package = tmp_path / "package"
    (package / "sessionbuddy").mkdir(parents=True)
    (package / "sessionbuddy" / "app.py").write_text("application = True\n")

    assert validate(package) == 1


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        ("fixtures/day_n/01-users.json", b"{}"),
        ("sessionbuddy/embedded.py", b"alex.organizer@example.test"),
    ],
)
def test_worker_package_boundary_rejects_fixture_material(
    tmp_path: Path, relative_path: str, content: bytes
) -> None:
    package = tmp_path / "package"
    target = package / relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(content)

    with pytest.raises(ValueError, match="fixture"):
        validate(package)
