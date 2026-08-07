from __future__ import annotations

import pytest

from tests.factories import SeedBundle, build_seed_bundle


@pytest.fixture
def seed_bundle() -> SeedBundle:
    return build_seed_bundle()

