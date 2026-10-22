from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from api.domain.models import DuplicateConflict, InvalidMeasurement, MetricValues, ServerNotFound


def ingest(service, payload):
    return service.ingest(
        UUID(payload["server_id"]), UUID(payload["measurement_id"]), payload["hostname"],
        MetricValues(**{k: v for k, v in payload.items() if k not in {"server_id", "measurement_id", "hostname"}}),
    )


def test_register_persist_and_replay_does_not_refresh_last_seen(service, payload):
    first = ingest(service, payload)
    original_clock = service.clock
    service.clock = lambda: original_clock() + timedelta(seconds=120)
    replay = ingest(service, payload)
    assert not first.duplicate
    assert replay.duplicate
    assert replay.measurement == first.measurement
    server = service.get_server(UUID(payload["server_id"]))
    assert server.last_seen == first.measurement.received_at
    assert server.status(service.clock()) == "offline"
    assert len(service.history(server.id, 10, 0)) == 1


def test_conflicting_id_rolls_back_registration(service, payload):
    ingest(service, payload)
    with pytest.raises(DuplicateConflict):
        ingest(service, {**payload, "server_id": str(uuid4())})
    with pytest.raises(DuplicateConflict):
        ingest(service, {**payload, "cpu_percent": 99})
    assert len(service.list_servers(100, 0)) == 1


def test_new_measurement_updates_hostname_and_contact(service, payload):
    ingest(service, payload)
    original_clock = service.clock
    service.clock = lambda: original_clock() + timedelta(seconds=30)
    ingest(service, {**payload, "measurement_id": str(uuid4()), "hostname": "renamed"})
    server = service.get_server(UUID(payload["server_id"]))
    assert server.hostname == "renamed"
    assert server.last_seen == service.clock()
    assert server.created_at == original_clock()
    assert server.status(service.clock() + timedelta(seconds=89)) == "online"
    assert server.status(service.clock() + timedelta(seconds=90)) == "offline"
    assert len(service.history(server.id, 10, 0)) == 2


@pytest.mark.parametrize("change", [
    {"cpu_percent": -1}, {"ram_percent": 101}, {"disk_percent": float("nan")},
    {"ram_total": float("inf")}, {"ram_free": -1}, {"ram_free": 9},
    {"disk_free": 101}, {"uptime_seconds": -1}, {"uptime_seconds": 2**63},
])
def test_domain_rejects_invalid_metrics(change):
    values = MetricValues(25, 50, 60, 123, 8, 4, 100, 40)
    with pytest.raises(InvalidMeasurement):
        replace(values, **change)


def test_unknown_server(service):
    with pytest.raises(ServerNotFound):
        service.get_server(uuid4())
    with pytest.raises(ServerNotFound):
        service.history(uuid4(), 10, 0)


def test_invalid_hostname_does_not_register(service, payload):
    with pytest.raises(InvalidMeasurement):
        ingest(service, {**payload, "hostname": " "})
    assert service.list_servers(100, 0) == []


@pytest.mark.parametrize("hostname", [
    "node\x00bad", "node\nbad", "node\rbad", "node\tbad", "node\x7fbad",
    "node\x85bad", "node\u202ebad", "node\u200bbad", "node\u2028bad", "node\ud800bad",
])
def test_hostname_rejected_before_opening_transaction(service, payload, hostname):
    def forbidden():
        raise AssertionError("No debe abrir una transacción para un hostname inválido")

    service.uow_factory = forbidden
    with pytest.raises(InvalidMeasurement):
        ingest(service, {**payload, "hostname": hostname})
