from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SLODefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: str
    owner: str
    p95_ms: int = Field(gt=0)
    error_rate_max: float = Field(ge=0, le=1)
    benchmark: str


class CoverageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str
    state: Literal["available", "planned", "missing"]
    source: str


class CapabilityProgressItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: str
    capability: str
    state: Literal["complete", "in_progress", "pending"]
    evidence: str


class EngineRoomStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["review"] = "review"
    data_classification: Literal["synthetic/local", "synthetic/non-production"] = (
        "synthetic/local"
    )
    environment: Literal["local", "development", "preview", "staging", "production"] = "local"
    runtime: str = "Python / FastAPI"
    api_version: str = "v1"
    measurements_are_live: bool = False
    slos: list[SLODefinition]
    coverage: list[CoverageItem]
    build_progress: list[CapabilityProgressItem]


class DatabaseStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    provider: Literal["cloudflare-d1"] = "cloudflare-d1"
    environment: Literal["local", "development", "preview", "staging", "production"]
    query_ms: float = Field(ge=0)


class CommunicationRequeueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    organization_id: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=250, ge=1, le=1000)


class CommunicationRequeueResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: int = Field(ge=0)
    requeued: int = Field(ge=0)
    published: int = Field(ge=0)
    publish_failures: int = Field(ge=0)


class BrowserTelemetryPayload(BaseModel):
    """Allow-listed, privacy-safe browser measurement envelope.

    There is intentionally no URL, identity, DOM, query, or arbitrary metadata
    field. Exporters should add server-known environment/version dimensions.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    page_template: Literal[
        "/engine-room",
        "/admin/events/{event_id}/cfp",
        "/cfp/{slug}",
        "/admin/events/{event_id}/submissions",
        "/reviews",
        "/admin/evaluation-rounds/{round_id}",
    ]
    navigation_type: Literal["navigate", "reload", "back_forward", "prerender", "unknown"]
    device_class: Literal["mobile", "tablet", "desktop"]
    sampled: bool
    lcp_ms: float | None = Field(default=None, ge=0, le=120_000)
    inp_ms: float | None = Field(default=None, ge=0, le=120_000)
    cls: float | None = Field(default=None, ge=0, le=10)
    ttfb_ms: float | None = Field(default=None, ge=0, le=120_000)
    fcp_ms: float | None = Field(default=None, ge=0, le=120_000)
    route_transition_ms: float | None = Field(default=None, ge=0, le=120_000)
    critical_api_ms: float | None = Field(default=None, ge=0, le=120_000)
    api_request_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")

    @field_validator(
        "lcp_ms", "inp_ms", "cls", "ttfb_ms", "fcp_ms", "route_transition_ms", "critical_api_ms"
    )
    @classmethod
    def finite_measurement(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("measurement must be finite")
        return value


def public_engine_room_status(*, environment: str = "local") -> EngineRoomStatus:
    return EngineRoomStatus(
        environment=environment,
        data_classification=(
            "synthetic/local" if environment == "local" else "synthetic/non-production"
        ),
        slos=[
            SLODefinition(
                route="GET /health",
                owner="platform-operations",
                p95_ms=250,
                error_rate_max=0.001,
                benchmark="engine-room-health",
            ),
            SLODefinition(
                route="GET /api/v1/health",
                owner="platform-operations",
                p95_ms=250,
                error_rate_max=0.001,
                benchmark="engine-room-health",
            ),
            SLODefinition(
                route="GET /api/v1/engine-room/status",
                owner="platform-operations",
                p95_ms=250,
                error_rate_max=0.001,
                benchmark="foundation-console-status",
            ),
            SLODefinition(
                route="GET /api/v1/engine-room/database",
                owner="platform-operations",
                p95_ms=250,
                error_rate_max=0.001,
                benchmark="engine-room-d1-probe",
            ),
        ],
        coverage=[
            CoverageItem(signal="Request ID", state="available", source="response header"),
            CoverageItem(signal="Server-Timing", state="available", source="response header"),
            CoverageItem(signal="API latency history", state="planned", source="managed dashboard"),
            CoverageItem(signal="Browser Web Vitals", state="planned", source="privacy-safe RUM"),
            CoverageItem(
                signal="D1 query timing",
                state="available",
                source="GET /api/v1/engine-room/database",
            ),
        ],
        build_progress=[
            CapabilityProgressItem(
                area="Platform",
                capability="Cloudflare-compatible Python runtime and D1",
                state="complete",
                evidence="Docker, Workerd/Pyodide, migrations, deployed smoke checks",
            ),
            CapabilityProgressItem(
                area="Platform",
                capability="API security, sessions, CSRF, RBAC and tenant isolation",
                state="complete",
                evidence="Automated adversarial foundation suite",
            ),
            CapabilityProgressItem(
                area="Platform",
                capability="Observability, SLOs, runbooks and benchmark tooling",
                state="complete",
                evidence="Route manifest, Server-Timing and Engine Room",
            ),
            CapabilityProgressItem(
                area="CFP management",
                capability="Call for Proposals, form and submission journey",
                state="complete",
                evidence=(
                    "Published forms, conditional fields, drafts, uploads, "
                    "and validated submissions"
                ),
            ),
            CapabilityProgressItem(
                area="Evaluation, speaker operations, and scheduling",
                capability="Speaker portal, evaluation, communications and agenda",
                state="complete",
                evidence=(
                    "Weighted review rounds, speaker workflows, messaging, "
                    "agenda conflicts, and publication"
                ),
            ),
        ],
    )
