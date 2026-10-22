"""Composition root. Ejecutar desde la raíz: uvicorn api.main:app."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout
from sqlalchemy.orm import sessionmaker

from api.application.services import MonitoringService
from api.domain.models import DuplicateConflict, InvalidMeasurement, ServerNotFound
from api.infrastructure.database import build_engine, database_url
from api.infrastructure.repository import SqlAlchemyUnitOfWork
from api.presentation.routes import router

logger = logging.getLogger(__name__)


def create_app(service: MonitoringService | None = None, url: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = None
        if service is None:
            engine = build_engine(url or database_url())
            sessions = sessionmaker(engine, expire_on_commit=False)
            app.state.monitoring_service = MonitoringService(lambda: SqlAlchemyUnitOfWork(sessions))
        else:
            app.state.monitoring_service = service
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(title="Tero API", version="0.2.1", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(ServerNotFound)
    async def not_found(request: Request, error: ServerNotFound):
        return JSONResponse(status_code=404, content={"detail": "Servidor no encontrado"})

    @app.exception_handler(DuplicateConflict)
    async def conflict(request: Request, error: DuplicateConflict):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(InvalidMeasurement)
    async def invalid(request: Request, error: InvalidMeasurement):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(OperationalError)
    @app.exception_handler(PoolTimeout)
    async def unavailable(request: Request, error: Exception):
        logger.warning("PostgreSQL no disponible/saturado: %s", type(error).__name__)
        return JSONResponse(
            status_code=503, content={"detail": "Base de datos no disponible o saturada"},
            headers={"Retry-After": "1"},
        )

    return app


app = create_app()
