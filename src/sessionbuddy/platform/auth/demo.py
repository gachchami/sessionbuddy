"""Demo persona configuration, gated to explicitly non-production environments.

Demo sign-in requires two independent conditions: ``APP_ENV`` must identify a
supported non-production runtime and ``DEMO_LOGIN_ENABLED`` must explicitly say
``true``. Anything else, including a missing or unknown environment, fails
closed. The separate flag keeps ordinary development deployments unaffected;
the environment allowlist prevents a copied flag from enabling password-free
privileged access in preview, staging, or production.

The three personas are resolved from configuration rather than hardcoded, so a
different database's unrelated account can never become a demo login by sharing
a display name.  Identity is always a user id; display names are mutable and
not unique.
"""

from dataclasses import dataclass
from typing import Literal

DemoRole = Literal["organizer", "reviewer", "speaker"]

DEMO_ROLES: tuple[DemoRole, ...] = ("organizer", "reviewer", "speaker")
DEMO_ENVIRONMENTS = frozenset({"local", "development"})

_USER_ID_VARIABLES: dict[DemoRole, str] = {
    "organizer": "DEMO_ORGANIZER_USER_ID",
    "reviewer": "DEMO_REVIEWER_USER_ID",
    "speaker": "DEMO_SPEAKER_USER_ID",
}

_DESCRIPTIONS: dict[DemoRole, str] = {
    "organizer": "Manage the event, proposals, speakers, and agenda.",
    "reviewer": "Score the proposals assigned to you.",
    "speaker": "Manage your proposal, profile, tasks, and files.",
}

_LABELS: dict[DemoRole, str] = {
    "organizer": "Sign in as demo organizer",
    "reviewer": "Sign in as demo reviewer",
    "speaker": "Sign in as demo speaker",
}


@dataclass(frozen=True, slots=True)
class DemoPersona:
    role: DemoRole
    user_id: str
    label: str
    description: str


def demo_login_enabled(environment) -> bool:
    """Require both a supported environment and an explicit capability flag."""
    app_env = str(getattr(environment, "APP_ENV", "production")).strip().lower()
    if app_env not in DEMO_ENVIRONMENTS:
        return False
    return str(getattr(environment, "DEMO_LOGIN_ENABLED", "")).strip().lower() == "true"


def configured_personas(environment) -> tuple[DemoPersona, ...]:
    """Return the personas that are both enabled and fully configured."""
    if not demo_login_enabled(environment):
        return ()
    personas = []
    for role in DEMO_ROLES:
        user_id = str(getattr(environment, _USER_ID_VARIABLES[role], "") or "").strip()
        if not user_id:
            continue
        personas.append(
            DemoPersona(
                role=role,
                user_id=user_id,
                label=_LABELS[role],
                description=_DESCRIPTIONS[role],
            )
        )
    return tuple(personas)


def persona_for_role(environment, role: str) -> DemoPersona | None:
    return next(
        (persona for persona in configured_personas(environment) if persona.role == role),
        None,
    )
