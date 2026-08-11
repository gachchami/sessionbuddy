import logging
import time
from html import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from sessionbuddy.api.errors import ErrorDetail, ErrorEnvelope
from sessionbuddy.api.models import ApiHealthResponse, HealthResponse
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
from sessionbuddy.scheduling import scheduling_router
from sessionbuddy.security import SecurityHeadersMiddleware
from sessionbuddy.speaker_operations import speaker_operations_router

logger = logging.getLogger("sessionbuddy.error")

app = FastAPI(
    title="Sessionbuddy API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
)

# Starlette executes the last-added middleware first. Observability is outermost so it
# measures and identifies failures from every subsequent middleware and route.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestObservabilityMiddleware)
app.include_router(engine_room_router)
app.include_router(session_router)
app.include_router(access_router)
app.include_router(cfp_router)
app.include_router(evaluation_router)
app.include_router(speaker_operations_router)
app.include_router(scheduling_router)
app.include_router(competition_router)
app.include_router(create_communications_router(communications_service))


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
    session_cookie = request.cookies.get("__Host-session") or request.cookies.get(
        "sessionbuddy-local"
    )
    if session_cookie:
        try:
            session = await current_access_session(request)
        except HTTPException as error:
            if error.status_code != 401:
                raise
        else:
            if not session.profile_complete:
                destination = "/account?onboarding=1&next=%2F"
            else:
                destination = {
                    "organizer": "/admin",
                    "speaker": "/speaker",
                    "reviewer": "/reviews",
                }.get(session.active_role or "", "/account")
            return RedirectResponse(
                destination,
                status_code=303,
                headers={"Cache-Control": "no-store"},
            )
    return HTMLResponse(
        embedded_assets.LANDING_HTML,
        headers={"Cache-Control": "no-store"},
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
) -> JSONResponse:
    response_headers = _failure_headers(request)
    if headers:
        response_headers.update(headers)
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message),
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
        return _browser_error_response(
            request,
            status=404,
            title="Page not found",
            heading="We could not find that page.",
            message="The link may be outdated, or the page may have moved.",
            primary_label="Open SessionBuddy",
            primary_href="/admin",
        )
    return _error_response(request, 404, "resource_not_found", "Resource not found")


@app.exception_handler(HTTPException)
async def http_error(request: Request, exception: HTTPException) -> JSONResponse:
    errors = {
        400: ("invalid_request", "The request could not be processed"),
        401: ("authentication_required", "Authentication is required"),
        403: ("forbidden", "The action is not permitted"),
        404: ("resource_not_found", "Resource not found"),
        409: ("conflict", "The request conflicts with current state"),
        413: ("payload_too_large", "The request body is too large"),
        415: ("unsupported_media_type", "The media type is not supported"),
        429: ("rate_limited", "Too many requests"),
        503: ("dependency_unavailable", "A required dependency is unavailable"),
    }
    code, message = errors.get(
        exception.status_code, ("request_failed", "The request could not be processed")
    )
    return _error_response(
        request,
        exception.status_code,
        code,
        message,
        headers=exception.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, _exception: RequestValidationError) -> JSONResponse:
    return _error_response(
        request,
        422,
        "validation_failed",
        "The request could not be processed",
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
