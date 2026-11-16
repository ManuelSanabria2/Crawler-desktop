"""Registro de auditoría de las acciones directas y del ejecutor.

Es la regla de CLAUDE.md: todo lo que se hace queda registrado con qué acción, sobre qué
elemento, en qué app, el resultado y quién la autorizó. Una línea JSON por acción, hecha o
rechazada. El ejecutor añade detalles en la clave opcional ``details``.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from geass.core.paths import ensure_dir, resolve_project_path
from geass.core.schemas.safety import SafetyConfig


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditLog:
    """Registro de auditoría en JSON Lines. Cada acción hecha o rechazada es una línea."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = _utc_now) -> None:
        """
        Args:
            path: archivo JSON Lines. La carpeta se crea al escribir la primera línea.
            clock: reloj que da la hora de cada línea; se convierte a UTC.
        """
        self._path = path
        self._clock = clock

    @classmethod
    def from_config(cls, safety: SafetyConfig | None = None) -> "AuditLog":
        """Registro en ``crawler.audit_log.path`` de safety.yaml."""
        if safety is None:
            from geass.core.config import load_safety_config

            safety = load_safety_config()
        return cls(resolve_project_path(safety.crawler.audit_log.path))

    def record(
        self,
        *,
        action: str,
        target: str,
        app: str,
        result: str,
        authorized_by: str,
        details: Mapping[str, str | int | float | bool | None] | None = None,
    ) -> None:
        """Añade una línea al registro.

        Sin ``details`` la línea tiene seis claves. Con ``details`` se añade la clave
        ``details`` con una copia de lo que se pasó.

        Raises:
            ValueError: ``authorized_by`` está vacío. No se escribe nada.
        """
        if not authorized_by.strip():
            raise ValueError("Toda acción necesita quién la autorizó: authorized_by está vacío.")
        line: dict[str, object] = {
            "at": self._clock().astimezone(UTC).isoformat(),
            "action": action,
            "target": target,
            "app": app,
            "result": result,
            "authorized_by": authorized_by,
        }
        if details is not None:
            line["details"] = dict(details)
        ensure_dir(self._path.parent)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
