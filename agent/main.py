import fcntl
import logging
import os
from pathlib import Path
import socket
from threading import Event, Thread
import time
from uuid import uuid4

if __package__:
    from .metrics import get_system_metrics
    from .client import send_metrics
    from .identity import load_or_create_server_id
    from .delivery import Delivery
    from .outbox import Outbox
else:
    from metrics import get_system_metrics
    from client import send_metrics
    from identity import load_or_create_server_id
    from delivery import Delivery
    from outbox import Outbox

INTERVAL = 30


def build_payload(server_id):
    return {
        **get_system_metrics(),
        "server_id": str(server_id),
        "measurement_id": str(uuid4()),
        "hostname": socket.gethostname(),
    }


logger = logging.getLogger(__name__)


def collect_once(outbox, server_id):
    dropped = outbox.enqueue(build_payload(server_id))
    if dropped:
        logger.warning("Outbox llena: %s muestras antiguas descartadas", dropped)


def run_agent(outbox, server_id, stop):
    worker = Thread(target=Delivery(outbox, send=send_metrics).run, args=(stop,), daemon=True)
    worker.start()

    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                collect_once(outbox, server_id)
            except Exception:
                logger.exception("No se pudo recolectar/persistir la muestra")
            stop.wait(max(0, INTERVAL - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(timeout=1)
        # Un envío todavía bloqueado no impide salir; su fila ya está persistida.


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server_id = load_or_create_server_id()
    if __package__:
        from .identity import default_identity_path
    else:
        from identity import default_identity_path
    path = Path(os.environ.get("TERO_OUTBOX_PATH", str(default_identity_path().with_name("outbox.sqlite3"))))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_name(path.name + ".worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Ya hay un agente usando esta outbox") from error
        outbox = Outbox(path)
        logger.info("Tero Agent iniciado; servidor %s", server_id)
        run_agent(outbox, server_id, Event())
        logger.info("Tero Agent detenido; pendientes conservados")


if __name__ == "__main__":
    main()
