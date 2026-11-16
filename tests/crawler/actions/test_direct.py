"""Tests de las acciones directas (casos 23 a 30 de la especificación C07).

Las funciones de ``system`` son dobles. Las reales solo se llaman en el caso 30, por caminos
de validación que fallan antes de tocar el sistema.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.actions import (
    ActionDisabledError,
    ActionError,
    AuditLog,
    MediaAction,
    media,
    open_explorer,
)
from geass.crawler.actions.system import MEDIA_KEYS

ACTIONS = get_args(MediaAction)


@pytest.mark.parametrize("action", ACTIONS)
def test_media_sends_the_key(
    action: str,
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    media(action, authorized_by="test", config=config, audit=audit)
    assert fake_system.calls == [("send_media_key", (action,))]
    lines = read_audit(audit_path)
    assert len(lines) == 1
    assert lines[0]["action"] == "media"
    assert lines[0]["target"] == action
    assert lines[0]["app"] == ""
    assert lines[0]["result"] == "done"


def test_media_rejects_unknown_actions(
    config: CrawlerConfig, audit: AuditLog, fake_system: Any
) -> None:
    with pytest.raises(ActionError):
        media("pulsar_enter", authorized_by="test", config=config, audit=audit)
    assert fake_system.calls == []


def test_disabled_media_action(
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
    launcher_with: Callable[..., CrawlerConfig],
) -> None:
    actions = [key for key in config.launcher.direct_actions if key != "media_play_pause"]
    limited = launcher_with(config, direct_actions=actions)
    with pytest.raises(ActionDisabledError, match="media_play_pause"):
        media("play_pause", authorized_by="test", config=limited, audit=audit)
    assert fake_system.calls == []
    assert read_audit(audit_path)[-1]["result"] == "disabled"

    media("next", authorized_by="test", config=limited, audit=audit)
    assert fake_system.calls == [("send_media_key", ("next",))]


def test_media_error_is_audited(
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    fake_system.errors["send_media_key"] = ActionError("SendInput rechazó la tecla")
    with pytest.raises(ActionError):
        media("mute", authorized_by="test", config=config, audit=audit)
    assert read_audit(audit_path)[-1]["result"].startswith("error:")


def test_media_needs_an_authorizer(
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    with pytest.raises(ValueError):
        media("mute", authorized_by="", config=config, audit=audit)
    assert fake_system.calls == []
    assert read_audit(audit_path) == []


def test_open_explorer_without_path(
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    assert open_explorer(authorized_by="test", config=config, audit=audit) is None
    assert fake_system.calls == [("spawn_explorer", (None,))]
    line = read_audit(audit_path)[-1]
    assert (line["action"], line["target"], line["app"], line["result"]) == (
        "open_explorer",
        "",
        "explorer.exe",
        "done",
    )


def test_open_explorer_in_a_folder(
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    tmp_path: Path,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    resolved = tmp_path.resolve()
    assert open_explorer(tmp_path, authorized_by="test", config=config, audit=audit) == resolved
    assert fake_system.calls == [("spawn_explorer", (resolved,))]
    assert read_audit(audit_path)[-1]["target"] == str(resolved)


def test_open_explorer_expands_variables(
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEASS_TEST_FOLDER", str(tmp_path))
    folder = open_explorer("%GEASS_TEST_FOLDER%", authorized_by="test", config=config, audit=audit)
    assert folder == tmp_path.resolve()


@pytest.mark.parametrize("kind", ["missing", "file", "blank"])
def test_open_explorer_rejects_bad_paths(
    kind: str,
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    tmp_path: Path,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    if kind == "missing":
        path: str | Path = tmp_path / "no_existe"
    elif kind == "file":
        path = tmp_path / "archivo.txt"
        path.write_text("x", encoding="utf-8")
    else:
        path = "   "
    with pytest.raises(ActionError):
        open_explorer(path, authorized_by="test", config=config, audit=audit)
    assert fake_system.calls == []
    assert read_audit(audit_path)[-1]["result"].startswith("error:")


def test_disabled_explorer(
    config: CrawlerConfig,
    audit: AuditLog,
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
    launcher_with: Callable[..., CrawlerConfig],
) -> None:
    actions = [key for key in config.launcher.direct_actions if key != "open_explorer"]
    limited = launcher_with(config, direct_actions=actions)
    with pytest.raises(ActionDisabledError, match="open_explorer"):
        open_explorer(authorized_by="test", config=limited, audit=audit)
    assert fake_system.calls == []
    assert read_audit(audit_path)[-1]["result"] == "disabled"


def test_real_system_functions_validate_first(
    real_system: dict[str, Callable[..., Any]], tmp_path: Path
) -> None:
    with pytest.raises(ActionError):
        real_system["send_media_key"]("enter")
    with pytest.raises(ActionError):
        real_system["start_shortcut"](tmp_path / "x.exe")
    with pytest.raises(ActionError):
        real_system["start_shortcut"](tmp_path / "no_existe.lnk")
    for app_id in ('a"b', "", " App", "App\n", "a'b"):
        with pytest.raises(ActionError):
            real_system["start_app_id"](app_id)
    assert set(MEDIA_KEYS) == set(ACTIONS)
    assert len(MEDIA_KEYS) == 6
