from concurrent.futures import ThreadPoolExecutor
import fcntl
from threading import Event
from uuid import UUID

import pytest
import requests

from agent import client, main
from agent.identity import load_or_create_server_id
from agent.outbox import Outbox


def test_identity_survives_restarts_and_concurrent_initialization(tmp_path):
    path = tmp_path / "state" / "server_id"
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: load_or_create_server_id(path), range(20)))
    assert len(set(ids)) == 1
    assert ids[0] == UUID(path.read_text().strip())
    assert load_or_create_server_id(path) == ids[0]
    assert path.stat().st_mode & 0o777 == 0o600


def test_corrupt_identity_is_not_silently_replaced(tmp_path):
    path = tmp_path / "server_id"
    path.write_text("broken")
    with pytest.raises(ValueError):
        load_or_create_server_id(path)
    assert path.read_text() == "broken"


def test_payload_has_stable_server_and_new_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_system_metrics", lambda: {"cpu_percent": 1})
    server_id = load_or_create_server_id(tmp_path / "server_id")
    one, two = main.build_payload(server_id), main.build_payload(server_id)
    assert one["server_id"] == two["server_id"] == str(server_id)
    assert UUID(one["measurement_id"]) != UUID(two["measurement_id"])


def test_client_propagates_http_error(monkeypatch):
    response = requests.Response()
    response.status_code = 503
    response._content = b""
    response._content_consumed = True
    monkeypatch.setattr(client.requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(requests.HTTPError):
        client.send_metrics({})


def test_collection_continues_while_network_is_blocked(tmp_path, monkeypatch):
    stop, sending, release = Event(), Event(), Event()
    calls = []
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    server_id = load_or_create_server_id(tmp_path / "server_id")
    outbox.enqueue({"measurement_id": "already-pending"})

    def send(payload):
        sending.set()
        release.wait(timeout=3)
        raise requests.ConnectionError("API no disponible")

    def metrics():
        assert sending.wait(timeout=1)
        calls.append(1)
        if len(calls) == 4:
            stop.set()
            release.set()
        return {"cpu_percent": 1}

    monkeypatch.setattr(main, "get_system_metrics", metrics)
    monkeypatch.setattr(main, "send_metrics", send)
    monkeypatch.setattr(main, "INTERVAL", 0.01)
    try:
        main.run_agent(outbox, server_id, stop)
        assert len(calls) == 4
        assert outbox.count() == 5
    finally:
        release.set()


def test_second_agent_for_same_outbox_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("TERO_IDENTITY_PATH", str(tmp_path / "server_id"))
    monkeypatch.setenv("TERO_OUTBOX_PATH", str(tmp_path / "outbox.sqlite3"))
    with (tmp_path / "outbox.sqlite3.worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Ya hay un agente"):
            main.main()
    assert not (tmp_path / "outbox.sqlite3").exists()
