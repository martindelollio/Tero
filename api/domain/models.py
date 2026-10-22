"""Entidades y reglas independientes de HTTP y de la base de datos."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import unicodedata
from uuid import UUID


class InvalidMeasurement(ValueError):
    pass


class DuplicateConflict(Exception):
    pass


class ServerNotFound(Exception):
    pass


def validate_hostname(hostname: str) -> None:
    if not hostname or len(hostname) > 253 or hostname != hostname.strip():
        raise InvalidMeasurement("hostname debe contener entre 1 y 253 caracteres sin espacios externos")
    # Cc: NUL, CR/LF, DEL, C1; Cf: controles de formato/bidi; Cs: surrogates.
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in hostname):
        raise InvalidMeasurement("hostname no puede contener caracteres de control o formato")


@dataclass(frozen=True)
class MetricValues:
    cpu_percent: float
    ram_percent: float
    disk_percent: float
    uptime_seconds: int
    ram_total: float | None = None
    ram_free: float | None = None
    disk_total: float | None = None
    disk_free: float | None = None

    def __post_init__(self):
        for name in ("cpu_percent", "ram_percent", "disk_percent"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise InvalidMeasurement(f"{name} debe estar entre 0 y 100")
        if not isinstance(self.uptime_seconds, int) or not 0 <= self.uptime_seconds <= 2**63 - 1:
            raise InvalidMeasurement("uptime_seconds fuera de rango")
        for prefix in ("ram", "disk"):
            total, free = getattr(self, f"{prefix}_total"), getattr(self, f"{prefix}_free")
            for value in (total, free):
                if value is not None and (not math.isfinite(value) or value < 0):
                    raise InvalidMeasurement("Capacidades deben ser finitas y no negativas")
            if total is not None and free is not None and free > total:
                raise InvalidMeasurement(f"{prefix}_free no puede superar el total")


@dataclass(frozen=True)
class Measurement:
    id: UUID
    server_id: UUID
    hostname: str
    values: MetricValues
    received_at: datetime


@dataclass(frozen=True)
class Server:
    id: UUID
    hostname: str
    created_at: datetime
    last_seen: datetime

    def status(self, now: datetime, timeout_seconds: int = 90) -> str:
        return "online" if now - self.last_seen < timedelta(seconds=timeout_seconds) else "offline"
