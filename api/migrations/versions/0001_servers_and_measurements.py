"""Registro de servidores e ingesta idempotente."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "servers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "measurements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("server_id", sa.Uuid(), sa.ForeignKey("servers.id"), nullable=False),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=False),
        sa.Column("ram_percent", sa.Float(), nullable=False),
        sa.Column("disk_percent", sa.Float(), nullable=False),
        sa.Column("uptime_seconds", sa.BigInteger(), nullable=False),
        sa.Column("ram_total", sa.Float()),
        sa.Column("ram_free", sa.Float()),
        sa.Column("disk_total", sa.Float()),
        sa.Column("disk_free", sa.Float()),
        sa.CheckConstraint("cpu_percent BETWEEN 0 AND 100", name="ck_cpu_percent"),
        sa.CheckConstraint("ram_percent BETWEEN 0 AND 100", name="ck_ram_percent"),
        sa.CheckConstraint("disk_percent BETWEEN 0 AND 100", name="ck_disk_percent"),
        sa.CheckConstraint("uptime_seconds >= 0", name="ck_uptime"),
        sa.CheckConstraint("ram_total >= 0 AND ram_free >= 0 AND ram_free <= ram_total", name="ck_ram_capacity"),
        sa.CheckConstraint("disk_total >= 0 AND disk_free >= 0 AND disk_free <= disk_total", name="ck_disk_capacity"),
    )
    op.create_index("ix_measurements_server_received", "measurements", ["server_id", "received_at", "id"])


def downgrade():
    op.drop_index("ix_measurements_server_received", table_name="measurements")
    op.drop_table("measurements")
    op.drop_table("servers")
