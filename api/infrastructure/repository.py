from dataclasses import asdict, fields
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from api.domain.models import DuplicateConflict, Measurement, MetricValues, Server
from api.infrastructure.models import MeasurementRow, ServerRow


def to_measurement(row: MeasurementRow) -> Measurement:
    values = MetricValues(**{field.name: getattr(row, field.name) for field in fields(MetricValues)})
    return Measurement(row.id, row.server_id, row.hostname, values, row.received_at)


def to_server(row: ServerRow) -> Server:
    return Server(row.id, row.hostname, row.created_at, row.last_seen)


class SqlAlchemyRepository:
    def __init__(self, session: Session):
        self.session = session

    def register_and_lock(self, server_id: UUID, hostname: str, now: datetime) -> None:
        self.session.execute(insert(ServerRow).values(
            id=server_id, hostname=hostname, created_at=now, last_seen=now,
        ).on_conflict_do_nothing(index_elements=[ServerRow.id]))
        self.session.execute(select(ServerRow.id).where(ServerRow.id == server_id).with_for_update()).one()

    def measurement(self, measurement_id: UUID) -> Measurement | None:
        row = self.session.get(MeasurementRow, measurement_id)
        return to_measurement(row) if row else None

    def add(self, measurement: Measurement) -> None:
        self.session.add(MeasurementRow(
            id=measurement.id, server_id=measurement.server_id, hostname=measurement.hostname,
            received_at=measurement.received_at, **asdict(measurement.values),
        ))
        self.session.flush()

    def touch(self, server_id: UUID, hostname: str, now: datetime) -> None:
        self.session.execute(update(ServerRow).where(ServerRow.id == server_id).values(
            hostname=hostname, last_seen=now,
        ))

    def servers(self, limit: int, offset: int) -> list[Server]:
        rows = self.session.scalars(select(ServerRow).order_by(ServerRow.id).limit(limit).offset(offset))
        return [to_server(row) for row in rows]

    def server(self, server_id: UUID) -> Server | None:
        row = self.session.get(ServerRow, server_id)
        return to_server(row) if row else None

    def history(self, server_id: UUID, limit: int, offset: int) -> list[Measurement]:
        rows = self.session.scalars(select(MeasurementRow).where(
            MeasurementRow.server_id == server_id,
        ).order_by(MeasurementRow.received_at.desc(), MeasurementRow.id.desc()).limit(limit).offset(offset))
        return [to_measurement(row) for row in rows]


class SqlAlchemyUnitOfWork:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions

    def __enter__(self):
        self.session = self.sessions()
        self.session.begin()
        self.repository = SqlAlchemyRepository(self.session)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
                if isinstance(exc_value, IntegrityError) and exc_value.orig.sqlstate == "23505":
                    raise DuplicateConflict("measurement_id ya utilizado") from exc_value
        except IntegrityError as error:
            self.session.rollback()
            if error.orig.sqlstate == "23505":
                raise DuplicateConflict("measurement_id ya utilizado") from error
            raise
        finally:
            self.session.close()
