"""Política de entrega independiente del ciclo de recolección."""

import logging
import random
import time
from threading import Event
from typing import Callable

import requests

if __package__:
    from .client import send_metrics
    from .outbox import Outbox
else:
    from client import send_metrics
    from outbox import Outbox

logger = logging.getLogger(__name__)


class Delivery:
    def __init__(self, outbox: Outbox, send: Callable = send_metrics, clock: Callable = time.time,
                 uniform: Callable = random.uniform):
        self.outbox = outbox
        self.send = send
        self.clock = clock
        self.uniform = uniform

    def step(self) -> bool:
        """Un intento como máximo; True si hubo trabajo, False si debe esperar."""
        failures, due = self.outbox.retry_state()
        # Acotar timestamps futuros incluso si el reloj retrocedió tras un reinicio.
        now = self.clock()
        if due > now + 300:
            due = now + 300
            self.outbox.defer(failures, due)
        if now < due:
            return False
        pending = self.outbox.head()
        if pending is None:
            return False
        try:
            self.send(pending.payload)
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            if status in {400, 409, 413, 415, 422}:
                logger.error("Muestra %s descartada por rechazo HTTP permanente %s",
                             pending.payload["measurement_id"], status)
                self.outbox.acknowledge(pending.sequence)
                return True
            self._defer(failures, pending.payload["measurement_id"])
        except requests.RequestException:
            self._defer(failures, pending.payload["measurement_id"])
        else:
            self.outbox.acknowledge(pending.sequence)
        return True

    def _defer(self, failures: int, measurement_id: str) -> None:
        failures = min(failures + 1, 32)
        ceiling = min(300, 2 * 2 ** min(failures - 1, 8))
        delay = self.uniform(ceiling / 2, ceiling)  # equal jitter: no bucle de demora cero
        self.outbox.defer(failures, self.clock() + delay)
        logger.warning("Envío pendiente %s; reintento en %.1fs (falla %s)", measurement_id, delay, failures)

    def run(self, stop: Event) -> None:
        while not stop.is_set():
            try:
                worked = self.step()
            except Exception:
                # No ocultar errores locales ni convertirlos en pérdida silenciosa.
                logger.exception("Error del worker; la muestra permanece en SQLite")
                stop.wait(1)
            else:
                stop.wait(0.2 if worked else 0.5)  # máximo cinco intentos/s por agente
