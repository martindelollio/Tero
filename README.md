# Tero v0.2.1 — Hardening

«Si algo se cae, el Tero canta.» Monitoreo de servidores Linux mediante un agente
Python y una API FastAPI, con persistencia en PostgreSQL.

## Implementado

- UUID persistente por instalación del agente, independiente de IP y hostname.
- Registro automático del servidor con su primera medición válida.
- Métricas persistidas con SQLAlchemy 2.x y migraciones Alembic.
- Ingesta transaccional e idempotente, consulta de servidores, estado e historial.
- Pruebas de dominio, aplicación, HTTP, agente y PostgreSQL real.
- Outbox SQLite durable y acotada, con reintentos del mismo payload/UUID y envío
  separado de la recolección.
- Timeouts de PostgreSQL/pool, respuesta `503` ante saturación y rechazo de
  hostnames con caracteres de control.

## Arquitectura

```text
agent/                         recolección, identidad local y cliente HTTP
agent/outbox.py                 persistencia SQLite FIFO acotada
agent/delivery.py               política de entrega, backoff y worker
api/presentation/              rutas y esquemas HTTP (FastAPI/Pydantic)
api/application/               casos de uso y puertos Repository/UnitOfWork
api/domain/                    entidades y reglas, sin dependencias de frameworks
api/infrastructure/            SQLAlchemy, PostgreSQL y configuración
api/main.py                    composición e inyección de dependencias
api/migrations/                evolución del esquema con Alembic
tests/                         pruebas unitarias e integración
docs/adr/                      decisiones y compromisos de arquitectura
```

No se crean tablas al iniciar la API: las migraciones son un paso explícito.
No hay microservicios adicionales, colas, repositorios genéricos ni estado de
negocio global en memoria. Cada operación abre su propia sesión/transacción.

## Ejecución local

Requiere Python 3.11+ y PostgreSQL 14+ (verificado con Python 3.14).
Todos los comandos se ejecutan desde la raíz del repositorio y sin `sudo`.

### 1. Dependencias

Si los entornos ya existen, no hace falta recrearlos:

```bash
python -m venv api/.venv
api/.venv/bin/python -m pip install -r api/requirements.txt
python -m venv .venv
.venv/bin/python -m pip install -r agent/requirements.txt
```

### 2. PostgreSQL

Usá una base **local de desarrollo** y un rol propio con permiso para crear
tablas. Configurá la conexión por variable de entorno, nunca en archivos
versionados. El driver debe ser `postgresql+psycopg`:

```bash
export TERO_DATABASE_URL='postgresql+psycopg://usuario@127.0.0.1:5432/tero'
```

Si no tenés una base local, esta alternativa usa los binarios de PostgreSQL,
sin servicios del sistema ni puertos TCP. Inicializala **una sola vez**, en un
directorio nuevo:

```bash
export PGDATA="$HOME/.local/state/tero-dev/postgres"
mkdir -p "$(dirname "$PGDATA")"
initdb -D "$PGDATA" --encoding=UTF8 --no-locale --auth-local=trust --auth-host=reject
pg_ctl -D "$PGDATA" -l "$PGDATA/server.log" -o "-h '' -k $PGDATA -p 55432" -w start
createdb -h "$PGDATA" -p 55432 tero
export TERO_DATABASE_URL="postgresql+psycopg:///tero?host=$PGDATA&port=55432"
```

Esta alternativa confía en las conexiones por el socket dentro del directorio
privado del cluster (`0700`). Es exclusivamente para desarrollo, no para despliegues.
No vuelvas a ejecutar `initdb` sobre una base existente.

### 3. Migración y API

