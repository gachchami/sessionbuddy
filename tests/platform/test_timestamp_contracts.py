import pytest
from pydantic import ValidationError

from sessionbuddy.cfp.models import FormPublish
from sessionbuddy.evaluation.models import EvaluationRoundCreate
from sessionbuddy.platform.auth.access import BootstrapCreate, EventCreate
from sessionbuddy.scheduling.models import AgendaCandidate
from sessionbuddy.scheduling.router import _agenda_date


def event_payload(time_zone: str) -> dict[str, object]:
    return {
        "name": "Event",
        "starts_at_ms": 1_800_000_000_000,
        "ends_at_ms": 1_800_003_600_000,
        "time_zone": time_zone,
        "location": "Online",
        "delivery_mode": "virtual",
        "description": "Description",
    }


@pytest.mark.parametrize("time_zone", ("UTC", "Asia/Kolkata", "America/New_York", "Etc/GMT+5"))
def test_event_models_accept_explicit_iana_time_zones(time_zone: str) -> None:
    assert EventCreate(**event_payload(time_zone)).time_zone == time_zone
    bootstrap = BootstrapCreate(
        organization_name="Organization",
        admin_name="Admin",
        admin_email="admin@example.test",
        event_name="Event",
        starts_at_ms=1_800_000_000_000,
        ends_at_ms=1_800_003_600_000,
        time_zone=time_zone,
        event_location="Online",
        event_description="Description",
        event_delivery_mode="virtual",
    )
    assert bootstrap.time_zone == time_zone


@pytest.mark.parametrize("time_zone", ("IST", "+05:30", "GMT-4", "Asia", "../UTC"))
def test_event_models_reject_ambiguous_or_offset_time_zones(time_zone: str) -> None:
    with pytest.raises(ValidationError):
        EventCreate(**event_payload(time_zone))


def test_epoch_millisecond_models_validate_ranges_without_reinterpreting_timezone() -> None:
    assert FormPublish(
        slug="event-cfp",
        welcome_text="Welcome",
        opens_at_ms=1_800_000_000_000,
        closes_at_ms=1_800_003_600_000,
    ).closes_at_ms == 1_800_003_600_000
    assert AgendaCandidate(
        session_id="session",
        start_at_ms=1_800_000_000_000,
        end_at_ms=1_800_003_600_000,
        room_id="room",
    ).start_at_ms == 1_800_000_000_000
    with pytest.raises(ValidationError):
        EvaluationRoundCreate(
            name="Review",
            rating_min=1,
            rating_max=5,
            recommendations=["accept", "reject"],
            review_opens_at_ms=20,
            review_closes_at_ms=10,
            submission_ids=["s" * 36],
            evaluator_user_ids=["e" * 36],
            assignment_strategy="all",
        )


def test_agenda_default_date_is_derived_in_the_event_timezone() -> None:
    # 2024-01-01 20:00 UTC is already 2024-01-02 in Asia/Kolkata.
    timestamp_ms = 1_704_139_200_000
    assert _agenda_date(timestamp_ms, "UTC") == "2024-01-01"
    assert _agenda_date(timestamp_ms, "Asia/Kolkata") == "2024-01-02"
