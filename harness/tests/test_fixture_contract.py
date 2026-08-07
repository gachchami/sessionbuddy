from tests.factories import SeedBundle, build_seed_bundle


def test_seed_bundle_is_deterministic() -> None:
    assert build_seed_bundle().as_dict() == build_seed_bundle().as_dict()


def test_seed_bundle_covers_key_submission_states(seed_bundle: SeedBundle) -> None:
    statuses = {submission["status"] for submission in seed_bundle.submissions}
    assert {"submitted", "under_review", "accepted", "waitlisted", "rejected"} <= statuses


def test_accepted_submissions_are_scheduled(seed_bundle: SeedBundle) -> None:
    accepted = [item for item in seed_bundle.submissions if item["status"] == "accepted"]
    assert accepted
    assert all(item["schedule"] is not None for item in accepted)


def test_invalid_submission_count_is_rejected() -> None:
    try:
        build_seed_bundle(submission_count=0)
    except ValueError as error:
        assert str(error) == "submission_count must be at least 1"
    else:
        raise AssertionError("Expected ValueError")

