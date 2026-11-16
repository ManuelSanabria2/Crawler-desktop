"""Tests de seguridad del paquete de acciones (casos 85 a 90 de la especificación C08a).

Los casos 85 a 87 solo leen el código fuente; sustituyen a los casos 42 y 43 de C07 y son
más estrictos. El caso 88 llama a las funciones reales de ``system`` solo por sus caminos
de validación, que fallan antes de tocar el sistema. Los casos 89 y 90 corren el ejecutor
sobre mundos falsos.
"""

import ast
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import geass.crawler.actions as actions_package
from geass.core.config import load_safety_config
from geass.crawler.actions import (
    ActionError,
    ActionRequest,
    DenyAllConfirmer,
    ElementExpectation,
    Executor,
    Expectation,
)
from geass.crawler.actions.system import MEDIA_KEYS, PATTERN_OPS

PACKAGE_DIR = Path(actions_package.__file__).resolve().parent
SYSTEM_FILE = PACKAGE_DIR / "system.py"
DESKTOP_FILE = PACKAGE_DIR / "desktop_win.py"
EXECUTOR_FILE = PACKAGE_DIR / "executor.py"

ONLY_IN_SYSTEM = ("SendInput", "startfile", "Popen", "subprocess", "PostMessage")
ONLY_IN_SYSTEM_AND_DESKTOP = ("get_elem_interface", "GetCurrentPattern")
FORBIDDEN_EVERYWHERE = (
    "shell=True",
    "keybd_event",
    "mouse_event",
    "SetCursorPos",
    "pynput",
    "type_keys",
    "send_keys",
    "click_input",
    "SetValue",
    "ValuePattern",
    "LegacyIAccessible",
    "DoDefaultAction",
    "SetForegroundWindow",
    "SetFocus",
)
SYSTEM_PUBLIC_FUNCTIONS = {
    "start_shortcut",
    "start_app_id",
    "spawn_explorer",
    "send_media_key",
    "read_start_apps",
    "perform_pattern",
    "click_at",
    "close_window",
}
SAFETY = load_safety_config().crawler
DANGEROUS = [*SAFETY.blocklist_patterns, *SAFETY.confirmation_dialog_accept_patterns]


def package_sources() -> dict[Path, str]:
    files = sorted(PACKAGE_DIR.rglob("*.py"))
    assert SYSTEM_FILE in files and DESKTOP_FILE in files and EXECUTOR_FILE in files
    return {path: path.read_text(encoding="utf-8") for path in files}


def test_system_calls_only_live_in_system_py() -> None:
    for path, text in package_sources().items():
        if path != SYSTEM_FILE:
            for word in ONLY_IN_SYSTEM:
                assert word not in text, f"{word} aparece en {path.name}"
        if path not in (SYSTEM_FILE, DESKTOP_FILE):
            for word in ONLY_IN_SYSTEM_AND_DESKTOP:
                assert word not in text, f"{word} aparece en {path.name}"


def test_no_forbidden_input_apis() -> None:
    for path, text in package_sources().items():
        for word in FORBIDDEN_EVERYWHERE:
            assert word not in text, f"{word} aparece en {path.name}"


def test_system_defines_only_the_eight_public_functions() -> None:
    tree = ast.parse(SYSTEM_FILE.read_text(encoding="utf-8"))
    public = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }
    assert public == SYSTEM_PUBLIC_FUNCTIONS
    public_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    assert public_classes == set()
    assert set(PATTERN_OPS) == {"invoke", "toggle", "expand", "collapse", "select"}
    assert set(MEDIA_KEYS) == {"play_pause", "next", "previous", "volume_up", "volume_down", "mute"}


def test_only_perform_acts_in_the_executor() -> None:
    text = EXECUTOR_FILE.read_text(encoding="utf-8")
    assert text.count("desktop.perform(") == 1
    assert text.count("desktop.click(") == 1
    assert ".close(" not in text
    tree = ast.parse(text)
    holders = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr in ("perform", "click")
            and isinstance(call.func.value, ast.Attribute)
            and call.func.value.attr == "_desktop"
            for call in ast.walk(node)
        )
    }
    assert holders == {"_perform"}

    desktop = DESKTOP_FILE.read_text(encoding="utf-8")
    for call in ("system.perform_pattern", "system.click_at", "system.close_window"):
        assert len(re.findall(re.escape(call), desktop)) == 1, call


