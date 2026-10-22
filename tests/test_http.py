from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from api.main import create_app


def test_http_contract(service, payload):
    with TestClient(create_app(service)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        response = client.post("/metrics", json=payload)
        assert response.status_code == 200
        assert response.json()["message"] == "Métricas recibidas correctamente"
        assert response.json()["duplicate"] is False
        assert client.post("/metrics", json=payload).json()["duplicate"] is True
        assert client.post("/metrics", json={**payload, "cpu_percent": 99}).status_code == 409
        servers = client.get("/servers").json()
        assert len(servers) == 1
        assert servers[0]["status"] == "online"
        path = f"/servers/{payload['server_id']}"
        assert client.get(path).json()["id"] == payload["server_id"]
        metric = client.get(path + "/metrics").json()[0]
        assert metric["ram_total"] == 8
        assert metric["measurement_id"] == payload["measurement_id"]
        assert metric["received_at"].endswith("Z")
        assert client.get(path + "/metrics?offset=1").json() == []
        assert client.get("/servers?limit=501").status_code == 422
        assert client.get(path + "/metrics?offset=-1").status_code == 422
        assert client.get(f"/servers/{uuid4()}").status_code == 404
        assert client.get(f"/servers/{uuid4()}/metrics").status_code == 404
        assert client.get("/servers/not-a-uuid").status_code == 422


def test_payload_validation(service, payload):
    with TestClient(create_app(service)) as client:
        for change in ({"cpu_percent": 101}, {"ram_free": 99}, {"server_id": "bad"},
                       {"unknown": 1}, {"hostname": " "}, {"uptime_seconds": 1.5}):
            assert client.post("/metrics", json={**payload, **change}).status_code == 422
        legacy = {k: v for k, v in payload.items() if k not in {"server_id", "measurement_id", "hostname"}}
        assert client.post("/metrics", json=legacy).status_code == 422
        assert client.get("/servers").json() == []


@pytest.mark.parametrize("hostname", ["host\x00bad", "host\nbad", "host\x7fbad", "host\x85bad", "host\u202ebad"])
def test_http_rejects_control_characters(service, payload, hostname):
    with TestClient(create_app(service)) as client:
        assert client.post("/metrics", json={**payload, "hostname": hostname}).status_code == 422
        assert client.get("/servers").json() == []


@pytest.mark.parametrize("hostname", ["node-01.example", "máquina-ñ", "服务器"])
def test_http_accepts_printable_hostnames(service, payload, hostname):
    with TestClient(create_app(service)) as client:
        assert client.post("/metrics", json={**payload, "hostname": hostname}).status_code == 200
