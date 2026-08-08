from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

from sessionbuddy.api.errors import ErrorDetail, ErrorEnvelope
from sessionbuddy.api.models import ApiHealthResponse, HealthResponse
from sessionbuddy.communications.d1 import communications_service
from sessionbuddy.communications.router import create_communications_router
from sessionbuddy.console import foundation_console_router
from sessionbuddy.observability import RequestObservabilityMiddleware
from sessionbuddy.platform.auth import session_router
from sessionbuddy.security import SecurityHeadersMiddleware
from sessionbuddy.wave1 import wave1_router
from sessionbuddy.wave2 import wave2_router
from sessionbuddy.wave3 import wave3_router

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
app.include_router(foundation_console_router)
app.include_router(session_router)
app.include_router(wave1_router)
app.include_router(wave2_router)
app.include_router(wave3_router)
app.include_router(create_communications_router(communications_service))


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/foundation", status_code=307)


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
