"""Cargador de la configuración de GEASS.

Lee los YAML de ``config/``, los valida con los esquemas Pydantic y devuelve modelos
inmutables. Un valor ``TODO`` no rompe la carga: el error llega al leer ese atributo.

El perfil del brazo decide qué archivo se lee:

- ``real``: ``arm.yaml``, el brazo de Manuel. Es el perfil por defecto.
- ``test``: ``arm.test.yaml``, valores ficticios solo para tests.

Se elige con el argumento ``profile`` o con la variable de entorno ``GEASS_ARM_PROFILE``.
El argumento tiene prioridad.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeVar

import yaml
from pydantic import ValidationError

from geass.core import paths
from geass.core.errors import ConfigError, ConfigValidationError
from geass.core.schema import ConfigModel, attach_paths
from geass.core.schemas.arm import ArmConfig
from geass.core.schemas.board import BoardConfig
from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig

ARM_PROFILE_ENV: Final = "GEASS_ARM_PROFILE"
DEFAULT_ARM_PROFILE: Final = "real"
ARM_PROFILE_FILES: Final[dict[str, str]] = {"real": "arm.yaml", "test": "arm.test.yaml"}
BOARD_FILE: Final = "board.yaml"
SAFETY_FILE: Final = "safety.yaml"
CRAWLER_FILE: Final = "crawler.yaml"

ModelT = TypeVar("ModelT", bound=ConfigModel)


@dataclass(frozen=True)
class GeassConfig:
    """Toda la configuración cargada, más el perfil del brazo que se usó."""

    arm_profile: str
    arm: ArmConfig
    board: BoardConfig
    safety: SafetyConfig
    crawler: CrawlerConfig

    def pending_keys(self) -> dict[str, list[str]]:
        """Claves que siguen como TODO, agrupadas por archivo."""
        return {
            ARM_PROFILE_FILES[self.arm_profile]: self.arm.pending_keys(),
            BOARD_FILE: self.board.pending_keys(),
            SAFETY_FILE: self.safety.pending_keys(),
            CRAWLER_FILE: self.crawler.pending_keys(),
        }


def resolve_arm_profile(profile: str | None = None) -> str:
    """Devuelve el perfil del brazo: el argumento, la variable de entorno o ``real``."""
    chosen = profile or os.environ.get(ARM_PROFILE_ENV) or DEFAULT_ARM_PROFILE
    chosen = chosen.strip().lower()
    if chosen not in ARM_PROFILE_FILES:
        valid = ", ".join(sorted(ARM_PROFILE_FILES))
        raise ConfigError(f"Perfil del brazo desconocido: '{chosen}'. Perfiles válidos: {valid}.")
    return chosen


def load_yaml_model(path: Path, model: type[ModelT]) -> ModelT:
    """Lee un YAML y lo valida contra ``model``."""
    if not path.is_file():
        raise ConfigError(f"No existe el archivo de configuración: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigValidationError(f"{path.name} no es YAML válido: {error}") from error
    if not isinstance(data, dict):
        raise ConfigValidationError(f"{path.name} debe contener un mapa de claves en la raíz.")
    try:
        loaded = model.model_validate(data)
    except ValidationError as error:
        raise ConfigValidationError(_format_validation_error(path.name, error)) from error
    attach_paths(loaded, source=path.name)
    return loaded


def load_arm_config(profile: str | None = None, *, config_dir: Path | None = None) -> ArmConfig:
    """Carga la configuración del brazo según el perfil."""
    chosen = resolve_arm_profile(profile)
    file_name = ARM_PROFILE_FILES[chosen]
    config = load_yaml_model(_dir(config_dir) / file_name, ArmConfig)
    if chosen == "real" and config.fictitious:
        raise ConfigValidationError(
            f"{file_name} está marcado como ficticio y no puede usarse como perfil real."
        )
    if chosen == "test" and not config.fictitious:
        raise ConfigValidationError(
            f"{file_name} debe llevar 'fictitious: true' para usarse como perfil de test."
        )
    return config


def load_board_config(*, config_dir: Path | None = None) -> BoardConfig:
    """Carga ``board.yaml``."""
    return load_yaml_model(_dir(config_dir) / BOARD_FILE, BoardConfig)


def load_safety_config(*, config_dir: Path | None = None) -> SafetyConfig:
    """Carga ``safety.yaml``."""
    return load_yaml_model(_dir(config_dir) / SAFETY_FILE, SafetyConfig)


def load_crawler_config(*, config_dir: Path | None = None) -> CrawlerConfig:
    """Carga ``crawler.yaml``."""
    return load_yaml_model(_dir(config_dir) / CRAWLER_FILE, CrawlerConfig)


def load_config(profile: str | None = None, *, config_dir: Path | None = None) -> GeassConfig:
    """Carga los cuatro YAML de una vez."""
    chosen = resolve_arm_profile(profile)
    return GeassConfig(
        arm_profile=chosen,
        arm=load_arm_config(chosen, config_dir=config_dir),
        board=load_board_config(config_dir=config_dir),
        safety=load_safety_config(config_dir=config_dir),
        crawler=load_crawler_config(config_dir=config_dir),
    )


def _dir(config_dir: Path | None) -> Path:
    return config_dir if config_dir is not None else paths.config_dir()


def _format_validation_error(file_name: str, error: ValidationError) -> str:
    lines = [f"{file_name} no cumple el esquema ({error.error_count()} errores):"]
    for item in error.errors():
        location = ""
        for part in item["loc"]:
            location += f"[{part}]" if isinstance(part, int) else f".{part}"
        lines.append(f"  {location.lstrip('.') or '(raíz)'}: {item['msg']}")
    return "\n".join(lines)
