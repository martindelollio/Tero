"""Outbox FIFO durable y acotada. Cada operación usa su propia conexión SQLite."""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class Pending:
    sequence: int
    payload: dict


class Outbox:
    MAX_PAYLOAD_BYTES = 4096
    MAX_PAGES = 4096  # páginas de 4096 bytes: techo de 16 MiB para la base principal

    def __init__(self, path: Path, *, max_items: int = 2880, max_bytes: int = 8 * 1024 * 1024):
        if max_items < 1 or not self.MAX_PAYLOAD_BYTES <= max_bytes <= 8 * 1024 * 1024:
            raise ValueError("Límites de outbox inválidos")
        self.path = path
        self.max_items = max_items
        self.max_bytes = max_bytes
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if path.is_symlink():
                raise ValueError("La outbox no puede ser un enlace simbólico")
        else:
            os.close(fd)
        path.chmod(0o600)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS pending (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload TEXT NOT NULL,
                    bytes INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delivery_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    failures INTEGER NOT NULL,
                    next_attempt REAL NOT NULL
                );
                INSERT OR IGNORE INTO delivery_state VALUES (1, 0, 0);
            """)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=0.25)
        try:
            connection.execute("PRAGMA page_size=4096")
            if connection.execute("PRAGMA page_size").fetchone()[0] != 4096:
                raise ValueError("La outbox requiere páginas SQLite de 4096 bytes")
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute(f"PRAGMA max_page_count={self.MAX_PAGES}").fetchone()[0] > self.MAX_PAGES:
                raise ValueError("La outbox existente supera el límite físico")
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(self, payload: dict) -> int:
        serialized = json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True)
        size = len(serialized.encode("utf-8"))
        if size > self.MAX_PAYLOAD_BYTES:
            raise ValueError("La muestra supera el límite de 4096 bytes")
        dropped = 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count, total = connection.execute("SELECT count(*), coalesce(sum(bytes), 0) FROM pending").fetchone()
            while count >= self.max_items or total + size > self.max_bytes:
                sequence, old_size = connection.execute(
                    "SELECT sequence, bytes FROM pending ORDER BY sequence LIMIT 1",
                ).fetchone()
                connection.execute("DELETE FROM pending WHERE sequence = ?", (sequence,))
                count, total, dropped = count - 1, total - old_size, dropped + 1
            connection.execute("INSERT INTO pending(payload, bytes) VALUES (?, ?)", (serialized, size))
        return dropped

    def head(self) -> Pending | None:
        with self._connection() as connection:
            row = connection.execute("SELECT sequence, payload FROM pending ORDER BY sequence LIMIT 1").fetchone()
        return Pending(row[0], json.loads(row[1])) if row else None

    def acknowledge(self, sequence: int) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM pending WHERE sequence = ?", (sequence,))
            connection.execute("UPDATE delivery_state SET failures = 0, next_attempt = 0 WHERE id = 1")

    def retry_state(self) -> tuple[int, float]:
        with self._connection() as connection:
            return connection.execute("SELECT failures, next_attempt FROM delivery_state WHERE id = 1").fetchone()

    def defer(self, failures: int, next_attempt: float) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE delivery_state SET failures = ?, next_attempt = ? WHERE id = 1",
                (failures, next_attempt),
            )

    def count(self) -> int:
        with self._connection() as connection:
            return connection.execute("SELECT count(*) FROM pending").fetchone()[0]
