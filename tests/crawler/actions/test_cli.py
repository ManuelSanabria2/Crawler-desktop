"""Tests del grupo de comandos ``do`` (casos 34 a 41 de la especificación C07).

Los cargadores de configuración que usan los comandos se sustituyen con ``monkeypatch``: el
menú Inicio, la caché del índice y la auditoría quedan en ``tmp_path``, y las funciones de
``system`` son dobles. Ningún comando abre nada de verdad.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest
from typer.testing import CliRunner

from geass.core import config as config_module
from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler import __main__ as cli
from geass.crawler.__main__ import app
from geass.crawler.actions import MediaAction


@pytest.fixture(autouse=True)
def injected_configs(
    monkeypatch: pytest.MonkeyPatch, config: CrawlerConfig, safety_tmp_audit: SafetyConfig
) -> None:
    monkeypatch.setattr(config_module, "load_crawler_config", lambda **_: config)
    monkeypatch.setattr(config_module, "load_safety_config", lambda **_: safety_tmp_audit)


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args))
    return result.exit_code, result.output


def test_do_help_lists_the_commands() -> None:
    code, output = run("do", "--help")
    assert code == 0, output
    for command in ("launch", "media", "explorer", "apps"):
        assert command in output


def test_launch(
    fake_system: Any,
    menu_dirs: tuple[Path, Path],
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    code, output = run("do", "launch", "spotify")
    assert code == 0, output
    assert "Abriendo Spotify." in output
    assert fake_system.calls_to("start_shortcut") == [(menu_dirs[0] / "Spotify.lnk",)]
    assert read_audit(audit_path)[-1]["authorized_by"] == "cli:manuel"


def test_launch_dry_run(
    fake_system: Any, audit_path: Path, read_audit: Callable[[Path], list[dict[str, Any]]]
) -> None:
    code, output = run("do", "launch", "spotify", "--dry-run")
    assert code == 0, output
    assert "Se abriría Spotify." in output
    assert fake_system.launch_calls() == []
    assert read_audit(audit_path) == []


def test_launch_ambiguous(fake_system: Any) -> None:
    code, output = run("do", "launch", "visual studio")
    assert code == 2, output
    assert "es ambiguo" in output
    assert "Visual Studio 2022" in output
    assert "Visual Studio Code" in output
    assert "  1. " in output
    assert fake_system.launch_calls() == []


def test_launch_not_found_and_blocked(fake_system: Any) -> None:
    code, output = run("do", "launch", "zzzzqqq")
    assert code == 1, output
    assert "No hay ninguna app que coincida con 'zzzzqqq'." in output
    code, output = run("do", "launch", "desinstalar foo")
    assert code == 1, output
    assert "lista de bloqueo" in output
    assert fake_system.launch_calls() == []


def test_launch_refresh_rebuilds_the_index(fake_system: Any) -> None:
    run("do", "launch", "spotify", "--dry-run")
    run("do", "launch", "spotify", "--dry-run")
    assert len(fake_system.calls_to("read_start_apps")) == 1
    run("do", "launch", "spotify", "--dry-run", "--refresh")
    assert len(fake_system.calls_to("read_start_apps")) == 2


def test_launch_blank_name_exits_with_1(fake_system: Any) -> None:
    code, _ = run("do", "launch", "   ")
    assert code == 1
    assert fake_system.launch_calls() == []


def test_media(fake_system: Any) -> None:
    code, output = run("do", "media", "play_pause")
    assert code == 0, output
    assert "Hecho: play_pause." in output
    assert fake_system.calls_to("send_media_key") == [("play_pause",)]


def test_media_rejects_other_keys(fake_system: Any) -> None:
    code, _ = run("do", "media", "enter")
    assert code == 2
    assert fake_system.calls == []


def test_media_choices_match_the_actions() -> None:
    assert get_args(cli.MediaChoice) == get_args(MediaAction)


def test_explorer(fake_system: Any, tmp_path: Path) -> None:
    code, output = run("do", "explorer")
    assert code == 0, output
    assert "Explorador abierto." in output
    assert fake_system.calls_to("spawn_explorer") == [(None,)]

    code, output = run("do", "explorer", str(tmp_path))
    assert code == 0, output
    assert "Explorador abierto en" in output


def test_explorer_missing_folder(fake_system: Any, tmp_path: Path) -> None:
    code, _ = run("do", "explorer", str(tmp_path / "no_existe"))
    assert code == 1
    assert fake_system.calls == []


def test_apps(fake_system: Any) -> None:
    code, output = run("do", "apps")
    assert code == 0, output
    assert "9 apps en el índice" in output
    assert "Desinstalar Foo [bloqueada]" in output.splitlines()
    assert "Spotify" in output.splitlines()
    assert fake_system.launch_calls() == []
