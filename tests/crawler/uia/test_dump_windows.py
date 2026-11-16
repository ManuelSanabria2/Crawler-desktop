"""Tests con apps reales: Bloc de notas y Calculadora. Necesitan un escritorio de Windows.

Cada test abre la app, la vuelca, comprueba elementos esperados y la cierra. Las fixtures
``open_app`` y ``settled_dump`` están en tests/crawler/conftest.py.
"""

import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.windows_ui,
    pytest.mark.skipif(sys.platform != "win32", reason="solo Windows"),
]

TIMINGS: list[tuple[str, int, float]] = []


def _report(app: str, result, record_property) -> None:  # noqa: ANN001
    from geass.core.config import load_crawler_config

    target = load_crawler_config().uia.target_dump_time_s
    elapsed_ms = result.elapsed_s * 1000
    TIMINGS.append((app, result.node_count, elapsed_ms))
    record_property("dump_ms", round(elapsed_ms, 1))
    record_property("node_count", result.node_count)
    verdict = "dentro de la meta" if result.elapsed_s < target else "FUERA de la meta"
    print(
        f"\n[volcado] {app}: {result.node_count} nodos, profundidad "
        f"{result.max_depth_reached}, {elapsed_ms:.0f} ms ({verdict} de {target:.1f} s)"
    )


def test_dump_notepad(open_app, settled_dump, record_property) -> None:  # noqa: ANN001
    handle = open_app(lambda: subprocess.Popen(["notepad.exe"]), r"bloc de notas|notepad")
    result = settled_dump(handle)
    _report("Bloc de notas", result, record_property)

    assert result.handle == handle
    assert result.process_name.lower() == "notepad.exe"
    assert not result.partial, result.partial_reasons
    assert result.error_count == 0
    assert result.root.control_type == "Window"
    assert result.root.rect is not None and result.root.rect.width > 0

    elements = list(result.root.iter_tree())
    assert result.node_count == len(elements)
    text_areas = [e for e in elements if e.control_type in ("Document", "Edit")]
    assert text_areas, "el Bloc de notas debe tener un área de texto"
    assert all(not e.is_password for e in text_areas)
    assert [e for e in elements if e.control_type == "MenuItem"], "debe haber elementos de menú"
    assert all(e.path.startswith("Window[0]") for e in elements)
    assert len({e.path for e in elements}) == len(elements)


def test_dump_calculator(open_app, settled_dump, record_property) -> None:  # noqa: ANN001
    handle = open_app(lambda: subprocess.Popen(["calc.exe"]), r"calculadora|calculator")
    result = settled_dump(handle)
    _report("Calculadora", result, record_property)

    assert result.handle == handle
    assert result.process_name.lower() == "calculatorapp.exe"
    assert not result.partial, result.partial_reasons
    assert result.root.control_type == "Window"

    for automation_id in ("num7Button", "num0Button", "plusButton", "equalButton"):
        (button,) = result.root.find_all(automation_id=automation_id)
        assert button.control_type == "Button"
        assert "Invoke" in button.patterns
        assert button.enabled
        assert button.rect is not None
    assert result.root.find_all(automation_id="CalculatorResults")


def test_dump_time_is_reported() -> None:
    """Resume los tiempos medidos por los tests anteriores."""
    if not TIMINGS:
        pytest.skip("no se volcó ninguna app")
    print("\n[volcado] resumen:")
    for app, nodes, elapsed_ms in TIMINGS:
        print(f"  {app}: {nodes} nodos en {elapsed_ms:.0f} ms")
