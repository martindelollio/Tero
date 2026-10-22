# ADR 0002 — Identidad persistente e ingesta idempotente

- Estado: aceptado
- Fecha: 2026-10-21
- Actualización v0.2.1: el [ADR 0003](0003-outbox-y-presupuestos-hardening.md)
  sustituye la limitación de ausencia de spool/reintentos y agrega sesiones UTC.

## Contexto

IP y hostname cambian y no identifican una instalación. Una respuesta HTTP puede
perderse después del commit. Reenvíos y primeras ingestas concurrentes no deben
duplicar datos ni dejar servidores sin su medición por una falla intermedia.

## Decisión

El agente genera UUID4 por instalación y lo guarda fuera del repositorio, en el
directorio de estado del usuario. flock serializa procesos inicializando la misma
ruta; un temporal con fsync, replace atómico y fsync del directorio publica el
archivo completo. Un UUID corrupto es un error explícito. El archivo debe
conservarse entre reinicios y excluirse de imágenes clonadas.

Cada muestra agrega measurement_id UUID4, único globalmente. La API almacena los
campos numéricos en columnas tipadas y el hostname de esa muestra para comparar
reenvíos aunque el servidor haya cambiado de nombre. Valores opcionales ausentes
y null se normalizan; se comparan valores semánticos, no bytes JSON ni un hash.

Una transacción READ COMMITTED hace:

1. INSERT servidor ON CONFLICT DO NOTHING.
2. SELECT FOR UPDATE del servidor para serializar sus ingestas.
3. Consulta measurement_id: si contenido/servidor coinciden, devuelve el original;
   si difieren, falla con conflicto y rollback.
4. Inserta medición y actualiza hostname/last_seen.
5. Commit antes de retornar el resultado al endpoint; ante errores, rollback.

La PK de mediciones protege también frente a IDs concurrentes entre servidores
distintos; su violación se traduce a conflicto y revierte el servidor provisional.
FK, nullability y checks refuerzan la integridad de los datos.

Los timestamps son UTC del backend. El reloj de recepción se consulta luego del
lock. last_seen sólo cambia con mediciones nuevas: repetir una muestra antigua no
mantiene un equipo artificialmente online. El umbral de contacto es 90 segundos.

## Compatibilidad y seguridad

Se conserva GET /health, POST /metrics, HTTP 200 y el mensaje previo. Los nuevos
campos son obligatorios: payloads v0.1 reciben 422. Mantener una identidad anónima
compartida sería inconsistente con el registro de servidores; se exige actualizar
API/agente juntos. Las unidades GB decimales del agente original se conservan.

UUID **no es una credencial**. Registro automático y consultas son sólo para uso
local hasta incorporar autenticación por agente, autorización, HTTPS y límites.
No se incluye contraseña ni URL productiva en el código.

## Consecuencias y límites

Se prueban duplicados concurrentes, colisión global, atomicidad, aislamiento de
historial y estado. Los locks duran una transacción pequeña, pero limitan throughput
por servidor. No hay coordinación distribuida adicional ni necesidad de una cola.

La idempotencia protege reenvíos durante toda la retención actual (sin borrado).
Si se agrega retención habrá que definir la ventana de deduplicación. El agente
actual no hace reintentos ni spool durable: reporta fallas y continúa con una muestra
nueva; puede perder datos. Una futura política de reintentos deberá preservar el
payload y UUID originales. No se promete exactamente-una-vez en el transporte.

Clonar/borrar la identidad cambia la semántica de instalación. Sin autenticación
pueden falsificarse UUIDs; sin sincronización del reloj pueden alterarse estados.
La paginación offset puede desplazarse con ingestas concurrentes y la retención
indefinida requiere una política antes de un despliegue sostenido.
