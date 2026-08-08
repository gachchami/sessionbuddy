"""Authentication primitives safe for the Python Worker runtime."""

from .cookies import sign_session_cookie, verify_session_cookie
from .csrf import issue_csrf_token, verify_csrf_token
from .models import CookiePolicy, SessionPolicy
from .request_guard import MutationGuardDecision, guard_cookie_mutation
from .service import AuthenticationResult, authenticate_session
from .sessions import SessionDecision, SessionRecord, validate_session
from .tokens import generate_token, hash_token, normalize_email

__all__ = [
    "CookiePolicy",
    "AuthenticationResult",
    "MutationGuardDecision",
    "SessionDecision",
    "SessionPolicy",
    "SessionRecord",
    "guard_cookie_mutation",
    "authenticate_session",
    "generate_token",
    "hash_token",
    "issue_csrf_token",
    "normalize_email",
    "sign_session_cookie",
    "validate_session",
    "verify_csrf_token",
    "verify_session_cookie",
]
