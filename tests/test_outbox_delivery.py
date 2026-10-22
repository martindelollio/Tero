import json
import sqlite3
from uuid import uuid4

import pytest
import requests

from agent.client import InvalidAcknowledgement, send_metrics
from agent.delivery import Delivery
from agent.outbox import Outbox


def response_for(payload, status=200, **changes):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({
        "measurement_id": payload["measurement_id"], "duplicate": False, **changes,
    }).encode()
    response._content_consumed = True
    return response


def test_sqlite_survives_restart_preserving_original_payload(tmp_path, payload):
    path = tmp_path / "outbox.sqlite3"
    one = Outbox(path)
    one.enqueue(payload)
    one.defer(3, 1234)
    two = Outbox(path)
    assert two.head().payload == payload
    assert two.retry_state() == (3, 1234)
    assert path.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_bounded_fifo_discards_oldest_atomically(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3", max_items=2)
    assert outbox.enqueue(payload) == 0
    second = {**payload, "measurement_id": str(uuid4())}
    third = {**payload, "measurement_id": str(uuid4())}
    outbox.enqueue(second)
    outbox.defer(4, 999)
    assert outbox.enqueue(third) == 1
    assert outbox.count() == 2
    assert outbox.head().payload == second
    assert outbox.retry_state() == (4, 999)  # desborde no reinicia el backoff
    outbox.acknowledge(outbox.head().sequence)
    assert outbox.head().payload == third


def test_byte_limit_and_oversized_payload(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3", max_bytes=4096)
    large = {**payload, "padding": "x" * 2500}
    outbox.enqueue(large)
    assert outbox.enqueue({**large, "measurement_id": str(uuid4())}) == 1
    assert outbox.count() == 1
    with pytest.raises(ValueError):
        outbox.enqueue({**payload, "padding": "x" * 4096})
    assert outbox.count() == 1


def test_response_lost_then_restart_retries_identical_id(tmp_path, payload, service):
    from uuid import UUID
    from api.domain.models import MetricValues

    now = [1000.0]
    received = []

    def send(item):
        result = service.ingest(
            UUID(item["server_id"]), UUID(item["measurement_id"]), item["hostname"],
            MetricValues(**{k: v for k, v in item.items() if k not in {"server_id", "measurement_id", "hostname"}}),
        )
        received.append((item, result.duplicate))
        if len(received) == 1:
            raise requests.ReadTimeout("Respuesta perdida DESPUÉS del commit")

    path = tmp_path / "outbox.sqlite3"
    outbox = Outbox(path)
    outbox.enqueue(payload)
    first = Delivery(outbox, send, clock=lambda: now[0], uniform=lambda low, high: high)
    assert first.step()
    assert outbox.count() == 1
    assert outbox.retry_state() == (1, 1002)
    restarted = Outbox(path)
    second = Delivery(restarted, send, clock=lambda: now[0])
    assert not second.step()  # reinicio respeta el backoff
    now[0] = 1002
    assert second.step()
    assert restarted.count() == 0
    assert received == [(payload, False), (payload, True)]
    assert len(service.history(UUID(payload["server_id"]), 100, 0)) == 1


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504, 401, 403, 404, 405])
def test_transient_or_configuration_errors_retain_payload(tmp_path, payload, status):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(payload)

    def send(item):
        response_for(item, status).raise_for_status()

    delivery = Delivery(outbox, send, clock=lambda: 1000, uniform=lambda low, high: high)
    assert delivery.step()
    assert outbox.head().payload == payload
    assert outbox.retry_state() == (1, 1002)


@pytest.mark.parametrize("status", [400, 409, 413, 415, 422])
def test_permanently_invalid_payload_does_not_block_fifo(tmp_path, payload, status, caplog):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(payload)

    def send(item):
        response_for(item, status).raise_for_status()

    assert Delivery(outbox, send).step()
    assert outbox.count() == 0
    assert "descartada" in caplog.text
    assert payload["measurement_id"] in caplog.text


