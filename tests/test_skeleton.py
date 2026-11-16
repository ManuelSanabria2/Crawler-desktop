"""Pruebas mínimas del esqueleto: paquetes importables, CLI con ayuda y YAML legibles."""

import importlib
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"

SUBPACKAGES = [
    "geass.core",
    "geass.arm.kinematics",
    "geass.arm.sim",
    "geass.arm.neural_ik",
    "geass.arm.calibration",
    "geass.arm.safety",
    "geass.arm.planning",
    "geass.arm.perception",
    "geass.arm.chess",
    "geass.arm.bridge",
    "geass.crawler.uia",
    "geass.crawler.signature",
    "geass.crawler.store",
    "geass.crawler.passive",
    "geass.crawler.graph",
    "geass.crawler.export",
    "geass.crawler.actions",
    "geass.crawler.active",
    "geass.crawler.vision",
]

CONFIG_FILES = ["arm.yaml", "arm.test.yaml", "board.yaml", "safety.yaml", "crawler.yaml"]


def _contains_todo(node: object) -> bool:
    if isinstance(node, dict):
        return any(_contains_todo(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_todo(value) for value in node)
    return node == "TODO"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(name)


@pytest.mark.parametrize("module", ["geass.arm.__main__", "geass.crawler.__main__"])
def test_cli_shows_help(module: str) -> None:
    app = importlib.import_module(module).app
    for args in ([], ["--help"]):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0
        assert "Usage" in result.output


@pytest.mark.parametrize("filename", CONFIG_FILES)
def test_config_parses(filename: str) -> None:
    data = yaml.safe_load((CONFIG_DIR / filename).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["schema_version"] == 1


def test_arm_yaml_keeps_pending_data_as_todo() -> None:
    data = yaml.safe_load((CONFIG_DIR / "arm.yaml").read_text(encoding="utf-8"))
    assert _contains_todo(data)


def test_arm_test_yaml_is_fictitious_and_complete() -> None:
    data = yaml.safe_load((CONFIG_DIR / "arm.test.yaml").read_text(encoding="utf-8"))
    assert data["fictitious"] is True
    assert not _contains_todo(data)


def test_arm_test_yaml_matches_arm_yaml_structure() -> None:
    real = yaml.safe_load((CONFIG_DIR / "arm.yaml").read_text(encoding="utf-8"))
    fake = yaml.safe_load((CONFIG_DIR / "arm.test.yaml").read_text(encoding="utf-8"))
    assert set(real) | {"fictitious"} == set(fake)
    for section, content in real.items():
        if isinstance(content, dict):
            assert set(content) == set(fake[section]), section
