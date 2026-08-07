from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sessionbuddy.api.errors import ErrorDetail, ErrorEnvelope
from sessionbuddy.observability import RequestObservabilityMiddleware
from sessionbuddy.security import SecurityHeadersMiddleware

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


@app.get("/health", tags=["operations"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/health", tags=["operations"])
async def api_health() -> dict[str, str]:
    return {"status": "ok", "api_version": "v1"}


@app.exception_handler(404)
async def not_found(request: Request, _exception: Exception) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorDetail(code="resource_not_found", message="Resource not found"),
        request_id=request.state.request_id,
    )
    return JSONResponse(status_code=404, content=envelope.model_dump(exclude_none=True))
