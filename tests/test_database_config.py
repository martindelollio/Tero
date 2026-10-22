import pytest

from api.infrastructure.database import build_engine


def test_engine_has_bounded_pool_and_connection_settings(monkeypatch):
    captured = {}

    def create(url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("api.infrastructure.database.create_engine", create)
    build_engine("postgresql+psycopg://localhost/tero?options=-csearch_path%3Dprivate")
    assert captured["pool_timeout"] == 0.5
    assert captured["pool_size"] == captured["max_overflow"] == 5
    args = captured["connect_args"]
    assert args["connect_timeout"] == 2
    assert args["tcp_user_timeout"] == 5000
    assert "search_path=private" in args["options"]
    assert "statement_timeout=1500" in args["options"]
    assert "lock_timeout=500" in args["options"]
    assert "timezone=UTC" in args["options"]


def test_engine_rejects_unsupported_database():
    with pytest.raises(ValueError):
        build_engine("sqlite://")
