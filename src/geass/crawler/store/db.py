"""Base SQLite del crawler: conexión, migraciones y funciones de repositorio."""

import json
import sqlite3
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, get_args

from geass.core.errors import GeassError
from geass.core.log import get_logger
from geass.core.paths import ensure_dir, resolve_project_path
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.store.migrations import MIGRATIONS, VERSION_TABLE, Migration
from geass.crawler.store.records import (
    ActionType,
    AppRecord,
    ElementInput,
    ElementRecord,
    ScreenshotRecord,
    SessionMode,
    SessionRecord,
    StateRecord,
    TransitionRecord,
    TransitionSource,
)
from geass.crawler.uia.schema import Rect

logger = get_logger(__name__)

Clock = Callable[[], datetime]


class StoreError(GeassError):
    """Problema con la base del crawler."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class CrawlerStore:
    """Almacenamiento del crawler en un archivo SQLite.

    Se puede usar como gestor de contexto. Los métodos de escritura son atómicos y se
    pueden llamar desde varios hilos.
    """

    def __init__(
        self,
        path: Path,
        *,
        clock: Clock = utc_now,
        migrations: Sequence[Migration] = MIGRATIONS,
    ) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.RLock()
        ensure_dir(path.parent)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        try:
            self._migrate(migrations)
        except Exception:
            self._conn.close()
            raise

    @classmethod
    def from_config(cls, config: CrawlerConfig, *, clock: Clock = utc_now) -> "CrawlerStore":
        """Abre la base en la ruta ``store.db_path`` de ``crawler.yaml``."""
        return cls(resolve_project_path(config.store.db_path), clock=clock)

    def __enter__(self) -> "CrawlerStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Versión y migraciones

    def schema_version(self) -> int:
        """Versión del esquema aplicada en este archivo."""
        row = self._conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
        return int(row["version"] or 0)

    def _migrate(self, migrations: Sequence[Migration]) -> None:
        versions = [migration.version for migration in migrations]
        if versions != list(range(1, len(versions) + 1)):
            raise StoreError("Las migraciones deben numerarse 1, 2, 3... sin saltos.")
        with self._lock:
            self._conn.execute(VERSION_TABLE)
            current = self.schema_version()
            latest = versions[-1] if versions else 0
            if current > latest:
                raise StoreError(
                    f"La base {self.path.name} tiene el esquema {current}, más nuevo que el que "
                    f"conoce este código ({latest}). Actualiza GEASS antes de abrirla."
                )
            for migration in migrations[current:]:
                self._conn.execute("BEGIN")
                try:
                    for statement in migration.statements:
                        self._conn.execute(statement)
                    self._conn.execute(
                        "INSERT INTO schema_version (version, description, applied_at) "
                        "VALUES (?, ?, ?)",
                        (migration.version, migration.description, self._now()),
                    )
                except Exception as error:
                    self._conn.execute("ROLLBACK")
                    raise StoreError(
                        f"Falló la migración {migration.version} ({migration.description}): {error}"
                    ) from error
                self._conn.execute("COMMIT")
                logger.info("Base del crawler migrada al esquema %d", migration.version)

    # Sesiones

    def start_session(self, mode: SessionMode, notes: str = "") -> int:
        """Abre una sesión y devuelve su id."""
        _check(mode, SessionMode, "modo de sesión")
        return self._insert(
            "INSERT INTO sessions (started_at, mode, notes) VALUES (?, ?, ?)",
            (self._now(), mode, notes),
        )

    def end_session(self, session_id: int, notes: str | None = None) -> SessionRecord:
        """Cierra una sesión. Si se dan notas, reemplazan a las anteriores."""
        with self._transaction():
            self._require("sessions", session_id)
            self._conn.execute(
                "UPDATE sessions SET ended_at = ?, notes = COALESCE(?, notes) WHERE id = ?",
                (self._now(), notes, session_id),
            )
        return self.get_session(session_id)

    def get_session(self, session_id: int) -> SessionRecord:
        return SessionRecord(**self._require("sessions", session_id))

    def list_sessions(self) -> list[SessionRecord]:
        return [SessionRecord(**dict(row)) for row in self._all("SELECT * FROM sessions")]

    # Apps

    def upsert_app(
        self, executable: str, display_name: str | None = None, app_user_model_id: str | None = None
    ) -> AppRecord:
        """Registra una app o actualiza sus datos. El ejecutable se guarda en minúsculas."""
        key = executable.strip().lower()
        if not key:
            raise StoreError("El ejecutable de la app no puede estar vacío.")
        with self._transaction():
            row = self._one("SELECT * FROM apps WHERE executable = ?", (key,))
            if row is None:
                app_id = self._insert(
                    "INSERT INTO apps (executable, display_name, app_user_model_id) "
                    "VALUES (?, ?, ?)",
                    (key, display_name or executable.strip(), app_user_model_id),
                )
            else:
                app_id = int(row["id"])
                self._conn.execute(
                    "UPDATE apps SET display_name = COALESCE(?, display_name), "
                    "app_user_model_id = COALESCE(?, app_user_model_id) WHERE id = ?",
                    (display_name, app_user_model_id, app_id),
                )
        return self.get_app(app_id)

    def get_app(self, app_id: int) -> AppRecord:
        return AppRecord(**self._require("apps", app_id))

    def find_app(self, executable: str) -> AppRecord | None:
        row = self._one("SELECT * FROM apps WHERE executable = ?", (executable.strip().lower(),))
        return AppRecord(**dict(row)) if row else None

    def list_apps(self) -> list[AppRecord]:
        return [AppRecord(**dict(row)) for row in self._all("SELECT * FROM apps")]

    # Estados

    def upsert_state(
        self,
        app_id: int,
        structural_signature: str,
        detailed_signature: str,
        title_pattern: str,
        *,
        screenshot_path: str | None = None,
    ) -> StateRecord:
        """Registra una visita a un estado.

        Un estado se identifica por la app y la firma estructural. Si ya existe, se suma
        una visita y se actualizan la última vez vista, la firma detallada y el título.
        """
        now = self._now()
        with self._transaction():
            self._require("apps", app_id)
            row = self._one(
                "SELECT id FROM states WHERE app_id = ? AND structural_signature = ?",
                (app_id, structural_signature),
            )
            if row is None:
                state_id = self._insert(
                    "INSERT INTO states (app_id, structural_signature, detailed_signature, "
                    "title_pattern, first_seen, last_seen, visits, screenshot_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        app_id,
                        structural_signature,
                        detailed_signature,
                        title_pattern,
                        now,
                        now,
                        screenshot_path,
                    ),
                )
            else:
                state_id = int(row["id"])
                self._conn.execute(
                    "UPDATE states SET visits = visits + 1, last_seen = ?, "
                    "detailed_signature = ?, title_pattern = ?, "
                    "screenshot_path = COALESCE(?, screenshot_path) WHERE id = ?",
                    (now, detailed_signature, title_pattern, screenshot_path, state_id),
                )
        return self.get_state(state_id)

    def get_state(self, state_id: int) -> StateRecord:
        return StateRecord(**self._require("states", state_id))

    def find_state(self, app_id: int, structural_signature: str) -> StateRecord | None:
        row = self._one(
            "SELECT * FROM states WHERE app_id = ? AND structural_signature = ?",
            (app_id, structural_signature),
        )
        return StateRecord(**dict(row)) if row else None

    def list_states(self, app_id: int | None = None) -> list[StateRecord]:
        if app_id is None:
            rows = self._all("SELECT * FROM states")
        else:
            rows = self._all("SELECT * FROM states WHERE app_id = ?", (app_id,))
        return [StateRecord(**dict(row)) for row in rows]

    # Elementos

    def upsert_elements(self, state_id: int, elements: Sequence[ElementInput]) -> dict[str, int]:
        """Guarda los elementos de un estado y devuelve el id de cada ruta.

        Un elemento se identifica por el estado y su ruta. Los que ya existían conservan su
        id y se actualizan; los que ya no aparecen no se borran, para no romper las
        transiciones que los usan.
        """
        with self._transaction():
            self._require("states", state_id)
            for element in elements:
                rect = element.rect
                self._conn.execute(
                    "INSERT INTO elements (state_id, path, control_type, name, automation_id, "
                    "rect_left, rect_top, rect_right, rect_bottom, patterns, enabled) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (state_id, path) DO UPDATE SET "
                    "control_type = excluded.control_type, name = excluded.name, "
                    "automation_id = excluded.automation_id, rect_left = excluded.rect_left, "
                    "rect_top = excluded.rect_top, rect_right = excluded.rect_right, "
                    "rect_bottom = excluded.rect_bottom, patterns = excluded.patterns, "
                    "enabled = excluded.enabled",
                    (
                        state_id,
                        element.path,
                        element.control_type,
                        element.name,
                        element.automation_id,
                        rect.left if rect else None,
                        rect.top if rect else None,
                        rect.right if rect else None,
                        rect.bottom if rect else None,
                        json.dumps(element.patterns),
                        int(element.enabled),
                    ),
                )
            rows = self._all("SELECT id, path FROM elements WHERE state_id = ?", (state_id,))
        wanted = {element.path for element in elements}
        return {row["path"]: int(row["id"]) for row in rows if row["path"] in wanted}

    def list_elements(self, state_id: int) -> list[ElementRecord]:
        rows = self._all("SELECT * FROM elements WHERE state_id = ?", (state_id,))
        return [_element(row) for row in rows]

    def get_element(self, element_id: int) -> ElementRecord:
        row = self._one("SELECT * FROM elements WHERE id = ?", (element_id,))
        if row is None:
            raise StoreError(f"No existe el registro {element_id} en elements.")
        return _element(row)

    # Transiciones

    def record_transition(
        self,
        source_state_id: int,
        target_state_id: int,
        action_type: ActionType,
        source: TransitionSource,
        *,
        element_id: int | None = None,
        latency_s: float | None = None,
    ) -> TransitionRecord:
        """Registra una transición observada.

        Si ya existía la misma (origen, destino, elemento, acción y fuente), suma una al
        conteo y actualiza la latencia media con la nueva medida.
        """
        _check(action_type, ActionType, "tipo de acción")
        _check(source, TransitionSource, "fuente de la transición")
        if latency_s is not None and latency_s < 0:
            raise StoreError("La latencia no puede ser negativa.")
        now = self._now()
        with self._transaction():
            self._require("states", source_state_id)
            self._require("states", target_state_id)
            if element_id is not None:
                self._require("elements", element_id)
            row = self._one(
                "SELECT * FROM transitions WHERE source_state_id = ? AND target_state_id = ? "
                "AND element_id IS ? AND action_type = ? AND source = ?",
                (source_state_id, target_state_id, element_id, action_type, source),
            )
            if row is None:
                transition_id = self._insert(
                    "INSERT INTO transitions (source_state_id, target_state_id, element_id, "
                    "action_type, source, count, mean_latency_s, latency_samples, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                    (
                        source_state_id,
                        target_state_id,
                        element_id,
                        action_type,
                        source,
                        latency_s,
                        0 if latency_s is None else 1,
                        now,
                    ),
                )
            else:
                transition_id = int(row["id"])
                mean, samples = row["mean_latency_s"], int(row["latency_samples"])
                if latency_s is not None:
                    samples += 1
                    mean = latency_s if mean is None else mean + (latency_s - mean) / samples
                self._conn.execute(
                    "UPDATE transitions SET count = count + 1, mean_latency_s = ?, "
                    "latency_samples = ?, last_seen = ? WHERE id = ?",
                    (mean, samples, now, transition_id),
                )
        return self.get_transition(transition_id)

    def get_transition(self, transition_id: int) -> TransitionRecord:
        return TransitionRecord(**self._require("transitions", transition_id))

    def list_transitions(self, source_state_id: int | None = None) -> list[TransitionRecord]:
        if source_state_id is None:
            rows = self._all("SELECT * FROM transitions")
        else:
            rows = self._all(
                "SELECT * FROM transitions WHERE source_state_id = ?", (source_state_id,)
            )
        return [TransitionRecord(**dict(row)) for row in rows]

    # Capturas (las usa ScreenshotStore)

    def add_screenshot(
        self, file_name: str, phash: str, width: int, height: int, size_bytes: int
    ) -> ScreenshotRecord:
        screenshot_id = self._insert(
            "INSERT INTO screenshots (file_name, phash, width, height, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (file_name, phash, width, height, size_bytes, self._now()),
        )
        return ScreenshotRecord(**self._require("screenshots", screenshot_id))

    def list_screenshots(self) -> list[ScreenshotRecord]:
        rows = self._all("SELECT * FROM screenshots ORDER BY created_at, id")
        return [ScreenshotRecord(**dict(row)) for row in rows]

    def remove_screenshot(self, file_name: str) -> None:
        """Borra el registro de una captura y la desliga de los estados que la usaban."""
        with self._transaction():
            self._conn.execute(
                "UPDATE states SET screenshot_path = NULL WHERE screenshot_path = ?", (file_name,)
            )
            self._conn.execute("DELETE FROM screenshots WHERE file_name = ?", (file_name,))

    # Estadísticas

    def counts(self) -> dict[str, int]:
        """Número de filas de cada tabla."""
        tables = ("apps", "states", "elements", "transitions", "sessions", "screenshots")
        return {
            table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    def now(self) -> datetime:
        """Hora actual según el reloj de la base."""
        return self._clock()

    # Internos

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat()

    def _transaction(self) -> "_Transaction":
        return _Transaction(self._conn, self._lock)

    def _insert(self, sql: str, params: tuple[Any, ...]) -> int:
        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
            except sqlite3.IntegrityError as error:
                raise StoreError(f"Dato rechazado por la base: {error}") from error
            return int(cursor.lastrowid or 0)

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _require(self, table: str, row_id: int) -> dict[str, Any]:
        row = self._one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        if row is None:
            raise StoreError(f"No existe el registro {row_id} en {table}.")
        return dict(row)


class _Transaction:
    """Transacción reentrante: solo la más externa hace BEGIN y COMMIT."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock
        self._outermost = False

    def __enter__(self) -> None:
        self._lock.acquire()
        self._outermost = not self._conn.in_transaction
        if self._outermost:
            self._conn.execute("BEGIN IMMEDIATE")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._outermost:
                self._conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self._lock.release()


def _check(value: str, allowed: Any, what: str) -> None:
    options = get_args(allowed)
    if value not in options:
        raise StoreError(f"Valor inválido para {what}: '{value}'. Opciones: {', '.join(options)}.")


def _element(row: sqlite3.Row) -> ElementRecord:
    rect = None
    if row["rect_left"] is not None:
        rect = Rect(
            left=row["rect_left"],
            top=row["rect_top"],
            right=row["rect_right"],
            bottom=row["rect_bottom"],
        )
    return ElementRecord(
        id=row["id"],
        state_id=row["state_id"],
        path=row["path"],
        control_type=row["control_type"],
        name=row["name"],
        automation_id=row["automation_id"],
        rect=rect,
        patterns=json.loads(row["patterns"]),
        enabled=bool(row["enabled"]),
    )
