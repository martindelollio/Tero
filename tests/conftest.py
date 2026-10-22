from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from api.application.services import MonitoringService
from api.domain.models import Server


class MemoryRepository:
    def __init__(self, state):
        self.state = state

    def register_and_lock(self, server_id, hostname, now):
        self.state["servers"].setdefault(server_id, Server(server_id, hostname, now, now))

    def measurement(self, measurement_id):
        return self.state["measurements"].get(measurement_id)

    def add(self, measurement):
        self.state["measurements"][measurement.id] = measurement

    def touch(self, server_id, hostname, now):
        old = self.state["servers"][server_id]
        self.state["servers"][server_id] = Server(server_id, hostname, old.created_at, now)

    def servers(self, limit, offset):
        return sorted(self.state["servers"].values(), key=lambda s: s.id)[offset:offset + limit]

    def server(self, server_id):
        return self.state["servers"].get(server_id)

    def history(self, server_id, limit, offset):
        items = [m for m in self.state["measurements"].values() if m.server_id == server_id]
        return sorted(items, key=lambda m: (m.received_at, m.id), reverse=True)[offset:offset + limit]


class MemoryUnitOfWork:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        self.repository = MemoryRepository(deepcopy(self.state))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.state.update(self.repository.state)


@pytest.fixture
def service():
    state = {"servers": {}, "measurements": {}}
    return MonitoringService(
        lambda: MemoryUnitOfWork(state),
        clock=lambda: datetime(2026, 10, 21, 12, tzinfo=timezone.utc),
    )


@pytest.fixture
def payload():
    return {
        "server_id": str(uuid4()), "measurement_id": str(uuid4()), "hostname": "tero-test",
        "cpu_percent": 25.0, "ram_percent": 50.0, "disk_percent": 60.0,
        "uptime_seconds": 123, "ram_total": 8.0, "ram_free": 4.0,
        "disk_total": 100.0, "disk_free": 40.0,
    }