def test_exponential_backoff_and_jitter_are_bounded(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(payload)
    now, intervals = [1000.0], []

    def fail(item):
        raise requests.ConnectionError()

    def jitter(low, high):
        intervals.append((low, high))
        return low

    delivery = Delivery(outbox, fail, clock=lambda: now[0], uniform=jitter)
    for _ in range(12):
        assert delivery.step()
        failures, due = outbox.retry_state()
        assert due > now[0]
        now[0] = due
    assert intervals[:3] == [(1, 2), (2, 4), (4, 8)]
    assert intervals[-1] == (150, 300)
    assert outbox.count() == 1


@pytest.mark.parametrize("status,changes", [
    (200, {"measurement_id": "wrong"}), (200, {"duplicate": "false"}),
    (201, {}), (302, {}),
])
def test_invalid_ack_is_not_delivery(tmp_path, payload, monkeypatch, status, changes):
    monkeypatch.setattr("agent.client.requests.post", lambda *a, **kw: response_for(payload, status, **changes))
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(payload)
    assert Delivery(outbox, send_metrics).step()
    assert outbox.count() == 1


def test_client_uses_timeouts_and_accepts_duplicate_ack(payload, monkeypatch):
    def post(url, **kwargs):
        assert kwargs["json"] == payload
        assert kwargs["timeout"] == (3, 15)
        assert kwargs["allow_redirects"] is False
        return response_for(payload, duplicate=True)

    monkeypatch.setattr("agent.client.requests.post", post)
    assert send_metrics(payload).json()["duplicate"] is True


def test_non_json_ack_retains_sample(payload, monkeypatch):
    response = response_for(payload)
    response._content = b"not JSON"
    monkeypatch.setattr("agent.client.requests.post", lambda *a, **kw: response)
    with pytest.raises(InvalidAcknowledgement):
        send_metrics(payload)


def test_sqlite_write_failure_rolls_back_overflow_eviction(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3", max_items=1)
    outbox.enqueue(payload)
    with sqlite3.connect(outbox.path) as connection:
        connection.execute("""CREATE TRIGGER reject_insert BEFORE INSERT ON pending
                              BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        outbox.enqueue({**payload, "measurement_id": str(uuid4())})
    assert outbox.count() == 1
    assert outbox.head().payload == payload


def test_sqlite_lock_has_bounded_wait_and_retains_data(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(payload)
    with sqlite3.connect(outbox.path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            outbox.enqueue({**payload, "measurement_id": str(uuid4())})
    assert outbox.count() == 1


def test_successful_delivery_resets_persistent_backoff(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(payload)
    outbox.defer(8, 1000)
    assert Delivery(outbox, send=lambda item: None, clock=lambda: 1000).step()
    assert outbox.retry_state() == (0, 0)
    assert outbox.count() == 0


def test_clock_regression_does_not_defer_forever(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3")
    outbox.enqueue(payload)
    outbox.defer(8, 5000)
    assert not Delivery(outbox, clock=lambda: 1000).step()
    assert outbox.retry_state() == (8, 1300)


def test_corrupt_outbox_is_not_reinitialized(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    path.write_bytes(b"broken sqlite state")
    with pytest.raises(sqlite3.DatabaseError):
        Outbox(path)
    assert path.read_bytes() == b"broken sqlite state"


def test_ack_for_evicted_inflight_sample_does_not_remove_newer(tmp_path, payload):
    outbox = Outbox(tmp_path / "outbox.sqlite3", max_items=1)
    outbox.enqueue(payload)
    replacement = {**payload, "measurement_id": str(uuid4())}

    def send(item):
        assert item == payload
        assert outbox.enqueue(replacement) == 1

    assert Delivery(outbox, send).step()
    assert outbox.count() == 1
    assert outbox.head().payload == replacement
