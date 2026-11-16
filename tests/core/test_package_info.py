"""Tests de la versión instalada del paquete."""

import tomllib

import pytest

import geass
import geass.core
from geass.core import package_info, paths


def test_version_is_non_empty_string() -> None:
    result = package_info.version()
    assert isinstance(result, str)
    assert result


def test_version_matches_pyproject(monkeypatch: pytest.MonkeyPatch) -> None:
    # Se ignora GEASS_ROOT para leer el pyproject.toml de este repositorio.
    monkeypatch.delenv(paths.ROOT_ENV, raising=False)
    pyproject = paths.project_root() / "pyproject.toml"
    with pyproject.open("rb") as handle:
        data = tomllib.load(handle)
    assert package_info.version() == data["project"]["version"]


def test_version_matches_dunder_version() -> None:
    assert package_info.version() == geass.__version__


def test_version_is_exported_from_core() -> None:
    from geass.core import version

    assert version is package_info.version
    assert "version" in geass.core.__all__
