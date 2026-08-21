"""Demo persona sign-in must stay fail-closed and non-impersonating."""

from types import SimpleNamespace

import pytest

from sessionbuddy.platform.auth.demo import (
    DEMO_ROLES,
    configured_personas,
    demo_login_enabled,
    persona_for_role,
)
from sessionbuddy.platform.auth.demo_router import DemoSignIn
from sessionbuddy.platform.auth.session_factory import (
    role_compatible_redirect,
    role_destination,
    valid_redirect,
)

ORGANIZER_ID = "11111111-1111-1111-1111-111111111111"
REVIEWER_ID = "22222222-2222-2222-2222-222222222222"
SPEAKER_ID = "33333333-3333-3333-3333-333333333333"


def environment(**overrides) -> SimpleNamespace:
    values = {
        "APP_ENV": "local",
        "DEMO_LOGIN_ENABLED": "true",
        "DEMO_ORGANIZER_USER_ID": ORGANIZER_ID,
        "DEMO_REVIEWER_USER_ID": REVIEWER_ID,
        "DEMO_SPEAKER_USER_ID": SPEAKER_ID,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "flag",
    ["false", "False", "1", "yes", "on", "", "  ", "no", "0", "tru", "true-ish"],
)
def test_nothing_but_true_enables_demo_login(flag: str) -> None:
    """Anything ambiguous reads as off, so the default is always disabled."""
    assert demo_login_enabled(environment(DEMO_LOGIN_ENABLED=flag)) is False


@pytest.mark.parametrize("flag", ["true", "True", "TRUE", " true ", "\tTrue\n"])
def test_true_is_matched_case_insensitively_and_trimmed(flag: str) -> None:
    """Config files quote booleans inconsistently; only casing and surrounding
    whitespace are forgiven, never a different word."""
    assert demo_login_enabled(environment(DEMO_LOGIN_ENABLED=flag)) is True


def test_an_absent_flag_leaves_demo_login_disabled() -> None:
    assert demo_login_enabled(SimpleNamespace()) is False
    assert demo_login_enabled(SimpleNamespace(DEMO_ORGANIZER_USER_ID=ORGANIZER_ID)) is False


def test_an_absent_environment_object_is_treated_as_disabled() -> None:
    assert demo_login_enabled(None) is False
    assert configured_personas(None) == ()


@pytest.mark.parametrize("app_env", ["local", "development"])
def test_supported_app_env_and_explicit_flag_enable_demo_login(app_env: str) -> None:
    assert demo_login_enabled(environment(APP_ENV=app_env)) is True


@pytest.mark.parametrize("app_env", ["preview", "staging", "production", "demo", "", "unknown"])
def test_unsupported_app_env_fails_closed_even_with_flag(app_env: str) -> None:
    assert demo_login_enabled(environment(APP_ENV=app_env)) is False


def test_a_disabled_flag_yields_no_personas_even_when_ids_are_configured() -> None:
    assert configured_personas(environment(DEMO_LOGIN_ENABLED="false")) == ()
    assert persona_for_role(environment(DEMO_LOGIN_ENABLED="false"), "organizer") is None


def test_only_fully_configured_personas_are_offered() -> None:
    personas = configured_personas(environment(DEMO_REVIEWER_USER_ID=""))
    assert [persona.role for persona in personas] == ["organizer", "speaker"]
    assert persona_for_role(environment(DEMO_REVIEWER_USER_ID=""), "reviewer") is None


def test_persona_ids_come_from_configuration_not_from_the_request() -> None:
    runtime = environment(DEMO_ORGANIZER_USER_ID="  " + ORGANIZER_ID + "  ")
    persona = persona_for_role(runtime, "organizer")
    assert persona is not None
    assert persona.user_id == ORGANIZER_ID


def test_every_configured_persona_targets_its_own_workspace() -> None:
    destinations = {
        persona.role: persona.destination for persona in configured_personas(environment())
    }
    assert destinations == {
        "organizer": "/admin",
        "reviewer": "/reviews",
        "speaker": "/speaker",
    }


def test_the_request_body_accepts_only_the_three_fixed_roles() -> None:
    for role in DEMO_ROLES:
        assert DemoSignIn(role=role).role == role
    for rejected in ["admin", "organization_admin", "", "ORGANIZER", "evaluator"]:
        with pytest.raises(ValueError):
            DemoSignIn(role=rejected)


def test_the_request_body_cannot_carry_an_identity() -> None:
    """A caller must not be able to name the account it signs in as."""
    for payload in (
        {"role": "organizer", "email": "someone@example.test"},
        {"role": "organizer", "user_id": ORGANIZER_ID},
        {"role": "organizer", "password": "irrelevant"},
    ):
        with pytest.raises(ValueError):
            DemoSignIn(**payload)


@pytest.mark.parametrize(
    "redirect",
    ["//evil.example", "https://evil.example", "/admin\\..", "evil", ""],
)
def test_offsite_redirects_are_rejected(redirect: str) -> None:
    assert valid_redirect(redirect) is False


def test_an_incompatible_redirect_is_corrected_to_the_role_workspace() -> None:
    assert role_compatible_redirect("/admin", "reviewer") == "/reviews"
    assert role_compatible_redirect("/reviews/queue", "speaker") == "/speaker"
    assert role_compatible_redirect("/speaker/tasks", "organizer") == "/admin"
    assert role_compatible_redirect("/", "organizer") == "/admin"


def test_a_compatible_redirect_is_preserved() -> None:
    assert role_compatible_redirect("/reviews/queue", "reviewer") == "/reviews/queue"
    assert role_compatible_redirect("/admin/events", "organizer") == "/admin/events"


def test_an_unknown_role_has_no_destination() -> None:
    from fastapi import HTTPException

    for unknown in [None, "", "admin", "evaluator"]:
        with pytest.raises(HTTPException) as raised:
            role_destination(unknown)
        assert raised.value.status_code == 403
