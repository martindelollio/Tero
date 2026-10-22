"""Integración real: cada ejecución crea y elimina únicamente su propio schema."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from api.application.services import MonitoringService
from api.domain.models import DuplicateConflict, MetricValues
from api.infrastructure.database import build_engine
from api.infrastructure.repository import SqlAlchemyRepository, SqlAlchemyUnitOfWork
from api.main import create_app


@pytest.fixture(scope="module")
def postgres_engine():
    url = os.environ.get("TERO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Definí TERO_TEST_DATABASE_URL para probar PostgreSQL real")
    schema = "tero_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    engine = build_engine(isolated_url)
    previous = os.environ.get("TERO_DATABASE_URL")
    try:
        os.environ["TERO_DATABASE_URL"] = isolated_url
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)
        yield engine
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("TERO_DATABASE_URL", None)
        else:
            os.environ["TERO_DATABASE_URL"] = previous
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def pg_service(postgres_engine):
    sessions = sessionmaker(postgres_engine, expire_on_commit=False)
    return MonitoringService(lambda: SqlAlchemyUnitOfWork(sessions))


def ingest(service, payload):
    return service.ingest(
        UUID(payload["server_id"]), UUID(payload["measurement_id"]), payload["hostname"],
        MetricValues(**{k: v for k, v in payload.items() if k not in {"server_id", "measurement_id", "hostname"}}),
    )


def test_postgres_http_roundtrip_and_persistence(pg_service, payload):
    with TestClient(create_app(pg_service)) as client:
        assert client.post("/metrics", json=payload).status_code == 200
    # Una aplicación/sesión nueva debe recuperar datos ya confirmados.
    with TestClient(create_app(pg_service)) as client:
        path = f"/servers/{payload['server_id']}"
        assert client.get(path).json()["status"] == "online"
        assert client.get(path + "/metrics").json()[0]["ram_free"] == 4.0
        assert client.post("/metrics", json=payload).json()["duplicate"]
        assert len(client.get(path + "/metrics").json()) == 1


def test_concurrent_same_measurement_is_stored_once(pg_service, payload):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ingest(pg_service, payload), range(16)))
    assert sum(not result.duplicate for result in results) == 1
    assert len(pg_service.history(UUID(payload["server_id"]), 100, 0)) == 1
    assert len({result.measurement.received_at for result in results}) == 1


def test_concurrent_global_id_collision_rolls_back_loser(pg_service, payload):
    payloads = [payload, {**payload, "server_id": str(uuid4())}]

    def attempt(item):
        try:
            ingest(pg_service, item)
            return "ok"
        except DuplicateConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, payloads)) == ["conflict", "ok"]
    with pg_service.uow_factory() as uow:
        assert sum(uow.repository.server(UUID(item["server_id"])) is not None for item in payloads) == 1


def test_failure_rolls_back_server_and_measurement(pg_service, payload, monkeypatch):
    def fail(*args):
        raise RuntimeError("Falla simulada después del INSERT")

    monkeypatch.setattr(SqlAlchemyRepository, "touch", fail)
    with pytest.raises(RuntimeError):
        ingest(pg_service, payload)
    with pg_service.uow_factory() as uow:
        assert uow.repository.server(UUID(payload["server_id"])) is None
        assert uow.repository.measurement(UUID(payload["measurement_id"])) is None


def test_history_is_ordered_paginated_and_isolated(pg_service, payload):
    for _ in range(3):
        ingest(pg_service, {**payload, "measurement_id": str(uuid4())})
    ingest(pg_service, {**payload, "server_id": str(uuid4()), "measurement_id": str(uuid4())})
    server_id = UUID(payload["server_id"])
    all_items = pg_service.history(server_id, 100, 0)
    assert len(all_items) == 3
    assert [m.received_at for m in all_items] == sorted([m.received_at for m in all_items], reverse=True)
    assert pg_service.history(server_id, 1, 1) == all_items[1:2]


def test_pool_saturation_returns_503_and_recovers(postgres_engine, monkeypatch):
    import api.main as api_main

    engine = build_engine(postgres_engine.url.render_as_string(hide_password=False),
                          pool_size=1, max_overflow=0, pool_timeout=0.05)
    monkeypatch.setattr(api_main, "build_engine", lambda url: engine)
    with TestClient(api_main.create_app(url="injected-in-test")) as client:
        with engine.connect():
            response = client.get("/servers")
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "1"
            assert "postgresql" not in response.text
        assert client.get("/servers").status_code == 200


def test_postgres_timeout_settings(postgres_engine):
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SHOW statement_timeout")) == "1500ms"
        assert connection.scalar(text("SHOW lock_timeout")) == "500ms"
        assert connection.scalar(text("SHOW idle_in_transaction_session_timeout")) == "5s"
        assert connection.scalar(text("SHOW TimeZone")) == "UTC"
        # build_engine no debe perder las opciones de aislamiento de los tests.
        assert connection.scalar(text("SHOW search_path")).startswith("tero_test_")


def test_lock_timeout_returns_503_and_rolls_back(pg_service, postgres_engine, payload):
    ingest(pg_service, payload)
    item = {**payload, "measurement_id": str(uuid4())}
    with postgres_engine.begin() as connection:
        connection.execute(text("SELECT id FROM servers WHERE id = :id FOR UPDATE"),
                           {"id": UUID(payload["server_id"])})
        with TestClient(create_app(pg_service)) as client:
            assert client.post("/metrics", json=item).status_code == 503
    assert len(pg_service.history(UUID(payload["server_id"]), 100, 0)) == 1
    assert not ingest(pg_service, item).duplicate


def test_statement_timeout_returns_503_and_connection_recovers(pg_service, monkeypatch):
    def slow_query(self, limit, offset):
        self.session.execute(text("SELECT pg_sleep(2)"))
        return []

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyRepository, "servers", slow_query)
        with TestClient(create_app(pg_service)) as client:
            assert client.get("/servers").status_code == 503
    with TestClient(create_app(pg_service)) as client:
        assert client.get("/servers").status_code == 200


def test_lost_response_outbox_restart_is_deduplicated_by_postgres(pg_service, payload, tmp_path):
    import requests
    from agent.delivery import Delivery
    from agent.outbox import Outbox

    attempts = []
    path = tmp_path / "outbox.sqlite3"
    box = Outbox(path)
    box.enqueue(payload)

    def send(item):
        with TestClient(create_app(pg_service)) as client:
            response = client.post("/metrics", json=item)
            assert response.status_code == 200
            attempts.append(response.json())
        if len(attempts) == 1:
            raise requests.ReadTimeout("Se perdió la respuesta luego del commit")

    Delivery(box, send, clock=lambda: 1000, uniform=lambda low, high: high).step()
    restarted = Outbox(path)
    assert restarted.count() == 1
    Delivery(restarted, send, clock=lambda: 1002).step()
    assert restarted.count() == 0
    assert [ack["duplicate"] for ack in attempts] == [False, True]
    assert attempts[0]["received_at"] == attempts[1]["received_at"]
    assert len(pg_service.history(UUID(payload["server_id"]), 100, 0)) == 1


@pytest.mark.parametrize("hostname", ["host\x00bad", "host\nbad", "host\x85bad", "host\u202ebad"])
def test_invalid_hostname_never_reaches_postgres(pg_service, payload, hostname):
    with TestClient(create_app(pg_service)) as client:
        assert client.post("/metrics", json={**payload, "hostname": hostname}).status_code == 422
    with pg_service.uow_factory() as uow:
        assert uow.repository.server(UUID(payload["server_id"])) is None
