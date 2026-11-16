"""Esquema de la base SQLite del crawler, como lista de migraciones.

Para cambiar el esquema se añade una migración nueva al final. Nunca se edita una que ya
se aplicó: las bases existentes no la volverían a ejecutar.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""

MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="esquema inicial (sección 6.4) y tabla de capturas",
        statements=(
            """
            CREATE TABLE apps (
                id INTEGER PRIMARY KEY,
                executable TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                app_user_model_id TEXT
            )
            """,
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                mode TEXT NOT NULL CHECK (mode IN ('passive', 'active', 'manual')),
                notes TEXT NOT NULL DEFAULT ''
            )
            """,
            """
            CREATE TABLE screenshots (
                id INTEGER PRIMARY KEY,
                file_name TEXT NOT NULL UNIQUE,
                phash TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE states (
                id INTEGER PRIMARY KEY,
                app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
                structural_signature TEXT NOT NULL,
                detailed_signature TEXT NOT NULL,
                title_pattern TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                visits INTEGER NOT NULL DEFAULT 1,
                screenshot_path TEXT,
                UNIQUE (app_id, structural_signature)
            )
            """,
            """
            CREATE TABLE elements (
                id INTEGER PRIMARY KEY,
                state_id INTEGER NOT NULL REFERENCES states(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                control_type TEXT NOT NULL,
                name TEXT NOT NULL,
                automation_id TEXT NOT NULL,
                rect_left INTEGER,
                rect_top INTEGER,
                rect_right INTEGER,
                rect_bottom INTEGER,
                patterns TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                UNIQUE (state_id, path)
            )
            """,
            """
            CREATE TABLE transitions (
                id INTEGER PRIMARY KEY,
                source_state_id INTEGER NOT NULL REFERENCES states(id) ON DELETE CASCADE,
                target_state_id INTEGER NOT NULL REFERENCES states(id) ON DELETE CASCADE,
                element_id INTEGER REFERENCES elements(id) ON DELETE SET NULL,
                action_type TEXT NOT NULL CHECK (action_type IN (
                    'invoke', 'toggle', 'expand_collapse', 'select', 'set_value',
                    'coordinate_click', 'unknown'
                )),
                source TEXT NOT NULL CHECK (source IN ('passive', 'active')),
                count INTEGER NOT NULL DEFAULT 1,
                mean_latency_s REAL,
                latency_samples INTEGER NOT NULL DEFAULT 0,
                last_seen TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_states_app ON states(app_id)",
            "CREATE INDEX idx_elements_state ON elements(state_id)",
            "CREATE INDEX idx_transitions_source ON transitions(source_state_id)",
            "CREATE INDEX idx_transitions_target ON transitions(target_state_id)",
        ),
    ),
)
