"""Escritura de las notas de cada app en la bóveda de Obsidian.

La bóveda es de Manuel. Este módulo nunca la crea, nunca escribe fuera de la carpeta de
notas configurada, nunca borra ni renombra una nota y no toca una nota dudosa (marcadores
mal formados o texto que no es UTF-8): la deja igual y la reporta como error.
"""

import contextlib
import os
import re
import tempfile
from pathlib import Path, PurePath
from typing import Literal

from pydantic import BaseModel, ConfigDict

from geass.core.errors import GeassError
from geass.core.log import get_logger
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.export.blocks import ExportError, replace_managed_block
from geass.crawler.export.render import clean_inline, render_app_block
from geass.crawler.graph import CrawlerGraph
from geass.crawler.store.records import AppRecord

logger = get_logger(__name__)

ExportStatus = Literal["created", "updated", "unchanged", "skipped_excluded", "error"]

_UNSAFE_NAME = re.compile(r"[^a-z0-9._-]")
_TEMP_PREFIX = ".geass-"
_TEMP_SUFFIX = ".tmp"


class NoteResult(BaseModel):
    """Resultado de exportar la nota de una app."""

    model_config = ConfigDict(frozen=True)

    app: AppRecord
    path: Path | None  # None si la app se omitió por estar excluida
    status: ExportStatus
    written: bool  # verdadero solo si se escribió el archivo
    detail: str = ""  # motivo, cuando status es "error"


def note_path(vault: Path, notes_folder: str, app: AppRecord) -> Path:
    """Ruta de la nota de una app dentro de la bóveda."""
    folder = _notes_dir(vault, notes_folder)
    executable = app.executable
    if executable.lower().endswith(".exe"):
        executable = executable[: -len(".exe")]
    name = _UNSAFE_NAME.sub("_", executable) or f"app_{app.id}"
    return folder / f"{name}.md"


def export_notes(
    graph: CrawlerGraph,
    config: CrawlerConfig,
    *,
    app: str | None = None,
    vault: Path | None = None,
    dry_run: bool = False,
) -> list[NoteResult]:
    """Escribe o actualiza la nota de cada app. Con dry_run no escribe nada."""
    vault_dir = _vault_dir(config, vault)
    settings = config.export
    _notes_dir(vault_dir, settings.notes_folder)

    records = graph.apps() if app is None else [graph.resolve_app(app)]
    excluded = {name.strip().lower() for name in config.apps.excluded}
    results: list[NoteResult] = []
    for record in records:
        if record.executable.strip().lower() in excluded:
            results.append(
                NoteResult(app=record, path=None, status="skipped_excluded", written=False)
            )
            continue
        path = note_path(vault_dir, settings.notes_folder, record)
        result = _export_one(graph, config, record, path, dry_run)
        if result.status == "error":
            logger.warning("No se exportó la nota de %s: %s", record.executable, result.detail)
        results.append(result)
    return results


def _export_one(
    graph: CrawlerGraph, config: CrawlerConfig, record: AppRecord, path: Path, dry_run: bool
) -> NoteResult:
    settings = config.export

    def error(detail: str) -> NoteResult:
        return NoteResult(app=record, path=path, status="error", written=False, detail=detail)

    try:
        body = render_app_block(
            graph,
            record,
            top_actions=settings.top_actions_per_app,
            top_routes=settings.top_routes_per_app,
        )
    except (GeassError, ValueError) as problem:
        return error(f"no se pudo generar el contenido: {problem}")

    markers = {"start": settings.managed_block_start, "end": settings.managed_block_end}
    if path.is_symlink() or path.exists():
        if path.is_symlink() or not path.is_file():
            return error("la ruta de la nota existe y no es un archivo normal")
        try:
            raw = path.read_bytes()
        except OSError as problem:
            return error(f"no se pudo leer la nota: {problem}")
        try:
            # Sin traducir saltos de línea: se decodifican los bytes tal cual.
            old_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return error("la nota no es texto UTF-8 válido; no se toca")
        try:
            new_text = replace_managed_block(old_text, body, **markers)
        except (ExportError, ValueError) as problem:
            return error(str(problem))
        if new_text == old_text:
            return NoteResult(app=record, path=path, status="unchanged", written=False)
        status: ExportStatus = "updated"
    else:
        title = f"# {clean_inline(record.display_name)}\n"
        try:
            new_text = replace_managed_block(title, body, **markers)
        except (ExportError, ValueError) as problem:
            return error(str(problem))
        status = "created"

    if dry_run:
        return NoteResult(app=record, path=path, status=status, written=False)
    try:
        _write_atomic(path, new_text)
    except (OSError, ValueError) as problem:
        return error(f"no se pudo escribir la nota: {problem}")
    return NoteResult(app=record, path=path, status=status, written=True)


def _vault_dir(config: CrawlerConfig, vault: Path | None) -> Path:
    """Carpeta de la bóveda. Si sigue como TODO, deja pasar ``ConfigIncompleteError``."""
    if vault is None:
        raw = config.export.obsidian_vault_path
        vault = Path(os.path.expandvars(raw)).expanduser()
    if not vault.is_dir():
        raise ExportError(f"La bóveda de Obsidian no existe o no es una carpeta: {vault}")
    return vault


def _notes_dir(vault: Path, notes_folder: str) -> Path:
    """Carpeta de notas dentro de la bóveda. No la crea."""
    relative = PurePath(notes_folder)
    if relative.is_absolute() or relative.anchor:
        raise ExportError(
            f"La carpeta de notas debe ser relativa a la bóveda, no absoluta: {notes_folder}"
        )
    root = vault.resolve()
    folder = (vault / relative).resolve()
    if not folder.is_relative_to(root):
        raise ExportError(f"La carpeta de notas queda fuera de la bóveda: {notes_folder}")
    return vault / relative


def _write_atomic(path: Path, text: str) -> None:
    """Escribe en un temporal de la misma carpeta y lo pone en su sitio con ``os.replace``."""
    data = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX, dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as temp_file:
            temp_file.write(data)
        os.replace(temp_name, path)
    except BaseException:
        # Solo se borra el temporal propio de esta escritura.
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
