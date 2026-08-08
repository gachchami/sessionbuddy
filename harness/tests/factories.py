from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any


@dataclass(frozen=True)
class SeedBundle:
    organization: dict[str, Any]
    event: dict[str, Any]
    program: dict[str, Any]
    people: list[dict[str, Any]]
    submissions: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_seed_bundle(seed: int = 20260808, submission_count: int = 12) -> SeedBundle:
    """Build stable, non-production fixture data with useful lifecycle coverage."""
    if submission_count < 1:
        raise ValueError("submission_count must be at least 1")

    rng = Random(seed)
    starts_at = datetime(2027, 3, 15, 9, 0, tzinfo=UTC)
    statuses = ["submitted", "under_review", "accepted", "waitlisted", "rejected"]
    topics = ["AI", "Community", "Engineering", "Leadership", "Product"]

    people = [
        {
            "id": f"person-{index:03d}",
            "first_name": f"Speaker{index}",
            "last_name": "Example",
            "email": f"speaker{index}@example.test",
            "biography": f"Synthetic speaker profile {index} for local testing.",
        }
        for index in range(1, submission_count + 1)
    ]

    submissions = []
    for index, person in enumerate(people, start=1):
        status = statuses[(index - 1) % len(statuses)]
        submissions.append(
            {
                "id": f"submission-{index:03d}",
                "reference": f"SB-{1000 + index}",
                "title": f"Example session {index}",
                "abstract": "Synthetic content created by the private seed harness.",
                "topic": rng.choice(topics),
                "status": status,
                "primary_submitter_id": person["id"],
                "schedule": (
                    {
                        "starts_at": (starts_at + timedelta(hours=index)).isoformat(),
                        "ends_at": (starts_at + timedelta(hours=index, minutes=45)).isoformat(),
                        "room": f"Room {(index % 3) + 1}",
                    }
                    if status == "accepted"
                    else None
                ),
            }
        )

    return SeedBundle(
        organization={"id": "org-fixture", "name": "Synthetic Fixture Organization"},
        event={
            "id": "event-fixture",
            "name": "Synthetic Fixture Conference",
            "timezone": "Asia/Kolkata",
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(days=2)).isoformat(),
        },
        program={"id": "program-fixture", "name": "Main Program", "status": "open"},
        people=people,
        submissions=submissions,
    )
