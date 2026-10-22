from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from api.application.ports import UnitOfWorkFactory
from api.domain.models import (
    DuplicateConflict, Measurement, MetricValues, Server, ServerNotFound,
    validate_hostname,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class IngestResult:
    measurement: Measurement
    duplicate: bool


class MonitoringService:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: Callable[[], datetime] = utc_now):
        self.uow_factory = uow_factory
        self.clock = clock

    def ingest(self, server_id: UUID, measurement_id: UUID, hostname: str, values: MetricValues) -> IngestResult:
        validate_hostname(hostname)
        with self.uow_factory() as uow:
            # Serializa las escrituras por servidor, incluso su primera ingesta.
            # La recepción se fecha tras el lock para respetar el orden concurrente.
            uow.repository.register_and_lock(server_id, hostname, self.clock())
            existing = uow.repository.measurement(measurement_id)
            if existing:
                if (existing.server_id, existing.hostname, existing.values) != (server_id, hostname, values):
                    raise DuplicateConflict("measurement_id ya utilizado con otro contenido")
                return IngestResult(existing, True)
            measurement = Measurement(measurement_id, server_id, hostname, values, self.clock())
            uow.repository.add(measurement)
            uow.repository.touch(server_id, hostname, measurement.received_at)
            return IngestResult(measurement, False)

    def list_servers(self, limit: int, offset: int) -> list[Server]:
        with self.uow_factory() as uow:
            return uow.repository.servers(limit, offset)

    def get_server(self, server_id: UUID) -> Server:
        with self.uow_factory() as uow:
            server = uow.repository.server(server_id)
            if server is None:
                raise ServerNotFound()
            return server

    def history(self, server_id: UUID, limit: int, offset: int) -> list[Measurement]:
        with self.uow_factory() as uow:
            if uow.repository.server(server_id) is None:
                raise ServerNotFound()
            return uow.repository.history(server_id, limit, offset)
