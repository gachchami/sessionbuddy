import logging
import time
from html import escape
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from sessionbuddy.api.errors import ErrorDetail, ErrorEnvelope
from sessionbuddy.api.models import ApiHealthResponse, HealthResponse
from sessionbuddy.api.openapi_contract import OPENAPI_JSON
from sessionbuddy.cfp import cfp_router
from sessionbuddy.communications.d1 import communications_service
from sessionbuddy.communications.router import create_communications_router
from sessionbuddy.competition import competition_router
from sessionbuddy.console import embedded_assets, engine_room_router
from sessionbuddy.evaluation import evaluation_router
from sessionbuddy.observability import RequestObservabilityMiddleware
from sessionbuddy.platform.auth import session_router
from sessionbuddy.platform.auth.access import (
    access_router,
    setup_is_configured,
)
from sessionbuddy.platform.auth.access import (
    current_session as current_access_session,
)
from sessionbuddy.platform.auth.demo_router import demo_router
from sessionbuddy.platform.auth.http import session_cookie_value
from sessionbuddy.platform.signed_cursors import StaleCursorError
from sessionbuddy.platform.upload_contracts import UploadPolicyError
from sessionbuddy.scheduling import scheduling_router
from sessionbuddy.security import SecurityHeadersMiddleware
from sessionbuddy.speaker_operations import speaker_operations_router

logger = logging.getLogger("sessionbuddy.error")

