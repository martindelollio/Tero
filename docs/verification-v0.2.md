# Verificación de Tero v0.2

Fecha: 2026-10-21. Entorno local: Python 3.14, PostgreSQL 18.6,
SQLAlchemy 2.0.55, Alembic 1.20.0, psycopg 3.3.6, FastAPI 0.159.1.

## Resultados

- Suite completa con `TERO_TEST_DATABASE_URL`: **26 pruebas aprobadas**.
- Sin esa variable: **21 aprobadas, 5 omitidas** (integración PostgreSQL).
- Alembic: upgrade inicial, comparación de metadatos sin diferencias,
  downgrade a base y upgrade nuevamente, dentro de un schema temporal aislado.
- Pruebas concurrentes: 16 envíos de la misma muestra producen una fila;
  una colisión global entre dos servidores acepta uno y revierte el otro.
- Falla simulada luego de insertar la métrica: rollback de servidor y medición.
- Pruebas de negocio: validación de porcentajes/capacidades/uptime, estado en el
  umbral de 90 segundos, cambio de hostname, duplicados sin refrescar contacto,
  rechazo de conflictos y servidores inexistentes.
- Agente: inicialización concurrente y persistencia de identidad, archivo
  corrupto, IDs de muestra distintos y continuidad después de falla de conexión.
- `python -m pip check` en el entorno de API: sin dependencias incompatibles.

## Verificación real de extremo a extremo

Se levantó un cluster PostgreSQL temporal del usuario, sin TCP ni servicios del
sistema. Se aplicó la migración y se inició Uvicorn en `127.0.0.1:18543`.
Se ejecutó el agente real con su propio entorno virtual e identidad temporal:

1. Dos ciclos de recolección con psutil, separados aproximadamente 31 segundos.
2. Ambos POST recibieron HTTP 200.
3. El historial HTTP devolvió dos filas persistidas con el mismo server_id y
   distintos measurement_id, incluyendo capacidades de RAM/disco.
4. El detalle indicó `online`.
5. El agente terminó correctamente con SIGINT; la API y el cluster temporal
   fueron detenidos al finalizar la verificación.

No se usó ni modificó el servidor Debian; no se hicieron commits ni pushes.

## Aviso pendiente

Starlette 1.9 emite un aviso de deprecación al usar su TestClient con httpx:
recomienda httpx2 para una futura transición. Las pruebas pasan; no se silenció
el aviso ni se agregó una dependencia nueva sólo para ocultarlo.
No se realizaron pruebas de carga, seguridad externa ni entrega durable offline.
