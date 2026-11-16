"""Rutas del proyecto: raíz, config, data, models y logs."""

import os
from pathlib import Path

from geass.core.errors import GeassError

ROOT_ENV = "GEASS_ROOT"
ROOT_MARKER = "pyproject.toml"


def project_root() -> Path:
    """Devuelve la raíz del repositorio.

    Usa la variable de entorno ``GEASS_ROOT`` si está definida. Si no, sube desde este
    archivo hasta encontrar ``pyproject.toml``.
    """
    override = os.environ.get(ROOT_ENV)
    if override:
        root = Path(override).expanduser().resolve()
        if not root.is_dir():
            raise GeassError(f"{ROOT_ENV} apunta a una carpeta que no existe: {root}")
        return root
    for parent in Path(__file__).resolve().parents:
        if (parent / ROOT_MARKER).is_file():
            return parent
    raise GeassError(
        f"No se encontró la raíz del proyecto (ningún {ROOT_MARKER} sobre {__file__}). "
        f"Define la variable de entorno {ROOT_ENV}."
    )


def config_dir() -> Path:
    """Carpeta ``config/`` con los YAML."""
    return project_root() / "config"


def data_dir() -> Path:
    """Carpeta ``data/``. Está fuera de git."""
    return project_root() / "data"


def models_dir() -> Path:
    """Carpeta ``models/``. Está fuera de git."""
    return project_root() / "models"


def logs_dir() -> Path:
    """Carpeta ``data/logs/`` de los archivos de log."""
    return data_dir() / "logs"


def resolve_project_path(path: str | Path) -> Path:
    """Convierte una ruta de la configuración en absoluta.

    Las rutas relativas de los YAML (por ejemplo ``data/arm/ik``) se toman respecto a la raíz.
    """
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return project_root() / candidate


def ensure_dir(path: Path) -> Path:
    """Crea la carpeta si no existe y la devuelve."""
    path.mkdir(parents=True, exist_ok=True)
    return path
