"""Acciones directas que no navegan ninguna interfaz: el explorador y las teclas multimedia.

Cada una tiene que estar activada en ``launcher.direct_actions`` de crawler.yaml y cada
llamada, hecha o rechazada, deja una línea en la auditoría.
"""

import os
from pathlib import Path
from typing import Literal, get_args

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.actions import system
from geass.crawler.actions.audit import AuditLog
from geass.crawler.actions.errors import ActionDisabledError, ActionError

MediaAction = Literal["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]

# Clave de launcher.direct_actions que activa cada acción.
DIRECT_ACTION_KEYS: dict[str, str] = {
    "explorer": "open_explorer",
    "play_pause": "media_play_pause",
    "next": "media_next",
    "previous": "media_previous",
    "volume_up": "volume_up",
    "volume_down": "volume_down",
    "mute": "volume_mute",
}

_EXPLORER_APP = "explorer.exe"
_MEDIA_ACTIONS: tuple[str, ...] = get_args(MediaAction)


def open_explorer(
    path: str | Path | None = None,
    *,
    authorized_by: str,
    config: CrawlerConfig | None = None,
    audit: AuditLog | None = None,
) -> Path | None:
    """Abre el explorador de archivos, en una carpeta que existe si se da.

    Devuelve la carpeta resuelta, o ``None`` si se abrió sin carpeta.

    Raises:
        ValueError: ``authorized_by`` está vacío.
        ActionDisabledError: ``open_explorer`` no está en ``launcher.direct_actions``.
        ActionError: la ruta no es una carpeta que existe, o Windows no pudo abrir el
            explorador.
    """
    _require_authorizer(authorized_by)
    config, audit = _defaults(config, audit)

    def record(target: str, result: str) -> None:
        audit.record(
            action="open_explorer",
            target=target,
            app=_EXPLORER_APP,
            result=result,
            authorized_by=authorized_by,
        )

    given = "" if path is None else str(path)
    try:
        _require_enabled("explorer", config)
    except ActionDisabledError:
        record(given, "disabled")
        raise

    folder: Path | None = None
    try:
        if path is not None:
            folder = _existing_folder(given)
        system.spawn_explorer(folder)
    except ActionError as error:
        record(str(folder) if folder is not None else given, f"error: {error}")
        raise
    record("" if folder is None else str(folder), "done")
    return folder


def media(
    action: MediaAction,
    *,
    authorized_by: str,
    config: CrawlerConfig | None = None,
    audit: AuditLog | None = None,
) -> None:
    """Pulsa una tecla multimedia de la lista cerrada ``MediaAction``.

    Raises:
        ValueError: ``authorized_by`` está vacío.
        ActionError: ``action`` no es una acción multimedia, o Windows no aceptó la tecla.
        ActionDisabledError: la acción no está en ``launcher.direct_actions``.
    """
    _require_authorizer(authorized_by)
    config, audit = _defaults(config, audit)

    def record(result: str) -> None:
        audit.record(
            action="media", target=str(action), app="", result=result, authorized_by=authorized_by
        )

    if action not in _MEDIA_ACTIONS:
        valid = ", ".join(_MEDIA_ACTIONS)
        error = ActionError(f"Acción multimedia desconocida: '{action}'. Válidas: {valid}.")
        record(f"error: {error}")
        raise error
    try:
        _require_enabled(action, config)
    except ActionDisabledError:
        record("disabled")
        raise
    try:
        system.send_media_key(action)
    except ActionError as error:
        record(f"error: {error}")
        raise
    record("done")


def _require_authorizer(authorized_by: str) -> None:
    if not authorized_by.strip():
        raise ValueError("Toda acción necesita quién la autorizó: authorized_by está vacío.")


def _defaults(
    config: CrawlerConfig | None, audit: AuditLog | None
) -> tuple[CrawlerConfig, AuditLog]:
    if config is None:
        from geass.core.config import load_crawler_config

        config = load_crawler_config()
    if audit is None:
        audit = AuditLog.from_config()
    return config, audit


def _require_enabled(action: str, config: CrawlerConfig) -> None:
    key = DIRECT_ACTION_KEYS[action]
    if key not in config.launcher.direct_actions:
        raise ActionDisabledError(
            f"La acción directa '{key}' no está activada en launcher.direct_actions "
            "de crawler.yaml."
        )


def _existing_folder(text: str) -> Path:
    """Carpeta absoluta a partir de ``text``, con ``~`` y variables de entorno expandidas."""
    if not text.strip():
        raise ActionError("La ruta del explorador está vacía.")
    try:
        folder = Path(os.path.expandvars(os.path.expanduser(text))).resolve()
        exists = folder.is_dir()
    except (OSError, ValueError) as error:
        raise ActionError(f"Ruta inválida para el explorador: {text} ({error})") from error
    if not exists:
        raise ActionError(f"No es una carpeta que exista: {folder}")
    return folder
