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
from sessionbuddy.platform.auth.access import access_router, setup_is_configured
from sessionbuddy.scheduling import scheduling_router
from sessionbuddy.security import SecurityHeadersMiddleware
from sessionbuddy.speaker_operations import speaker_operations_router

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
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message),
        request_id=request.state.request_id,
    )
    return JSONResponse(
        status_code=status,
        content=envelope.model_dump(exclude_none=True),
        headers=headers,
    )


@app.exception_handler(404)
async def not_found(request: Request, _exception: Exception) -> JSONResponse:
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
