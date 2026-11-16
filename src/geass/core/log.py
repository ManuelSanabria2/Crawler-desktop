"""Logging estándar de GEASS: consola con rich y archivo rotativo en data/logs/."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

from geass.core.paths import ensure_dir, logs_dir

ROOT_LOGGER_NAME = "geass"
LOG_LEVEL_ENV = "GEASS_LOG_LEVEL"
DEFAULT_LEVEL = "INFO"
DEFAULT_FILE_NAME = "geass.log"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(
    level: str | int | None = None,
    *,
    log_dir: Path | None = None,
    console: bool = True,
    to_file: bool = True,
    file_name: str = DEFAULT_FILE_NAME,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> logging.Logger:
    """Configura el logger ``geass`` y lo devuelve.

    Se puede llamar varias veces: cada llamada reemplaza los manejadores anteriores.

    Args:
        level: nivel de log. Si es ``None`` se usa ``GEASS_LOG_LEVEL`` o ``INFO``.
        log_dir: carpeta del archivo de log. Por defecto ``data/logs/``.
        console: si se escribe en la consola con rich.
        to_file: si se escribe en el archivo rotativo.
        file_name: nombre del archivo de log.
        max_bytes: tamaño a partir del cual rota el archivo.
        backup_count: cuántos archivos rotados se conservan.
    """
    resolved_level = level if level is not None else os.environ.get(LOG_LEVEL_ENV, DEFAULT_LEVEL)
    if isinstance(resolved_level, str):
        resolved_level = resolved_level.upper()

    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(resolved_level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    if console:
        logger.addHandler(RichHandler(rich_tracebacks=True, show_path=False))
    if to_file:
        directory = ensure_dir(log_dir if log_dir is not None else logs_dir())
        file_handler = RotatingFileHandler(
            directory / file_name,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
        logger.addHandler(file_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger hijo de ``geass``. Uso habitual: ``get_logger(__name__)``."""
    if name == ROOT_LOGGER_NAME or name.startswith(f"{ROOT_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{name}")
