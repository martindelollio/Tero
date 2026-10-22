from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Capacity = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class MetricFields(BaseModel):
    cpu_percent: Percent
    ram_percent: Percent
    disk_percent: Percent
    uptime_seconds: Annotated[int, Field(ge=0, le=2**63 - 1, strict=True)]
    ram_total: Capacity | None = None
    ram_free: Capacity | None = None
    disk_total: Capacity | None = None
    disk_free: Capacity | None = None


class IngestRequest(MetricFields):
    model_config = ConfigDict(extra="forbid")
    server_id: UUID
    measurement_id: UUID
    hostname: Annotated[str, Field(min_length=1, max_length=253)]


class MeasurementResponse(MetricFields):
    measurement_id: UUID
    server_id: UUID
    hostname: str
    received_at: datetime


class IngestResponse(BaseModel):
    message: str = "Métricas recibidas correctamente"
    measurement_id: UUID
    received_at: datetime
    duplicate: bool


class ServerResponse(BaseModel):
    id: UUID
    hostname: str
    created_at: datetime
    last_seen: datetime
    status: str
