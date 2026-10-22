# ADR 0003 — Outbox local y presupuestos de espera

- Estado: aceptado
- Fecha: 2026-10-21
- Actualiza: ADR 0001 y 0002 para Tero v0.2.1

## Contexto

La auditoría detectó descarte de muestras fallidas, esperas del pool mayores que
el timeout HTTP y entradas hostname incompatibles con PostgreSQL. Se preservan
el monolito modular, los puertos y el esquema PostgreSQL existente. Autenticación,
retención e historial por cursor son próximas tareas obligatorias, no parte de
esta iteración.

## Decisión: outbox SQLite

Agregar un único SQLite por instalación, sin servicios externos. El recolector
persiste antes de enviar. Un thread hace entrega FIFO; no hay conexiones SQLite
compartidas entre threads, cada operación tiene una conexión y transacción corta.
El proceso mantiene un flock exclusivo por ruta de outbox para no duplicar workers.

SQLite implementa almacenamiento local; `Delivery` implementa política de reintento
y clasificación de resultados; `client` conserva el HTTP. No se agrega Repository
genérico al agente porque sólo hay un mecanismo de buffer y la clase concreta es
pequeña y comprobable. Se inyectan transporte, reloj y aleatoriedad para las pruebas.
La API conserva las cuatro capas y su UnitOfWork.

Límites por defecto: 2880 filas, 8 MiB de JSON serializado, 4096 bytes por muestra.
El límite lógico se aplica dentro de `BEGIN IMMEDIATE`; si hace falta espacio, se
elimina el FIFO más antiguo junto con la inserción nueva. Si la transacción falla,
se revierten también las eliminaciones. Se loguea cualquier desborde. Se prefiere
preservar el monitoreo reciente antes que bloquear indefinidamente la recolección.

Se usa rollback journal, `synchronous=FULL`, espera de locks de 250 ms y techo de
4096 páginas de 4096 bytes (16 MiB) para el archivo principal creado por Tero. Los
journals y el filesystem agregan espacio: no se promete un límite exacto total.
No hay VACUUM en el ciclo ni un WAL que pueda crecer por lectores retenidos.

## Entrega y fallas

Se mantiene el payload completo y su UUID. Sólo un ACK HTTP 200 JSON con ID
coincidente y `duplicate` booleano elimina la fila. Caer entre ACK y borrado local
causa un reenvío, resuelto por la idempotencia PostgreSQL del ADR 0002. El descarte
por desborde puede alcanzar una fila que esté en vuelo: no se cancela el HTTP y no
se presupone que el destino no la haya recibido.

Errores de red, ACK inválidos, 5xx y rechazos de configuración conservan la muestra.
Los rechazos de payload 400/409/413/415/422 se loguean y descartan sin dead-letter
durable. Así un payload incompatible no bloquea toda la cola. La política es
explícita, no se considera ese descarte una entrega exitosa.

Se persiste backoff global para que un reinicio o la eliminación del FIFO por
desborde no lo reinicie. Equal jitter entre la mitad y el techo exponencial,
con techo creciente de 2 a 300 segundos; máximo cinco intentos/s al recuperar.
No se interpreta Retry-After remoto. Un retroceso del reloj acota la espera
pendiente a 300 s. La recolección usa reloj monotónico y período objetivo de 30 s.

El thread HTTP es daemon y el cierre espera como máximo un segundo. Un DNS o
destino defectuoso no frena la recolección ni la salida del proceso. Requests
limita conexión/lectura (3/15 s), pero no implementa deadline global. Durante un
envío atascado no hay drenaje; la política de desborde sigue aplicándose.

## Presupuesto de PostgreSQL

Por proceso: pool 5 + 5 overflow, checkout 500 ms. Libpq: conexión 2 s; PostgreSQL:
statement_timeout 1500 ms, lock_timeout 500 ms, idle-in-transaction 5 s. Keepalives
TCP y tcp_user_timeout ayudan a detectar red rota, sujetos al SO y tipo de socket.
El handler existente de OperationalError se completa con TimeoutError del pool;
devuelven 503 y Retry-After: 1 sin exponer conexiones ni SQL. Las transacciones
fallidas se revierten y cierran antes de volver al cliente.

Son presupuestos por operación, no un deadline transaccional universal compatible
con todas las versiones PostgreSQL soportadas. La ingesta habitual tiene cinco
sentencias cortas, con margen respecto a la lectura HTTP. El spool cubre la
ambigüedad de un commit completado cuya respuesta se pierda.

Las opciones preservan search_path y agregan timezone=UTC. Esto evita distintas
representaciones del mismo instante entre el ACK inicial y un reenvío. No resuelve
relojes regresivos ni desincronización entre backends.

## Hostnames

Validación compartida de dominio antes del UnitOfWork: longitud y espacios
externos del contrato original, más rechazo de categorías Unicode Cc, Cf, Cs, Zl
y Zp. Incluye NUL, C0/C1/DEL, bidi y separadores problemáticos. No se impone una
gramática DNS que rompa hostnames Unicode existentes. HTTP traduce el error a 422.
No se necesita migrar columnas ni eliminar datos existentes.

## Consecuencias y comprobación

Se agregan pruebas de reinicio real de conexión SQLite, backoff persistido,
desborde atómico, error de SQLite, ACK inválido, respuesta perdida tras commit,
recolección durante HTTP bloqueado, saturación real del pool y timeouts SQL/locks.
La integración PostgreSQL usa ahora build_engine para ejercer la configuración
real, además del camino normal de composición de la aplicación.

La entrega es al menos una vez sólo mientras la muestra se conserve y el destino
eventualmente acepte el payload. No cubre disco roto/lleno, corrupción ni caídas
anteriores al commit SQLite. FIFO agrega demora a muestras recientes al drenar.
No hay timestamp de recolección en el contrato: el historial recuperado se ordena
por recepción y puede mostrar hostname antiguo hasta completar la cola.

Siguen pendientes autenticación/autorización, retención con deduplicación y cursor
antes de producción/crecimiento significativo. También: observabilidad, readiness,
pruebas de carga, jitter inicial, lock de dependencias y CI. No se agregan colas
externas, servicios, migraciones destructivas ni cambios en Debian.
