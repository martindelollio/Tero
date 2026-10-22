# ADR 0001 — Monolito modular y dependencias hacia el dominio

- Estado: aceptado
- Fecha: 2026-10-21

## Contexto

La v0.1 sólo imprimía payloads en un endpoint. La v0.2 necesita persistencia y
consultas sin acoplar reglas de identidad, contacto y validación a FastAPI/ORM.

## Decisión

Separar presentación, aplicación, dominio e infraestructura dentro de una única
API. Dominio usa dataclasses inmutables, aplicación define Protocols específicos
Repository y UnitOfWork. Infraestructura implementa esos puertos con SQLAlchemy
2.x síncrono y PostgreSQL; presentación transforma esquemas HTTP. `main.py`
compone dependencias mediante una fábrica y lifespan, sin sesiones globales.

Los endpoints síncronos corren en el threadpool de FastAPI: no bloquean el loop
async con SQL síncrono. Cada caso de uso usa una sesión y transacción propia;
el pool tiene 5 conexiones y hasta 5 adicionales por proceso.

Registro ocurre durante la primera ingesta: no hay un caso de uso público de
registro separado sin necesidad actual. Consultas permiten listado, detalle
y métricas. El estado es una regla de dominio derivada de last_seen, no una
columna que necesite cron. La última medición se consulta por historial limit=1.

Entidades, tablas y esquemas se separan porque tienen responsabilidades distintas:
reglas, índices/relaciones y contrato externo. Validación HTTP temprana no sustituye
la validación de dominio (también se prueba sin HTTP ni base de datos).

## Consecuencias

SRP y DIP permiten probar casos de uso con un repositorio en memoria. Una única
interfaz de repositorio de monitoreo y una unidad de trabajo son suficientes:
no se introduce un repositorio genérico, CQRS, bus de comandos, microservicios,
cola ni jerarquía de servicios. No se duplica el estado de conexión en memoria.

Hay mapeo explícito entre capas, pero no frameworks adicionales. Lecturas acotadas
y el índice de historial evitan cargas ilimitadas; no se calculan totales mediante
COUNT en cada página. El offset es simple pero no escala indefinidamente.

SQLAlchemy/Alembic sólo soportan PostgreSQL en esta versión. Pruebas de integración
usan el motor real, no SQLite que ocultaría semántica de locks/upsert/timestamps.
No se modifica ninguna instalación de producción.
