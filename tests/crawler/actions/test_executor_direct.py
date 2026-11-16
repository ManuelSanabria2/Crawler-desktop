"""Tests del ejecutor: acciones directas (casos 74 a 82 de la especificación C08a).

Usan el índice y el menú Inicio de prueba de C07, y los dobles de ``system``: nada se abre
de verdad. Los efectos de los dobles añaden ventanas al ``FakeDesktop``.
"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.actions import ActionRequest, AppIndex, Executor, Expectation

Audit = Callable[[Path], list[dict[str, Any]]]
SPOTIFY = ActionRequest(kind="launch", app="spotify")


@pytest.fixture
def desktop(fakes: SimpleNamespace) -> Any:
    """Escritorio con la Calculadora en primer plano."""
    return fakes.calc_world()


@pytest.fixture
def executor(
    make_executor: Callable[..., Executor], index: AppIndex, desktop: Any
) -> Callable[..., Executor]:
    def build(**kwargs: Any) -> Executor:
        return make_executor(desktop, index=index, **kwargs)

    return build


def add_window(fakes: SimpleNamespace, desktop: Any, handle: int, process: str, title: str, **kw):
    def effect(*_: Any) -> None:
        desktop.add_window(fakes.make_window(handle, process, title, **kw))

    return effect


def test_launch_and_see_the_window(
    fakes: SimpleNamespace,
    fake_system: Any,
    desktop: Any,
    executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    fake_system.effects["start_shortcut"] = add_window(
        fakes, desktop, 700, "spotify.exe", "Spotify Premium"
    )
    result = executor().run(SPOTIFY)
    assert result.status == "done"
    assert result.strategy == "direct_action"
    assert len(fake_system.calls_to("start_shortcut")) == 1
    lines = read_audit(audit_path)
    assert [(line["action"], line["result"]) for line in lines] == [
        ("launch", "launched"),
        ("execute", "done"),
    ]
    assert lines[-1]["app"] == "spotify"


def test_launch_without_window_is_retried(
    fake_system: Any, executor: Callable[..., Executor]
) -> None:
    result = executor().run(SPOTIFY)
    assert result.status == "failed"
    assert len(fake_system.calls_to("start_shortcut")) == 2


def test_launch_existing_window_in_foreground(
    fakes: SimpleNamespace, fake_system: Any, desktop: Any, executor: Callable[..., Executor]
) -> None:
    desktop.add_window(fakes.make_window(700, "spotify.exe", "Spotify"))

    def bring(*_: Any) -> None:
        desktop.foreground_handle = 700

    fake_system.effects["start_shortcut"] = bring
    result = executor().run(SPOTIFY)
    assert result.status == "done"
    assert len(fake_system.calls_to("start_shortcut")) == 1


@pytest.mark.parametrize(
    ("app", "status"),
    [("visual studio", "ambiguous"), ("zzzzqqq", "not_found"), ("desinstalar foo", "blocked")],
)
def test_launch_in_doubt(
    fake_system: Any, executor: Callable[..., Executor], app: str, status: str
) -> None:
    result = executor().run(ActionRequest(kind="launch", app=app))
    assert result.status == status
    assert fake_system.launch_calls() == []


def test_launch_of_an_excluded_window(
    fakes: SimpleNamespace, fake_system: Any, desktop: Any, executor: Callable[..., Executor]
) -> None:
    # El doble devuelve title="" para las apps excluidas, como WinDesktop.
    fake_system.effects["start_shortcut"] = add_window(fakes, desktop, 900, "chrome.exe", "")
    result = executor().run(SPOTIFY)
    assert result.status == "done_unverified"
    assert len(fake_system.calls_to("start_shortcut")) == 1


def test_launch_with_excluded_foreground_and_no_new_window(
    fakes: SimpleNamespace, fake_system: Any, desktop: Any, executor: Callable[..., Executor]
) -> None:
    desktop.add_window(fakes.make_window(900, "chrome.exe", ""), foreground=True)
    result = executor().run(SPOTIFY)
    assert result.status == "failed"
    assert len(fake_system.calls_to("start_shortcut")) == 2
    # De la ventana excluida no se leyó el árbol.
    assert ("snapshot", 900) not in desktop.reads


@pytest.mark.parametrize(("process", "status"), [("spotify.exe", "done"), ("other.exe", "failed")])
def test_launch_with_expectation(
    fakes: SimpleNamespace,
    fake_system: Any,
    desktop: Any,
    executor: Callable[..., Executor],
    process: str,
    status: str,
) -> None:
    fake_system.effects["start_shortcut"] = add_window(fakes, desktop, 700, process, "Spotify")
    request = SPOTIFY.model_copy(
        update={"expect": Expectation(process="spotify.exe", new_window=True)}
    )
    result = executor().run(request)
    assert result.status == status


def test_explorer(
    fakes: SimpleNamespace,
    fake_system: Any,
    desktop: Any,
    executor: Callable[..., Executor],
    config: CrawlerConfig,
    launcher_with: Callable[..., CrawlerConfig],
) -> None:
    fake_system.effects["spawn_explorer"] = add_window(
        fakes, desktop, 800, "explorer.exe", "Inicio", class_name="CabinetWClass"
    )
    result = executor().run(ActionRequest(kind="explorer"))
    assert result.status == "done"
    assert len(fake_system.calls_to("spawn_explorer")) == 1

    del fake_system.effects["spawn_explorer"]
    fake_system.calls.clear()
    result = executor().run(ActionRequest(kind="explorer"))
    assert result.status == "failed"
    assert len(fake_system.calls_to("spawn_explorer")) == 2

    fake_system.calls.clear()
    disabled = launcher_with(config, direct_actions=["media_play_pause"])
    result = executor(config=disabled).run(ActionRequest(kind="explorer"))
    assert result.status == "forbidden"
    assert fake_system.calls == []


def test_media(
    fake_system: Any,
    executor: Callable[..., Executor],
    config: CrawlerConfig,
    launcher_with: Callable[..., CrawlerConfig],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    result = executor().run(ActionRequest(kind="media", media="play_pause"))
    assert result.status == "done_unverified"
    assert fake_system.calls_to("send_media_key") == [("play_pause",)]
    assert read_audit(audit_path)[-1]["app"] == ""

    fake_system.calls.clear()
    disabled = launcher_with(config, direct_actions=["open_explorer"])
    result = executor(config=disabled).run(ActionRequest(kind="media", media="play_pause"))
    assert result.status == "forbidden"
    assert fake_system.calls_to("send_media_key") == []


def test_dry_run(
    fake_system: Any, executor: Callable[..., Executor], audit_path: Path, read_audit: Audit
) -> None:
    result = executor(dry_run=True).run(SPOTIFY)
    assert result.status == "would_do"
    result = executor(dry_run=True).run(ActionRequest(kind="media", media="mute"))
    assert result.status == "would_do"
    # La única llamada es la lectura de Get-StartApps al construir el índice de prueba.
    assert [name for name, _ in fake_system.calls] == ["read_start_apps"]
    assert read_audit(audit_path) == []


def test_direct_action_out_of_the_order(
    fake_system: Any, executor: Callable[..., Executor], fakes: SimpleNamespace, config: Any
) -> None:
    result = executor(config=fakes.without_step(config, "direct_action")).run(SPOTIFY)
    assert result.status == "forbidden"
    assert fake_system.launch_calls() == []
