"""Identidad local. flock serializa inicialización y replace publica el UUID completo."""

import fcntl
import os
from pathlib import Path
import tempfile
from uuid import UUID, uuid4


def default_identity_path() -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return Path(os.environ.get("TERO_IDENTITY_PATH", str(state / "tero" / "server_id")))


def load_or_create_server_id(path: Path | None = None) -> UUID:
    path = path or default_identity_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            # No regenerar silenciosamente una identidad corrupta.
            return UUID(path.read_text().strip())
        server_id = uuid4()
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".server_id-")
        try:
            with os.fdopen(fd, "w") as output:
                output.write(str(server_id) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return server_id
