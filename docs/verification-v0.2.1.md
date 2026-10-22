# Verificación de Tero v0.2.1 — Hardening

Entorno local: Python 3.14, PostgreSQL 18.6, SQLite de la biblioteca estándar,
SQLAlchemy 2.0.55, psycopg 3.3.6, FastAPI 0.159.1.

## Pruebas automatizadas

- Suite completa con PostgreSQL: **89 aprobadas, ninguna omitida**.
- Sin `TERO_TEST_DATABASE_URL`: **75 aprobadas, 14 omitidas** (integración real).
- Persistencia de payload y backoff al reabrir SQLite; reenvío del mismo UUID
  después de perder la respuesta a un commit exitoso, en memoria y PostgreSQL.
- Límites por filas/bytes, FIFO, rechazo de payload sobredimensionado y rollback
  de las eliminaciones si falla la inserción durante un desborde.
- Un ACK de una muestra en vuelo descartada por desborde no elimina la muestra
  nueva que la reemplazó.
- Lock SQLite con espera acotada, ACK incorrecto/no JSON, fallas transitorias y
  rechazos permanentes, backoff/jitter acotados y reinicio del backoff tras ACK.
- Rechazo de un segundo agente con la misma outbox y de una base SQLite corrupta
  sin reinicializarla ni borrar su contenido.
- Recolección de cuatro muestras mientras el transporte está bloqueado en otro
  thread; el envío no hace esperar al recolector.
- Saturación real del pool a través de la composición normal de FastAPI: 503,
  Retry-After y recuperación tras liberar la conexión.
- Timeouts SQL y de locks reales: 503, rollback y reutilización de conexiones.
- Opciones efectivas de sesión (UTC, timeouts, preservación de search_path).
- Hostnames NUL, controles C0/C1/DEL y Unicode problemático rechazados antes de
  abrir transacción; HTTP 422 y ausencia de registros PostgreSQL. Nombres Unicode
  imprimibles aceptados.
- Se conservan las pruebas de migraciones, concurrencia, deduplicación y dominio
  de v0.2. No hay una migración nueva ni cambios destructivos de esquema.

## Verificación manual real

Se usó únicamente el cluster temporal del usuario en `/tmp/opencode`, accesible
por socket Unix, sin servicios del sistema ni servidor Debian:

1. Ejecutar el agente real con el destino HTTP detenido y rutas de estado
   temporales: una muestra queda persistida en SQLite y registra backoff.
2. Detener el proceso con SIGINT: comprobar que la fila conserva el payload.
3. Levantar Uvicorn en loopback y reiniciar el proceso del agente con esa outbox.
4. Consultar el historial HTTP: aparece el measurement_id original en PostgreSQL.
5. Comprobar que la outbox se vacía sólo después de recibir el ACK válido.
6. Detener agente, API y cluster temporal al terminar.

`pip check` no detectó dependencias incompatibles en los entornos de API y agente.
Permanece un aviso de deprecación Starlette/httpx del cliente de pruebas, sin
fallas. No se silenció ni se agregó una dependencia para ocultarlo.

## No validado / pendiente

No se simularon cortes físicos de energía, corrupción del filesystem ni un
servidor HTTP que envíe bytes indefinidamente. No hay pruebas de carga con
100/1000/10000 agentes ni medición de capacidad. El buffer no sustituye esas
pruebas ni garantiza entrega frente a desborde, disco lleno o payload rechazado.

Autenticación/autorización, retención/ventana de deduplicación y paginación por
cursor siguen siendo tareas obligatorias antes de producción o crecimiento
significativo. También faltan readiness, observabilidad, jitter inicial y CI/lock.
