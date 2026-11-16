"""Tests de firmas con apps reales. Necesitan un escritorio de Windows.

El caso del Bloc de notas con distinto texto se prueba solo con volcados guardados: para
ponerle texto habría que escribir en la ventana o abrir archivos, y el Bloc de notas de
Windows 11 deja esas pestañas en la sesión de Manuel.
"""

import os
import subprocess
import sys
import time

import pytest

pytestmark = [
    pytest.mark.windows_ui,
    pytest.mark.skipif(sys.platform != "win32", reason="solo Windows"),
]

REPEATS = 5


def test_same_view_dumped_several_times_keeps_its_signature(open_app, settled_dump) -> None:  # noqa: ANN001
    from geass.core.config import load_crawler_config
    from geass.crawler.signature import compare_dumps, compute_signature, explain
    from geass.crawler.uia import dump_window

    handle = open_app(lambda: subprocess.Popen(["calc.exe"]), r"calculadora|calculator")
    reference = settled_dump(handle)
    first = compute_signature(reference)
    dumps = []
    for _ in range(REPEATS):
        time.sleep(0.3)
        dumps.append(dump_window(handle))

    # Si alguna firma cambia, el mensaje del test dice qué elementos cambiaron.
    differences = [
        "\n".join(explain(compare_dumps(reference, dump)))
        for dump in dumps
        if compute_signature(dump).detailed != first.detailed
    ]
    report = "\n\n".join(differences)
    matches = sum(compute_signature(dump).structural == first.structural for dump in dumps)
    print(f"\n[firma] Calculadora: {matches}/{REPEATS} volcados con la misma firma estructural")
    assert matches / REPEATS >= load_crawler_config().signature.target_repeatability, report
    assert not differences, report


def test_live_calculator_matches_the_saved_fixture(open_app, settled_dump, uia_fixture) -> None:  # noqa: ANN001
    from geass.crawler.signature import compare_dumps, explain

    handle = open_app(lambda: subprocess.Popen(["calc.exe"]), r"calculadora|calculator")
    live = settled_dump(handle)
    if not live.root.find_all(automation_id="num7Button"):
        pytest.skip("la Calculadora no está en modo estándar")
    comparison = compare_dumps(uia_fixture("calculator_a"), live)
    assert comparison.structural_equal, "\n".join(explain(comparison))


def test_two_settings_pages_have_different_signatures(open_app, settled_dump) -> None:  # noqa: ANN001
    from geass.crawler.signature import compute_signature

    title = r"^configuraci[oó]n$|^settings$"
    handle = open_app(lambda: os.startfile("ms-settings:mousetouchpad"), title)
    mouse = settled_dump(handle)
    os.startfile("ms-settings:colors")
    colors = settled_dump(handle, differs_from=mouse)

    assert mouse.process_name == colors.process_name == "SystemSettings.exe"
    first, second = compute_signature(mouse), compute_signature(colors)
    print(f"\n[firma] Mouse {first.structural[:12]} | Colores {second.structural[:12]}")
    assert first.structural != second.structural
    assert first.detailed != second.detailed

    # Volver a la primera página da otra vez la primera firma.
    os.startfile("ms-settings:mousetouchpad")
    again = compute_signature(settled_dump(handle, differs_from=colors))
    assert again.structural == first.structural
