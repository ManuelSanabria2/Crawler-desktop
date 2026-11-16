"""Tests de las rutas del proyecto y del logging."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
from rich.logging import RichHandler

from geass.core import paths
from geass.core.errors import GeassError
from geass.core.log import LOG_LEVEL_ENV, ROOT_LOGGER_NAME, get_logger, setup_logging


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.ROOT_ENV, raising=False)
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)


@pytest.fixture
def clean_logger() -> logging.Logger:
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


# Rutas


def test_project_root_is_the_repo() -> None:
    root = paths.project_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "geass").is_dir()


def test_standard_directories() -> None:
    root = paths.project_root()
    assert paths.config_dir() == root / "config"
    assert paths.data_dir() == root / "data"
    assert paths.models_dir() == root / "models"
    assert paths.logs_dir() == root / "data" / "logs"
    assert (paths.config_dir() / "arm.yaml").is_file()


def test_root_override_by_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.ROOT_ENV, str(tmp_path))
    assert paths.project_root() == tmp_path.resolve()
    assert paths.data_dir() == tmp_path.resolve() / "data"


def test_root_override_must_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.ROOT_ENV, str(tmp_path / "no_existe"))
    with pytest.raises(GeassError, match="no existe"):
        paths.project_root()


def test_resolve_project_path(tmp_path: Path) -> None:
    assert paths.resolve_project_path("data/arm/ik") == paths.project_root() / "data/arm/ik"
    assert paths.resolve_project_path(tmp_path) == tmp_path


def test_ensure_dir_creates_nested(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b"
    assert paths.ensure_dir(target) == target
    assert target.is_dir()
    assert paths.ensure_dir(target) == target


# Logging


def test_setup_logging_writes_console_and_file(
    tmp_path: Path, clean_logger: logging.Logger
) -> None:
    logger = setup_logging("DEBUG", log_dir=tmp_path)
    kinds = {type(handler) for handler in logger.handlers}
    assert kinds == {RichHandler, RotatingFileHandler}

    get_logger("geass.arm.prueba").debug("mensaje de prueba con tilde: configuración")
    for handler in logger.handlers:
        handler.flush()
    content = (tmp_path / "geass.log").read_text(encoding="utf-8")
    assert "mensaje de prueba con tilde: configuración" in content
    assert "geass.arm.prueba" in content
    assert "DEBUG" in content


def test_setup_logging_is_idempotent(tmp_path: Path, clean_logger: logging.Logger) -> None:
    setup_logging(log_dir=tmp_path)
    logger = setup_logging(log_dir=tmp_path)
    assert len(logger.handlers) == 2


def test_level_from_environment(
    tmp_path: Path, clean_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LOG_LEVEL_ENV, "warning")
    logger = setup_logging(log_dir=tmp_path)
    assert logger.level == logging.WARNING
    assert setup_logging("ERROR", log_dir=tmp_path).level == logging.ERROR


def test_default_log_dir_is_data_logs(
    tmp_path: Path, clean_logger: logging.Logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.ROOT_ENV, str(tmp_path))
    setup_logging(console=False)
    get_logger("core").info("hola")
    assert (tmp_path / "data" / "logs" / "geass.log").is_file()


def test_file_rotates(tmp_path: Path, clean_logger: logging.Logger) -> None:
    logger = setup_logging(log_dir=tmp_path, console=False, max_bytes=300, backup_count=2)
    for index in range(50):
        logger.info("linea de relleno numero %d", index)
    names = sorted(path.name for path in tmp_path.iterdir())
    assert names == ["geass.log", "geass.log.1", "geass.log.2"]


def test_get_logger_hangs_from_geass() -> None:
    assert get_logger("crawler.uia").name == "geass.crawler.uia"
    assert get_logger("geass.arm").name == "geass.arm"
    assert get_logger("geass").name == "geass"
