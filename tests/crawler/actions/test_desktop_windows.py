"""Escritorio real (casos 91 y 92 de la especificación C08a).

Necesitan un escritorio de Windows real: llevan el marker ``windows_ui``. El fixture
``open_app`` (tests/crawler/conftest.py) abre la Calculadora, espera su ventana y la cierra
con WM_CLOSE al terminar; si ya había una abierta, el test se omite. El caso 91 solo lee.
El caso 92 pulsa botones de la Calculadora con ``DenyAllConfirmer``: nada que requiera
confirmación se hace. La auditoría va a ``tmp_path``.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from geass.core.config import load_crawler_config, load_safety_config
from geass.crawler.actions import (
    ActionRequest,
    AuditLog,
    DenyAllConfirmer,
    ElementExpectation,
    Executor,
    Expectation,
    WinDesktop,
)

pytestmark = pytest.mark.windows_ui

CALC_TITLE = r"calculadora|calculator"


def open_calculator(open_app: Callable[[Callable[[], object], str], int]) -> int:
    return open_app(lambda: subprocess.Popen(["calc.exe"]), CALC_TITLE)


def test_read_the_calculator(open_app: Callable[[Callable[[], object], str], int]) -> None:
    open_calculator(open_app)
    desktop = WinDesktop(load_crawler_config())
    window = desktop.foreground()
    assert window is not None
    assert window.process_name.lower() == "calculatorapp.exe"
    found = desktop.find(window, automation_id="num2Button")
    assert found
    assert "Invoke" in found[0].props.patterns
    desktop.state(found[0])


def test_two_plus_three(
    open_app: Callable[[Callable[[], object], str], int], tmp_path: Path
) -> None:
    open_calculator(open_app)
    config = load_crawler_config()
    executor = Executor(
        authorized_by="test",
        confirmer=DenyAllConfirmer(),
        desktop=WinDesktop(config),
        config=config,
        safety=load_safety_config(),
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )

    def press(automation_id: str, shown: str) -> str:
        # Con expectativa en cada paso, una verificación que tarda no repite la pulsación
        # por error: el resultado esperado ya se ve.
        expect = Expectation(
            element=ElementExpectation(automation_id="CalculatorResults", name=shown)
        )
        request = ActionRequest(
            kind="element",
            app="CalculatorApp.exe",
            automation_id=automation_id,
            action="invoke",
            expect=expect,
        )
        return executor.run(request).status

    assert press("num2Button", "Se muestra 2") == "done"
    assert press("plusButton", "Se muestra 2") == "done"
    assert press("num3Button", "Se muestra 3") == "done"
    assert press("equalButton", "Se muestra 5") == "done"