```bash
api/.venv/bin/python -m alembic upgrade head
api/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Documentación interactiva: <http://127.0.0.1:8000/docs>.
`GET /health` conserva su respuesta y es liveness, **no** verifica PostgreSQL.
La falta de `TERO_DATABASE_URL` impide iniciar la aplicación. Las fallas de
conexión y la saturación del pool al consultar/ingerir devuelven `503` con
`Retry-After: 1`, sin exponer la cadena de conexión.
Un esquema sin migrar es un error de instalación: ejecutá Alembic antes de servir.
La actualización v0.2 → v0.2.1 no agrega ni altera tablas PostgreSQL: la revisión
Alembic sigue siendo `0001`. Basta actualizar el código/dependencias y reiniciar
los procesos locales; `alembic upgrade head` también sirve para instalaciones nuevas.

### 4. Agente (otra terminal)

```bash
.venv/bin/python -m agent.main
```

También se conserva `python agent/main.py`. Configuración opcional:

- `TERO_API_URL`: URL completa de ingesta; por defecto `http://127.0.0.1:8000/metrics`.
- `TERO_IDENTITY_PATH`: archivo del UUID. Por defecto
  `$XDG_STATE_HOME/tero/server_id` o `~/.local/state/tero/server_id`.
- `TERO_OUTBOX_PATH`: SQLite de muestras pendientes; por defecto `outbox.sqlite3`
  junto al archivo de identidad. Usá un directorio local privado, no compartido
  ni un filesystem de red. Conservá la identidad y la outbox entre reinicios.

El UUID se escribe con permisos `0600`, bloqueo de inicialización y reemplazo
atómico. Si el archivo está corrupto, el agente falla explícitamente en vez de
inventar otro servidor. Conservá este archivo entre reinicios; **no lo copies a
otras instalaciones**. Cada instalación necesita su propia identidad.
La recolección usa un período objetivo de 30 segundos medido con reloj monotónico,
incluyendo el segundo de medición de CPU. Guarda cada payload en SQLite antes de
permitir su envío. Un thread de entrega realiza HTTP independientemente de la
recolección. La contención SQLite espera hasta 250 ms; fallas locales se registran
y no impiden continuar con el siguiente ciclo. Ctrl+C solicita cierre y espera
hasta un segundo al worker: un envío pendiente no impide salir ni elimina su fila.
Sólo se permite un proceso por ruta de outbox mediante `flock` no bloqueante.

### Buffer y política de entrega

- FIFO, hasta **2880 muestras** (aproximadamente 24 horas a 30 s) y **8 MiB de
  payloads UTF-8**, el límite que se alcance primero. Una muestra admite 4096 bytes.
- Al llenarse, se descartan las muestras **más antiguas** dentro de la misma
  transacción que inserta la nueva. Se registra la cantidad descartada. La base
  principal tiene un techo de 4096 páginas de 4096 bytes (16 MiB); el rollback
  journal y el filesystem requieren espacio adicional. No es un límite total
  exacto del espacio en disco. Se reutilizan las páginas liberadas, sin VACUUM
  durante la recolección ni crecimiento de WAL.
- SQLite usa `synchronous=FULL`, rollback journal y archivo nuevo `0600`. No se
  reinicializa una base corrupta silenciosamente. Si no se puede persistir una
  muestra (disco lleno, error de I/O o lock vencido), se registra el error: esa
  muestra puede perderse. No hay garantía frente a fallas físicas del disco.
- Ante errores de red, ACK inválido, `5xx`, `408/425/429` o problemas de configuración
  como `401/403/404/405`, la muestra permanece. Se reintenta **sin cambiar el
  measurement_id ni el contenido**. El contador y el próximo intento sobreviven
  al reinicio y al desborde del buffer.
- Backoff exponencial con *equal jitter*: primer intervalo aleatorio de 1–2 s,
  luego 2–4 s, 4–8 s, hasta 150–300 s. El worker consulta cada 500 ms mientras
  espera y limita el ritmo a cinco intentos/s. Una corrección de reloj hacia atrás
  no posterga el próximo intento más de 300 s. La política no interpreta
  `Retry-After` remoto; mantiene su propio backoff acotado.
- Sólo un `200` JSON con el measurement_id esperado y `duplicate` booleano es ACK
  válido. Si el servidor confirmó pero se perdió la respuesta, el reintento usa
  la deduplicación existente. Si el agente cae antes de eliminar la fila local,
  volverá a enviar la misma muestra, sin duplicar su almacenamiento en la API.