def test_real_system_validation_fails_before_touching_anything(
    real_system: dict[str, Callable[..., Any]],
) -> None:
    perform, click, close = (
        real_system["perform_pattern"],
        real_system["click_at"],
        real_system["close_window"],
    )
    with pytest.raises(ActionError):
        perform(object(), "type")
    with pytest.raises(ActionError):
        perform(None, "invoke")
    for x, y in ((1.5, 2), (True, 2), ("1", 2)):
        with pytest.raises(ActionError):
            click(x, y)
    for handle in (0, -3):
        with pytest.raises(ActionError):
            close(handle)


def test_passive_does_not_import_actions() -> None:
    passive_dir = PACKAGE_DIR.parent / "passive"
    for path in sorted(passive_dir.rglob("*.py")):
        assert "geass.crawler.actions" not in path.read_text(encoding="utf-8"), path.name


def titled(pattern: str) -> str:
    return pattern[0].upper() + pattern[1:]


@pytest.mark.parametrize("pattern", DANGEROUS)
def test_dangerous_names_are_never_done_without_confirmation(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    config: Any,
    pattern: str,
) -> None:
    name = titled(pattern)

    # Elemento en la ventana actual.
    desktop = fakes.calc_world()
    desktop.get("Dos").rename(name)
    deny = DenyAllConfirmer()
    request = ActionRequest(
        kind="element", app="CalculatorApp.exe", automation_id="num2Button", action="invoke"
    )
    assert make_executor(desktop, confirmer=deny).run(request).status == "denied"
    assert desktop.performed == [] and desktop.clicks == []
    assert len(deny.requests) == 1

    # Paso intermedio de una ruta.
    desktop = fakes.notepad_world(file_name=name)
    deny = DenyAllConfirmer()
    graph = fakes.notepad_graph(config, file_name=name)
    request = ActionRequest(kind="element", app="notepad.exe", name="Configurar página")
    assert make_executor(desktop, confirmer=deny, graph=graph).run(request).status == "denied"
    assert desktop.performed == [] and desktop.clicks == []
    assert [asked.is_target for asked in deny.requests] == [False]

    # Clic por coordenadas.
    desktop = fakes.paint_world(name)
    deny = DenyAllConfirmer()
    request = ActionRequest(kind="element", app="mspaint.exe", name=name)
    assert make_executor(desktop, confirmer=deny).run(request).status == "denied"
    assert desktop.performed == [] and desktop.clicks == []


def test_every_action_has_a_gate_decision_before(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    gate_events: list[tuple[Any, ...]],
    config: Any,
) -> None:
    # Una muestra de los escenarios de los casos 33 a 73 que actúan. El fixture
    # make_executor hace esta misma comprobación al terminar cada uno de esos tests.
    settings = fakes.settings_world()
    make_executor(settings).run(
        ActionRequest(
            kind="element", app="SystemSettings.exe", name="Sistema", control_type="ListItem"
        )
    )
    calc = fakes.calc_world()
    yes = fakes.YesConfirmer()
    make_executor(calc, confirmer=yes).run(
        ActionRequest(kind="element", app="CalculatorApp.exe", automation_id="clearButton")
    )
    calc.on_perform = None
    expect = Expectation(element=ElementExpectation(automation_id="CalculatorResults", name="9"))
    make_executor(calc).run(
        ActionRequest(
            kind="element", app="CalculatorApp.exe", automation_id="num2Button", expect=expect
        )
    )
    notepad = fakes.notepad_world()
    make_executor(notepad, graph=fakes.notepad_graph(config)).run(
        ActionRequest(kind="element", app="notepad.exe", name="Configurar página")
    )
    paint = fakes.paint_world()
    make_executor(paint).run(ActionRequest(kind="element", app="mspaint.exe", name="Panel"))

    acted = [event for event in gate_events if event[0] == "act"]
    assert len(acted) == 1 + 1 + 2 + 2 + 1
    fakes.check_gate_before_actions([event[:3] for event in gate_events])