app = FastAPI(
    title="Sessionbuddy API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Starlette executes the last-added middleware first. Observability is outermost so it
# measures and identifies failures from every subsequent middleware and route.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestObservabilityMiddleware)
app.include_router(engine_room_router)
app.include_router(session_router)
app.include_router(demo_router)
app.include_router(access_router)
app.include_router(cfp_router)
app.include_router(evaluation_router)
app.include_router(speaker_operations_router)
app.include_router(scheduling_router)
app.include_router(competition_router)
app.include_router(create_communications_router(communications_service))

_OPENAPI_ENVIRONMENTS = frozenset({"local", "development", "preview"})


def _has_development_docs(request: Request) -> bool:
    runtime = request.scope.get("env")
    app_env = str(getattr(runtime, "APP_ENV", "production")).strip().lower()
    return app_env in _OPENAPI_ENVIRONMENTS


@app.get("/api/v1/openapi.json", include_in_schema=False)
async def openapi_contract(request: Request) -> Response:
    if not _has_development_docs(request):
        raise HTTPException(status_code=404)
    return Response(
        OPENAPI_JSON,
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
async def api_documentation(request: Request) -> Response:
    if not _has_development_docs(request):
        raise HTTPException(status_code=404)
    return HTMLResponse(
        embedded_assets.API_DOCS_HTML,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/docs/assets/api-docs.css", include_in_schema=False)
async def api_documentation_stylesheet(request: Request) -> Response:
    if not _has_development_docs(request):
        raise HTTPException(status_code=404)
    return Response(
        embedded_assets.API_DOCS_CSS,
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/docs/assets/api-docs.js", include_in_schema=False)
async def api_documentation_javascript(request: Request) -> Response:
    if not _has_development_docs(request):
        raise HTTPException(status_code=404)
    return Response(
        embedded_assets.API_DOCS_JS,
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


def _session_home_destination(session) -> str:
    destination = getattr(session, "workspace_path", None)
    if destination is not None:
        return str(destination)
    # The account document is persona-neutral and owns recovery actions. A
    # null workspace must never be guessed into a protected role document.
    return "/account?workspace=recovery"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root(request: Request) -> Response:
    environment = request.scope.get("env")
    db = getattr(environment, "DB", None) if environment is not None else None
    if db is not None and not await setup_is_configured(db):
        return RedirectResponse(
            "/setup",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )
    session_cookie = session_cookie_value(request)
    if session_cookie:
        try:
            session = await current_access_session(request)
        except HTTPException as error:
            if error.status_code != 401:
                raise
        else:
            destination = _session_home_destination(session)
            return RedirectResponse(
                destination,
                status_code=303,
                headers={"Cache-Control": "no-store"},
            )
    return HTMLResponse(
        embedded_assets.LANDING_HTML,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/landing/assets/demo-access.js", response_class=Response, include_in_schema=False)
async def landing_demo_access_javascript() -> Response:
    return Response(
        embedded_assets.DEMO_ACCESS_JS,
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/landing/assets/landing.css", response_class=Response, include_in_schema=False)
async def landing_css() -> Response:
    return Response(
        embedded_assets.LANDING_CSS,
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get(
    "/landing/assets/sessionbuddy-favicon.svg",
    response_class=Response,
    include_in_schema=False,
)
async def sessionbuddy_favicon() -> Response:
    return Response(
        embedded_assets.SESSIONBUDDY_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/landing/assets/aie-new-york-2026.jpg", response_class=Response, include_in_schema=False)
async def ai_engineer_new_york_artwork() -> Response:
    return Response(
        embedded_assets.AIE_NEW_YORK_2026_JPG,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/landing/assets/aie-code-sf-2026.jpg", response_class=Response, include_in_schema=False)
async def ai_engineer_code_artwork() -> Response:
    return Response(
        embedded_assets.AIE_CODE_SF_2026_JPG,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get(
    "/health",
    tags=["operations"],
    operation_id="getHealth",
    response_model=HealthResponse,
)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get(
    "/api/v1/health",
    tags=["operations"],
    operation_id="getApiHealth",
    response_model=ApiHealthResponse,
)
async def api_health() -> ApiHealthResponse:
    return ApiHealthResponse()


def _error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    *,
    field: str | None = None,
    metadata: dict[str, object] | None = None,
) -> JSONResponse:
    response_headers = _failure_headers(request)
    if headers:
        response_headers.update(headers)
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, field=field, metadata=metadata),
        request_id=request.state.request_id,
    )
    return JSONResponse(
        status_code=status,
        content=envelope.model_dump(exclude_none=True),
        headers=response_headers,
    )


def _failure_headers(request: Request) -> dict[str, str]:
    request_id = str(getattr(request.state, "request_id", ""))
    started_ns = getattr(request.state, "request_started_ns", None)
    headers = {"Cache-Control": "no-store"}
    if request_id:
        headers["X-Request-ID"] = request_id
    if isinstance(started_ns, int):
        elapsed_ms = max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000)
        headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    return headers


def _expects_browser_page(request: Request) -> bool:
    return not request.url.path.startswith("/api/") and "text/html" in request.headers.get(
        "accept", ""
    ).lower()


def _browser_error_response(
    request: Request,
    *,
    status: int,
    title: str,
    eyebrow: str,
    heading: str,
    message: str,
    primary_label: str,
    primary_href: str,
) -> HTMLResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    reference = (
        f'<p class="reference">Reference: {escape(request_id)}</p>' if request_id else ""
    )
    page = (
        embedded_assets.ERROR_PAGE_HTML.replace("{{TITLE}}", escape(title))
        .replace("{{EYEBROW}}", escape(eyebrow))
        .replace("{{STATUS}}", str(status))
        .replace("{{HEADING}}", escape(heading))
        .replace("{{MESSAGE}}", escape(message))
        .replace("{{PRIMARY_LABEL}}", escape(primary_label))
        .replace("{{PRIMARY_HREF}}", escape(primary_href, quote=True))
        .replace("{{REFERENCE}}", reference)
    )
    return HTMLResponse(
        page,
        status_code=status,
        headers=_failure_headers(request),
    )


@app.exception_handler(404)
async def not_found(request: Request, _exception: Exception) -> Response:
    if _expects_browser_page(request):
        path = request.url.path
        if getattr(request.state, "document_scope", "") == "event_speaker":
            event_id = str(getattr(request.state, "document_event_id", ""))
            return _browser_error_response(
                request,
                status=404,
                title="Speaker record unavailable",
                eyebrow="Speaker record unavailable",
                heading="This speaker record isn’t available.",
                message=(
                    "The link may be outdated, or the speaker may no longer belong "
                    "to this event."
                ),
                primary_label="Open speaker roster",
                primary_href=f"/admin/events/{quote(event_id, safe='')}/speakers",
            )
        if path.startswith("/admin/events/"):
            return _browser_error_response(
                request,
                status=404,
                title="Event unavailable",
                eyebrow="Event unavailable",
                heading="This event isn’t available.",
                message="The link may be outdated, or your account may no longer have access.",
                primary_label="Open active workspace",
                primary_href="/",
            )
        if path.startswith("/admin/evaluation-rounds/"):
            return _browser_error_response(
                request,
                status=404,
                title="Review round unavailable",
                eyebrow="Review round unavailable",
                heading="This review round isn’t available.",
                message="The link may be outdated, or your account may no longer have access.",
                primary_label="Open active workspace",
                primary_href="/",
            )
        if path.startswith(("/events/", "/embeds/events/")):
            return _browser_error_response(
                request,
                status=404,
                title="Event unavailable",
                eyebrow="Event unavailable",
                heading="This event isn’t available.",
                message="The link may be outdated, or the event may not be public.",
                primary_label="Open SessionBuddy",
                primary_href="/",
            )
        return _browser_error_response(
            request,
            status=404,
            title="Page not found",
            eyebrow="This route stops here",
            heading="We could not find that page.",
            message="The link may be outdated, or the page may have moved.",
            primary_label="Open SessionBuddy",
            primary_href="/",
        )
    return _error_response(request, 404, "resource_not_found", "Resource not found")


@app.exception_handler(HTTPException)
async def http_error(request: Request, exception: HTTPException) -> Response:
    if (
        300 <= exception.status_code < 400
        and exception.headers
        and exception.headers.get("Location")
    ):
        return Response(status_code=exception.status_code, headers=exception.headers)
    if exception.status_code == 403 and _expects_browser_page(request):
        return _browser_error_response(
            request,
            status=403,
            title="Access denied",
            eyebrow="Access denied",
            heading="This page is not available for your active role.",
            message=(
                "Use Roles and Access on your account page to switch to a role "
                "that can open this workspace."
            ),
            primary_label="Open account and switch role",
            primary_href="/account",
        )
    if isinstance(exception, StaleCursorError):
        return _error_response(
            request,
            400,
            "stale_cursor",
            "This list changed or the continuation expired. Refresh the list to continue.",
            metadata={"reason": exception.reason},
            headers=exception.headers,
        )
    if isinstance(exception, UploadPolicyError):
        return _error_response(
            request,
            exception.status_code,
            exception.code,
            str(exception.detail),
            headers=exception.headers,
        )
    errors = {
        400: ("invalid_request", "The request could not be processed"),
        401: ("authentication_required", "Authentication is required"),
        403: ("forbidden", "The action is not permitted"),
        404: ("resource_not_found", "Resource not found"),
        409: ("conflict", "The request conflicts with current state"),
        413: ("payload_too_large", "The request body is too large"),
        415: ("unsupported_media_type", "The media type is not supported"),
        422: ("validation_failed", "The request could not be processed"),
        429: ("rate_limited", "Too many requests"),
        503: ("dependency_unavailable", "A required dependency is unavailable"),
    }
    code, message = errors.get(
        exception.status_code, ("request_failed", "The request could not be processed")
    )
    if (
        exception.status_code < 500
        and isinstance(exception.detail, str)
        and exception.detail.strip()
    ):
        message = exception.detail.strip()
    return _error_response(
        request,
        exception.status_code,
        code,
        message,
        headers=exception.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exception: RequestValidationError) -> JSONResponse:
    details: list[dict[str, str]] = []
    for error in exception.errors():
        location = [str(part) for part in error.get("loc", ()) if part not in {"body"}]
        details.append(
            {
                "field": ".".join(location) or "request",
                "message": str(error.get("msg", "Invalid value")),
                "type": str(error.get("type", "validation_error")),
            }
        )
    first = details[0] if details else None
    return _error_response(
        request,
        422,
        "validation_failed",
        first["message"] if first else "The request could not be processed",
        field=first["field"] if first else None,
        metadata={"details": details} if details else None,
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exception: Exception) -> Response:
    request_id = str(getattr(request.state, "request_id", ""))
    logger.error(
        "Unhandled request failure request_id=%s method=%s path=%s exception_type=%s",
        request_id,
        request.method,
        request.url.path,
        type(exception).__name__,
    )
    if _expects_browser_page(request):
        return _browser_error_response(
            request,
            status=500,
            title="Something went wrong",
            eyebrow="Something went wrong",
            heading="We could not load this page.",
            message="Try again. If the problem continues, share the reference below with support.",
            primary_label="Try again",
            primary_href=request.url.path,
        )
    return _error_response(
        request,
        500,
        "internal_error",
        "Something went wrong on our side. Try again.",
    )
