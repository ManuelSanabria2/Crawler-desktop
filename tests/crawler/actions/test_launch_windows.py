"""Lanzamiento real de apps (casos 44 y 45 de la especificación C07).

Necesitan un escritorio de Windows real: llevan el marker ``windows_ui`` y abren de verdad
el Bloc de notas y la Calculadora. El fixture ``open_app`` (tests/crawler/conftest.py)
espera la ventana y la cierra con WM_CLOSE al terminar. La caché del índice y la auditoría
van a ``tmp_path``.

Si el índice real da ``ambiguous`` o ``not_found`` para el texto, el test se omite: depende
de lo que haya instalado.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from geass.core.config import load_crawler_config, load_safety_config
from geass.crawler.actions import AppIndex, AuditLog, LaunchResult, launch, match_app

pytestmark = pytest.mark.windows_ui


@pytest.mark.parametrize(
    ("query", "title_pattern"),
    [("bloc de nots", "Bloc de notas|Notepad"), ("calculadra", "Calculadora|Calculator")],
)
def test_launch_real_app(
    query: str,
    title_pattern: str,
    tmp_path: Path,
    open_app: Callable[[Callable[[], object], str], int],
) -> None:
    config = load_crawler_config()
    safety = load_safety_config()
    index = AppIndex.load(config, safety, cache_path=tmp_path / "app_index.json")
    audit = AuditLog(tmp_path / "audit.jsonl")

    decision = match_app(index, query, config)
    if decision.status != "would_launch":
        options = ", ".join(f"{o.entry.name} ({o.score:.1f})" for o in decision.options)
        pytest.skip(f"'{query}' da {decision.status} en este PC. Opciones: {options or 'ninguna'}")

    results: list[LaunchResult] = []

    def run() -> None:
        results.append(
            launch(
                query,
                authorized_by="test",
                index=index,
                config=config,
                safety=safety,
                audit=audit,
            )
        )

    handle = open_app(run, title_pattern)
    assert handle
    assert results[0].status == "launched"
    assert (tmp_path / "audit.jsonl").is_file()