- `400/409/413/415/422` se consideran rechazos permanentes del payload: se registra
  ID y código y se descarta para no bloquear el FIFO. No hay dead-letter durable.
  Revisá estos errores en los logs; no se deben confundir con entrega exitosa.

La entrega es **al menos una vez mientras la muestra siga en el buffer**, sujeta
a que el destino se recupere y acepte el payload. No hay entrega garantizada
ilimitada. Un backlog grande tarda en drenarse; los registros guardan hora de
recepción en la API, no hora de recolección. Por eso las muestras recuperadas
aparecen agrupadas en el historial y el hostname puede reflejar muestras antiguas
hasta completar el drenaje. No se agregó un timestamp al contrato en esta versión.

### Presupuestos de espera

| Componente | Límite por defecto |
|---|---|
| Pool por proceso API | 5 conexiones + 5 de overflow; espera de 500 ms |
| Conexión libpq | 2 s por intento de conexión |
| Sentencia PostgreSQL | 1500 ms por sentencia |
| Lock PostgreSQL | 500 ms |
| Sesión ociosa en transacción | 5 s |
| HTTP del worker | conexión 3 s, lectura 15 s; sin redirects |
| Keepalive TCP PostgreSQL | idle 5 s, intervalo 2 s, 2 probes; `tcp_user_timeout=5000` |

El camino habitual de ingesta usa cinco sentencias cortas, por lo que se da margen
al HTTP respecto del pool, conexión y consultas. **No es un deadline global**:
los timeouts SQL se aplican a cada sentencia; DNS, múltiples hosts, commit, red
y SO pueden exceder una suma simple. Los timeouts de lectura HTTP limitan la
inactividad, no la duración total de una respuesta. Un envío lento no bloquea la
recolección porque ocurre en otro thread. Las sesiones PostgreSQL se configuran
en UTC y se preservan opciones como `search_path` al agregar los límites.

## Contrato HTTP v0.2

v0.2.1 conserva los campos y respuestas exitosas de v0.2; agrega validaciones y
respuestas transitorias controladas, sin requerir una migración de payloads.

### Ingesta

`POST /metrics` mantiene el path, el código `200` y el mensaje original, pero
**rompe compatibilidad con agentes v0.1**: ahora requiere `server_id`,
`measurement_id` y `hostname`. Actualizá API y agente juntos. No se asigna una
identidad compartida a payloads antiguos (reciben `422`). Se rechazan campos
desconocidos y métricas fuera de rango.

```json
{
  "server_id": "372076ef-4545-48c0-b938-a924884e08b8",
  "measurement_id": "e2295839-2203-43c8-a86c-57aa8e4f1961",
  "hostname": "mi-servidor",
  "cpu_percent": 12.5,
  "ram_percent": 42.0,
  "disk_percent": 60.0,
  "uptime_seconds": 12345,
  "ram_total": 8.0,
  "ram_free": 4.64,
  "disk_total": 100.0,
  "disk_free": 40.0
}
```

Los porcentajes deben estar entre 0 y 100, uptime es un entero no negativo
de 64 bits, capacidades son opcionales, finitas, no negativas y en **GB decimales**,
como en el agente original. `ram_free` representa memoria disponible de psutil.
Si llegan total y libre, libre no puede superar total.
Hostname tiene entre 1 y 253 caracteres, sin espacios externos.
Se rechazan NUL, controles C0/C1/DEL, controles Unicode de formato/bidi, surrogates
y separadores de línea/párrafo. Se permiten nombres Unicode imprimibles; no se
exige un nombre DNS. La regla vive en dominio y se aplica antes de abrir la
transacción. Los rechazos responden `422`.

