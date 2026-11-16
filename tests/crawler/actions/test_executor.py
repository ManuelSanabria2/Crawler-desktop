"""Tests del ejecutor: elemento en la ventana actual (casos 33 a 58 de la especificación C08a).

Todo pasa sobre mundos falsos (``FakeDesktop``); nada toca el escritorio. El fixture
``make_executor`` comprueba además, al terminar cada test, que toda acción tuvo antes una
decisión de la puerta que la permitía (caso 90).
"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.errors import ConfigIncompleteError
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.actions import (
    ActionRequest,
    DenyAllConfirmer,
    ElementExpectation,
    ExecutionResult,
    Executor,
    Expectation,
    PatternState,
)

Audit = Callable[[Path], list[dict[str, Any]]]


def element(app: str, **fields: Any) -> ActionRequest:
    return ActionRequest(kind="element", app=app, **fields)


def settings(**fields: Any) -> ActionRequest:
    return element("SystemSettings.exe", **fields)


def calc(**fields: Any) -> ActionRequest:
    return element("CalculatorApp.exe", **fields)


def run(make_executor: Callable[..., Executor], desktop: Any, request: ActionRequest, **kw: Any):
    return make_executor(desktop, **kw).run(request)


def test_select_list_item(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    desktop = fakes.settings_world()
    result = run(
        make_executor, desktop, settings(name="Sistema", control_type="ListItem", action="select")
    )
    assert result.status == "done"
    assert result.strategy == "current_window_element"
    assert desktop.performed == [(fakes.SETTINGS, "Sistema", "select")]
    assert desktop.get("Sistema", "ListItem").state.selected is True
    assert desktop.get("Inicio").state.selected is False
    lines = read_audit(audit_path)
    assert [line["action"] for line in lines] == ["ui:select", "execute"]
    assert lines[0]["result"] == "done"
    assert lines[0]["details"]["gate_verdict"] == "free"
    assert lines[1]["result"] == "done"
    assert result.performed_count == 1


def test_name_is_normalized(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.settings_world()
    result = run(make_executor, desktop, settings(name="personalizacion", control_type="ListItem"))
    assert result.status == "done"
    assert desktop.performed == [(fakes.SETTINGS, "Personalización", "select")]


def test_ambiguous(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.settings_world()
    result = run(make_executor, desktop, settings(name="Personalización"))
    assert result.status == "ambiguous"
    assert desktop.performed == []
    assert "ListItem" in result.message and "Button" in result.message


def test_prefers_on_screen(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.settings_world()
    desktop.get("Sistema", "MenuItem").change(offscreen=True)
    result = run(make_executor, desktop, settings(name="Sistema"))
    assert result.status == "done"
    assert desktop.performed == [(fakes.SETTINGS, "Sistema", "select")]


def test_invoke_with_expectation(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.calc_world()
    expect = Expectation(
        element=ElementExpectation(automation_id="CalculatorResults", name="Se muestra 2")
    )
    result = run(
        make_executor, desktop, calc(automation_id="num2Button", action="invoke", expect=expect)
    )
    assert result.status == "done"
    assert desktop.performed == [(fakes.CALC, "Dos", "invoke")]


def test_inferred_invoke_and_default_verification(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.calc_world()
    result = run(make_executor, desktop, calc(name="Dos", control_type="Button"))
    assert result.status == "done"
    assert result.attempts[0].op == "invoke"
    assert desktop.get("Se muestra 2").props.automation_id == "CalculatorResults"


def test_not_found(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    desktop = fakes.calc_world()
    result = run(make_executor, desktop, calc(name="Nada"))
    assert result.status == "not_found"
    assert desktop.performed == [] and desktop.clicks == []
    lines = read_audit(audit_path)
    assert [(line["action"], line["result"]) for line in lines] == [("execute", "not_found")]


def test_foreground_of_another_app(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.calc_world()
    desktop.add_window(fakes.settings_world().window(fakes.SETTINGS))
    desktop.foreground_handle = fakes.CALC
    result = run(make_executor, desktop, settings(name="Sistema"))
    assert result.status == "aborted"
    assert not any(read[0] == "find" for read in desktop.reads)


def test_no_foreground(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.settings_world()
    desktop.foreground_handle = None
    result = run(make_executor, desktop, settings(name="Sistema"))
    assert result.status == "forbidden"
    assert "unknown_window" in result.message
    assert desktop.performed == []


def test_excluded_app_is_never_read(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.settings_world()
    result = run(make_executor, desktop, element("chrome.exe", name="Nueva pestaña"))
    assert result.status == "forbidden"
    assert "excluded_app" in result.message
    assert desktop.reads == []


def test_excluded_foreground(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.notepad_world()
    desktop.add_window(fakes.make_window(900, "chrome.exe", ""), foreground=True)
    result = run(make_executor, desktop, element("chrome.exe", name="Nueva pestaña"))
    assert result.status == "forbidden" and "excluded_app" in result.message
    assert desktop.reads == []

    result = run(make_executor, desktop, element("notepad.exe", name="Archivo"))
    assert result.status == "forbidden" and "excluded_app" in result.message
    assert desktop.reads == [("foreground",)]


def test_foreground_changes_before_acting(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.settings_world()
    desktop.add_window(fakes.calc_window())

    def switch(desktop: Any) -> None:
        desktop.foreground_handle = fakes.CALC

    desktop.after_find = switch
    result = run(make_executor, desktop, settings(name="Sistema", control_type="ListItem"))
    assert result.status == "aborted"
    assert desktop.performed == []


def test_blocklist_is_denied(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    desktop = fakes.calc_world()
    deny = DenyAllConfirmer()
    result = run(
        make_executor, desktop, calc(automation_id="clearButton", action="invoke"), confirmer=deny
    )
    assert result.status == "denied"
    assert desktop.performed == []
    [asked] = deny.requests
    assert (asked.rule, asked.pattern, asked.is_target) == ("blocklist_patterns", "borrar", True)
    ui = [line for line in read_audit(audit_path) if line["action"] == "ui:invoke"]
    assert [line["result"] for line in ui] == ["denied"]


def test_blocklist_confirmed(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    desktop = fakes.calc_world()
    yes = fakes.YesConfirmer("test:manuel")
    result = run(
        make_executor, desktop, calc(automation_id="clearButton", action="invoke"), confirmer=yes
    )
    assert len(desktop.performed) == 1
    assert len(yes.requests) == 1
    ui = [line for line in read_audit(audit_path) if line["action"] == "ui:invoke"]
    assert ui[0]["details"]["confirmed_by"] == "test:manuel"
    assert result.attempts[0].confirmed is True


def test_confirmed_is_never_retried(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.calc_world()
    desktop.on_perform = None
    expect = Expectation(
        element=ElementExpectation(automation_id="CalculatorResults", name="Se muestra 99")
    )
    result = run(
        make_executor,
        desktop,
        calc(automation_id="clearButton", action="invoke", expect=expect),
        confirmer=fakes.YesConfirmer(),
    )
    assert result.status == "failed"
    assert len(desktop.performed) == 1


def test_retry_once_then_done(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.calc_world()
    effect = desktop.on_perform
    calls = {"n": 0}

    def second_time(desktop: Any, live: Any, op: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            effect(desktop, live, op)

    desktop.on_perform = second_time
    result = run(make_executor, desktop, calc(automation_id="num2Button", action="invoke"))
    assert result.status == "done"
    assert len(desktop.performed) == 2
    assert [attempt.try_number for attempt in result.attempts] == [1, 2]
    assert [attempt.result for attempt in result.attempts] == ["failed", "done"]


def test_retries_exhausted(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.calc_world()
    desktop.on_perform = None
    result = run(make_executor, desktop, calc(automation_id="num2Button", action="invoke"))
    assert result.status == "failed"
    assert len(desktop.performed) == config.executor.max_retries + 1


def test_verification_disabled(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.calc_world()
    desktop.on_perform = None
    result = run(
        make_executor,
        desktop,
        calc(automation_id="num2Button", action="invoke"),
        config=fakes.with_executor(config, verify_transition=False),
    )
    assert result.status == "done_unverified"
    assert len(desktop.performed) == 1


def add_toggle(fakes: SimpleNamespace, desktop: Any) -> None:
    desktop.window(fakes.SETTINGS).elements.append(
        fakes.make_element(
            "Button",
            "Modo oscuro",
            patterns=("Toggle",),
            state=PatternState(toggle="off"),
            rect=(400, 300, 500, 340),
        )
    )


def test_toggle(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.settings_world()
    add_toggle(fakes, desktop)
    result = run(make_executor, desktop, settings(name="Modo oscuro", action="toggle"))
    assert result.status == "done"
    assert desktop.get("Modo oscuro").state.toggle == "on"

    desktop = fakes.settings_world()
    add_toggle(fakes, desktop)
    desktop.on_perform = None
    result = run(make_executor, desktop, settings(name="Modo oscuro", action="toggle"))
    assert result.status == "failed"
    assert len(desktop.performed) == 2


@pytest.mark.parametrize(("before", "op"), [("collapsed", "expand"), ("expanded", "collapse")])
def test_expand_collapse_from_state(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], before: str, op: str
) -> None:
    desktop = fakes.settings_world()
    menu = desktop.get("Sistema", "MenuItem")
    menu.state = PatternState(expand=before)
    result = run(make_executor, desktop, settings(name="Sistema", control_type="MenuItem"))
    assert result.status == "done"
    assert desktop.performed == [(fakes.SETTINGS, "Sistema", op)]
    assert menu.state.expand == ("expanded" if op == "expand" else "collapsed")


def test_unsupported_action(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.settings_world()
    result = run(
        make_executor,
        desktop,
        settings(name="Sistema", control_type="ListItem", action="toggle"),
        config=fakes.without_step(config, "coordinate_click"),
    )
    assert result.status == "failed"
    assert "no admite toggle" in result.message
    assert desktop.performed == [] and desktop.clicks == []


def test_password_field(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.settings_world()
    desktop.window(fakes.SETTINGS).elements.append(
        fakes.make_element("Edit", "Clave Wi-Fi", patterns=("Invoke",), is_password=True)
    )
    result = run(make_executor, desktop, settings(name="Clave Wi-Fi", action="invoke"))
    assert result.status == "forbidden"
    assert result.attempts[-1].result == "forbidden: password"
    assert desktop.performed == []


def test_dry_run(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    desktop = fakes.settings_world()
    result = run(
        make_executor,
        desktop,
        settings(name="Sistema", control_type="ListItem", action="select"),
        dry_run=True,
    )
    assert result.status == "would_do"
    assert desktop.performed == []

    desktop = fakes.calc_world()
    deny = DenyAllConfirmer()
    result = run(
        make_executor,
        desktop,
        calc(automation_id="clearButton", action="invoke"),
        dry_run=True,
        confirmer=deny,
    )
    assert result.status == "would_do"
    gate = result.attempts[-1].gate
    assert gate is not None and gate.verdict == "confirm"
    assert "requeriría confirmación" in result.message
    assert deny.requests == []
    assert desktop.performed == []
    assert read_audit(audit_path) == []


def test_needs_an_authorizer(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    with pytest.raises(ValueError):
        make_executor(fakes.calc_world(), authorized_by="")


def test_stop_requested(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.calc_world()
    result = run(
        make_executor,
        desktop,
        calc(automation_id="num2Button", action="invoke"),
        should_stop=lambda: True,
    )
    assert result.status == "aborted"
    assert desktop.performed == []


def permissions_world(fakes: SimpleNamespace) -> Any:
    window = fakes.make_window(
        500,
        "foo.exe",
        "Permisos de Foo",
        [fakes.make_element("Button", "Permitir", patterns=("Invoke",), rect=(10, 10, 90, 40))],
    )
    desktop = fakes.FakeDesktop([window], foreground=500)
    desktop.on_perform = lambda desktop, live, op: desktop.remove_window(500)
    return desktop


def test_authorization_button(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Audit,
) -> None:
    safety = fakes.safety_with_allowed_windows(
        [{"executable": "foo.exe", "title_pattern": "Permisos"}]
    )
    order = element("foo.exe", name="Permitir", authorize=True)

    yes = fakes.YesConfirmer()
    desktop = permissions_world(fakes)
    result = run(make_executor, desktop, order, safety=safety, confirmer=yes)
    assert result.status == "done"
    assert [asked.rule for asked in yes.requests] == ["authorization"]

    desktop = permissions_world(fakes)
    result = run(make_executor, desktop, order, safety=safety)
    assert result.status == "denied"
    assert desktop.performed == []

    deny = DenyAllConfirmer()
    desktop = permissions_world(fakes)
    result = run(
        make_executor,
        desktop,
        element("foo.exe", name="Permitir"),
        safety=safety,
        confirmer=deny,
    )
    assert result.status == "forbidden"
    assert deny.requests == []

    desktop = permissions_world(fakes)
    with pytest.raises(ConfigIncompleteError):
        run(make_executor, desktop, order)
    assert desktop.performed == []
    last = read_audit(audit_path)[-1]
    assert last["action"] == "execute"
    assert last["result"].startswith("error:")


def test_result_model(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.calc_world()
    result = run(make_executor, desktop, calc(automation_id="num2Button", action="invoke"))
    assert isinstance(result, ExecutionResult)
    assert result.request.automation_id == "num2Button"
    attempt = result.attempts[0]
    assert (attempt.strategy, attempt.step, attempt.performed, attempt.verified) == (
        "current_window_element",
        0,
        True,
        True,
    )
