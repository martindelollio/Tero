from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ServerRow(Base):
    __tablename__ = "servers"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(253))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MeasurementRow(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        CheckConstraint("cpu_percent BETWEEN 0 AND 100", name="ck_cpu_percent"),
        CheckConstraint("ram_percent BETWEEN 0 AND 100", name="ck_ram_percent"),
        CheckConstraint("disk_percent BETWEEN 0 AND 100", name="ck_disk_percent"),
        CheckConstraint("uptime_seconds >= 0", name="ck_uptime"),
        CheckConstraint("ram_total >= 0 AND ram_free >= 0 AND ram_free <= ram_total", name="ck_ram_capacity"),
        CheckConstraint("disk_total >= 0 AND disk_free >= 0 AND disk_free <= disk_total", name="ck_disk_capacity"),
        Index("ix_measurements_server_received", "server_id", "received_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    server_id: Mapped[UUID] = mapped_column(ForeignKey("servers.id"))
    hostname: Mapped[str] = mapped_column(String(253))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cpu_percent: Mapped[float] = mapped_column(Float)
    ram_percent: Mapped[float] = mapped_column(Float)
    disk_percent: Mapped[float] = mapped_column(Float)
    uptime_seconds: Mapped[int] = mapped_column(BigInteger)
    ram_total: Mapped[float | None] = mapped_column(Float)
    ram_free: Mapped[float | None] = mapped_column(Float)
    disk_total: Mapped[float | None] = mapped_column(Float)
    disk_free: Mapped[float | None] = mapped_column(Float)
