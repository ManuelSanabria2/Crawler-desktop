"""Índice de las apps que se pueden abrir: accesos directos del menú Inicio y Get-StartApps.

El índice solo guarda nombres, rutas de accesos directos y AppID. No resuelve a qué apunta
cada ``.lnk`` ni ejecuta nada. Los nombres son datos: solo se comparan con lo que pide Manuel.
"""

import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from geass.core.log import get_logger
from geass.core.paths import ensure_dir, resolve_project_path
from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions import system
from geass.crawler.actions.errors import ActionError
from geass.crawler.graph.text import normalize_text

logger = get_logger(__name__)

AppSource = Literal["shortcut", "start_apps"]

_SHORTCUT_SUFFIX = ".lnk"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AppEntry(BaseModel):
    """Una app del índice."""

    model_config = ConfigDict(frozen=True)

    name: str  # nombre visible
    source: AppSource
    shortcut_path: str | None  # ruta del .lnk, si source es "shortcut"
    app_id: str | None  # AppID, si source es "start_apps"
    blocked: bool  # el nombre coincide con la lista de bloqueo de safety.yaml

    @model_validator(mode="after")
    def _check_source(self) -> "AppEntry":
        if self.source == "shortcut" and (not self.shortcut_path or self.app_id is not None):
            raise ValueError("una entrada de acceso directo necesita shortcut_path y no app_id")
        if self.source == "start_apps" and (not self.app_id or self.shortcut_path is not None):
            raise ValueError("una entrada de Get-StartApps necesita app_id y no shortcut_path")
        return self


class _CacheFile(BaseModel):
    """Forma del archivo de caché."""

    built_at: datetime
    entries: list[AppEntry]


class AppIndex:
    """Apps conocidas, ordenadas por nombre normalizado."""

    def __init__(self, entries: Sequence[AppEntry], *, built_at: datetime) -> None:
        self._entries = sorted(entries, key=lambda entry: (normalize_text(entry.name), entry.name))
        self._built_at = built_at

    @property
    def entries(self) -> list[AppEntry]:
        """Entradas ordenadas por nombre normalizado. Es una copia."""
        return list(self._entries)

    @property
    def built_at(self) -> datetime:
        """Momento en que se construyó el índice."""
        return self._built_at

    @classmethod
    def build(
        cls,
        config: CrawlerConfig,
        safety: SafetyConfig,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> "AppIndex":
        """Construye el índice leyendo el menú Inicio y, si está activado, Get-StartApps.

        Si Get-StartApps falla o su salida no es JSON válido, se registra un aviso y el
        índice sigue solo con los accesos directos.
        """
        launcher = config.launcher
        patterns = _blocklist(safety)
        candidates: list[tuple[str, AppSource, str | None, str | None]] = []
        for folder in launcher.start_menu_dirs:
            for path in _shortcuts_in(Path(os.path.expandvars(folder))):
                candidates.append((path.stem, "shortcut", str(path), None))
        if launcher.include_store_apps:
            for name, app_id in _start_apps(launcher.start_apps_timeout_s):
                candidates.append((name, "start_apps", None, app_id))

        seen: set[str] = set()
        entries: list[AppEntry] = []
        for raw_name, source, shortcut_path, app_id in candidates:
            name = raw_name.strip()
            key = normalize_text(name)
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append(
                AppEntry(
                    name=name,
                    source=source,
                    shortcut_path=shortcut_path,
                    app_id=app_id,
                    blocked=_is_blocked(name, patterns),
                )
            )
        return cls(entries, built_at=clock().astimezone(UTC))

    @classmethod
    def load(
        cls,
        config: CrawlerConfig,
        safety: SafetyConfig,
        *,
        refresh: bool = False,
        cache_path: Path | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> "AppIndex":
        """Índice de la caché si es reciente; si no, lo construye y lo guarda.

        Una caché que no existe, antigua o corrupta nunca es un error: se reconstruye.
        """
        path = cache_path if cache_path is not None else _default_cache(config)
        if not refresh:
            cached = _read_cache(path, safety)
            if cached is not None:
                age_s = (clock() - cached.built_at).total_seconds()
                if 0 <= age_s < config.launcher.cache_max_age_s:
                    return cached
        index = cls.build(config, safety, clock=clock)
        index.save(path)
        return index

    def save(self, path: Path) -> None:
        """Guarda el índice en JSON de forma atómica, creando la carpeta si falta."""
        data = {
            "built_at": self._built_at.astimezone(UTC).isoformat(),
            "entries": [entry.model_dump(mode="json") for entry in self._entries],
        }
        _write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def _default_cache(config: CrawlerConfig) -> Path:
    return resolve_project_path(config.launcher.cache_path)


def _blocklist(safety: SafetyConfig) -> list[str]:
    normalized = (normalize_text(pattern) for pattern in safety.crawler.blocklist_patterns)
    return [pattern for pattern in normalized if pattern]


def _is_blocked(name: str, patterns: Sequence[str]) -> bool:
    normalized = normalize_text(name)
    return any(pattern in normalized for pattern in patterns)


def _shortcuts_in(folder: Path) -> list[Path]:
    """Accesos directos de una carpeta y sus subcarpetas, ordenados por ruta."""
    if not folder.is_dir():
        logger.debug("La carpeta del menú Inicio no existe y se salta: %s", folder)
        return []
    found = [
        Path(root) / file_name
        for root, _, file_names in os.walk(folder)
        for file_name in file_names
        if Path(file_name).suffix.lower() == _SHORTCUT_SUFFIX
    ]
    return sorted(found)


def _start_apps(timeout_s: float) -> Iterator[tuple[str, str]]:
    """Pares (nombre, AppID) de Get-StartApps. Si falla, avisa y no da ninguno."""
    try:
        text = system.read_start_apps(timeout_s)
        data: Any = json.loads(text.lstrip("﻿"))
    except ActionError as error:
        logger.warning("No se pudo leer Get-StartApps; el índice sigue sin esas apps: %s", error)
        return
    except ValueError as error:
        logger.warning("La salida de Get-StartApps no es JSON válido; se ignora: %s", error)
        return
    items = [data] if isinstance(data, dict) else data
    if not isinstance(items, list):
        logger.warning("La salida de Get-StartApps no es una lista de apps; se ignora.")
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        name, app_id = item.get("Name"), item.get("AppID")
        if isinstance(name, str) and isinstance(app_id, str) and app_id:
            yield name, app_id


def _read_cache(path: Path, safety: SafetyConfig) -> AppIndex | None:
    """Índice guardado en ``path``, o ``None`` si no existe o no se puede leer."""
    try:
        cache = _CacheFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as error:
        if path.exists():
            logger.info("La caché del índice de apps no se pudo leer y se reconstruye: %s", error)
        return None
    if cache.built_at.tzinfo is None:
        return None
    patterns = _blocklist(safety)
    entries = [
        entry.model_copy(update={"blocked": _is_blocked(entry.name, patterns)})
        for entry in cache.entries
    ]
    return AppIndex(entries, built_at=cache.built_at)


def _write_atomic(path: Path, text: str) -> None:
    """Escribe en un temporal de la misma carpeta y lo pone en su sitio con ``os.replace``."""
    ensure_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
