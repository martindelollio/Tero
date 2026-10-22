import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def database_url() -> str:
    url = os.environ.get("TERO_DATABASE_URL")
    if not url:
        raise RuntimeError("Definí TERO_DATABASE_URL (postgresql+psycopg://...)")
    return url


def build_engine(url: str, *, pool_size: int = 5, max_overflow: int = 5, pool_timeout: float = 0.5):
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg":
        raise ValueError("Tero requiere PostgreSQL con el driver psycopg")
    # Preservar search_path/opciones del entorno, pero imponer límites al final.
    options = parsed.query.get("options", "")
    options += " -c timezone=UTC -c statement_timeout=1500 -c lock_timeout=500 -c idle_in_transaction_session_timeout=5000"
    return create_engine(
        url, pool_pre_ping=True, pool_size=pool_size, max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        connect_args={
            "connect_timeout": 2,
            "options": options,
            "keepalives": 1,
            "keepalives_idle": 5,
            "keepalives_interval": 2,
            "keepalives_count": 2,
            "tcp_user_timeout": 5000,
        },
    )
