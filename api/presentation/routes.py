from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from api.application.services import MonitoringService
from api.domain.models import MetricValues, Server
from api.presentation.schemas import IngestRequest, IngestResponse, MeasurementResponse, ServerResponse

router = APIRouter()


def get_service(request: Request) -> MonitoringService:
    return request.app.state.monitoring_service


Service = Annotated[MonitoringService, Depends(get_service)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


def server_response(server: Server, service: MonitoringService) -> ServerResponse:
    return ServerResponse(**asdict(server), status=server.status(service.clock()))


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/metrics", response_model=IngestResponse)
def receive_metrics(payload: IngestRequest, service: Service):
    values = MetricValues(**payload.model_dump(exclude={"server_id", "measurement_id", "hostname"}))
    result = service.ingest(payload.server_id, payload.measurement_id, payload.hostname, values)
    return IngestResponse(
        measurement_id=result.measurement.id, received_at=result.measurement.received_at,
        duplicate=result.duplicate,
    )


@router.get("/servers", response_model=list[ServerResponse])
def list_servers(service: Service, limit: Limit = 100, offset: Offset = 0):
    return [server_response(server, service) for server in service.list_servers(limit, offset)]


@router.get("/servers/{server_id}", response_model=ServerResponse)
def get_server(server_id: UUID, service: Service):
    return server_response(service.get_server(server_id), service)


@router.get("/servers/{server_id}/metrics", response_model=list[MeasurementResponse])
def history(server_id: UUID, service: Service, limit: Limit = 100, offset: Offset = 0):
    return [MeasurementResponse(
        measurement_id=measurement.id, server_id=measurement.server_id,
        hostname=measurement.hostname, received_at=measurement.received_at,
        **asdict(measurement.values),
    ) for measurement in service.history(server_id, limit, offset)]
