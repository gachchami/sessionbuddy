"""Authentication primitives safe for the Python Worker runtime."""

from .cookies import sign_session_cookie, verify_session_cookie
from .csrf import issue_csrf_token, verify_csrf_token
from .demo import DemoPersona, configured_personas, demo_login_enabled, persona_for_role
from .demo_router import demo_router
from .http import (
    AuthenticatedContext,
    authenticate_request,
    authorization_denial_status,
    guard_mutation,
    require_permission,
)
from .models import CookiePolicy, SessionPolicy
from .request_guard import MutationGuardDecision, guard_cookie_mutation
from .router import session_router
from .service import AuthenticationResult, authenticate_session
from .sessions import SessionDecision, SessionRecord, validate_session
from .tokens import generate_token, hash_token, normalize_email

__all__ = [
    "CookiePolicy",
    "AuthenticatedContext",
    "AuthenticationResult",
    "DemoPersona",
    "MutationGuardDecision",
    "SessionDecision",
    "SessionPolicy",
    "SessionRecord",
    "session_router",
    "configured_personas",
    "demo_login_enabled",
    "demo_router",
    "persona_for_role",
    "guard_cookie_mutation",
    "guard_mutation",
    "authenticate_session",
    "authenticate_request",
    "authorization_denial_status",
    "generate_token",
    "hash_token",
    "issue_csrf_token",
    "require_permission",
    "normalize_email",
    "sign_session_cookie",
    "validate_session",
    "verify_csrf_token",
    "verify_session_cookie",
]