La respuesta agrega `measurement_id`, `received_at` UTC y `duplicate`.
Un reenvío debe conservar **todo el contenido y el mismo measurement_id**:
devuelve `200`, `duplicate=true` y el timestamp original. Un ID reutilizado
con contenido diferente devuelve `409` y no deja registros parciales.
Los duplicados no refrescan `last_seen`, para que reproducir muestras antiguas
no simule contacto nuevo. `received_at` marca recepción, no hora de recolección.

### Consultas

| Endpoint | Resultado |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `GET /servers?limit=100&offset=0` | Array con `id`, `hostname`, `created_at`, `last_seen`, `status` |
| `GET /servers/{server_id}` | Detalle y estado; `404` si no existe |
| `GET /servers/{server_id}/metrics?limit=100&offset=0` | Historial descendente con métricas, IDs y `received_at`; `404` si no existe |

`online` significa que la última medición nueva llegó hace menos de 90 segundos.
`offline` significa pérdida de contacto, **no apagado físico**. No se persiste el
estado: se calcula al consultar. El primer elemento del historial es la última
medición. Las listas permiten `limit` de 1 a 500 y `offset` de 0 a 100000.
Servidores se ordenan por UUID; historial por timestamp e ID descendentes.
La paginación por offset no constituye un snapshot frente a ingestas concurrentes.

## Pruebas

```bash
api/.venv/bin/python -m pip install -r requirements-dev.txt
api/.venv/bin/python -m pytest -q
```

Sin PostgreSQL se ejecutan pruebas unitarias/HTTP y se omiten explícitamente las
de integración. Para ejecutarlas todas, apuntá a una base local de pruebas:

```bash
export TERO_TEST_DATABASE_URL="$TERO_DATABASE_URL"
api/.venv/bin/python -m pytest -q
```

Las pruebas crean un schema de nombre aleatorio, aplican migraciones, verifican
divergencias de modelos con `alembic check`, ejercitan concurrencia/rollback,
prueban downgrade/upgrade y eliminan **únicamente ese schema**. Necesitan permiso
`CREATE` en la base. Usá una base de desarrollo descartable, nunca producción.

Para comprobar el esquema local manualmente:

```bash
api/.venv/bin/python -m alembic current
api/.venv/bin/python -m alembic check
```

Para detener únicamente el cluster local opcional creado arriba:

```bash
pg_ctl -D "$PGDATA" -m fast -w stop
```

## Riesgos y límites

- **Sin autenticación todavía**: UUID identifica, no autentica. Cualquier cliente
  con acceso puede registrar o suplantar servidores y leer métricas. Escuchar
  sólo en loopback; no exponer fuera del equipo hasta incorporar credenciales
  por agente, autorización, HTTPS y límites de solicitudes/tamaño.
- No hay retención ni agregación: las métricas crecen indefinidamente. El índice
  compuesto sirve al historial, pero offset alto será caro. Antes de escalar:
  retención, paginación por cursor y mediciones de carga.
- PostgreSQL serializa ingestas por servidor con locks transaccionales; servidores
  distintos no se bloquean entre sí salvo colisión de measurement_id. Adecuado
  para el intervalo actual, sin prometer capacidad no medida.
- Los timestamps dependen del reloj UTC del backend (mantenerlo sincronizado).
  Configurar sesiones UTC no garantiza monotonía ni sincronización entre instancias.
- El buffer es finito y puede descartar datos. No hay retención de rechazos,
  métricas operacionales del worker ni timeout HTTP global duro. No hay jitter
  inicial de recolección: los arranques masivos todavía pueden generar ráfagas.
- Las dependencias tienen rangos de versiones, no un lock reproducible. La suite
  detecta incompatibilidades, pero queda pendiente fijar un lock y CI.
- No se implementan borrado, onboarding autenticado, frontend ni despliegue.

**Próximas tareas obligatorias antes de producción o crecimiento significativo:**
autenticación/autorización por agente y usuarios, HTTPS y límites de solicitudes;
retención con ventana explícita de deduplicación; paginación por cursor. También
quedan pendientes pruebas de carga, readiness, observabilidad y CI reproducible.

Ver las decisiones detalladas en [docs/adr](docs/adr/).
