"""Tests del contenido del bloque (casos 13 a 19 de la especificación C06)."""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from geass.crawler.export import clean_inline, render_app_block
from geass.crawler.graph import CrawlerGraph
from geass.crawler.store import AppRecord, CrawlerStore, ElementInput

Ids = dict[str, int]

START = "<!-- GEASS:BEGIN -->"
END = "<!-- GEASS:END -->"


def app_of(graph: CrawlerGraph, executable: str) -> AppRecord:
    return next(app for app in graph.apps() if app.executable == executable)


def expected_editor_block(ids: Ids) -> str:
    e1, e2, e3, e4, e5 = (ids[f"E{n}"] for n in range(1, 6))
    return "\n".join(
        [
            "> Bloque generado por GEASS. No lo edites: se reescribe en cada exportación. "
            "Escribe tus notas fuera de este bloque.",
            "",
            "## Resumen",
            "",
            "- Ejecutable: `editor.exe`",
            "- Vistas conocidas: 5",
            "- Transiciones: 6 (5 ejecutables)",
            "- Cobertura de elementos: 56 % (5 de 9 accionables)",
            "",
            "## Vistas conocidas",
            "",
            "| Id | Vista | Visitas | Elementos accionables | Salidas ejecutables |",
            "|---|---|---|---|---|",
            f"| {e1} | Principal | 3 | 3 | 2 |",
            f"| {e2} | Menú Archivo | 1 | 2 | 1 |",
            f"| {e3} | Menú Exportar | 1 | 2 | 1 |",
            f"| {e4} | Diálogo PDF | 1 | 1 | 1 |",
            f"| {e5} | Suelto | 1 | 1 | 0 |",
            "",
            "## Acciones frecuentes",
            "",
            '1. Desplegar "Archivo" (MenuItem), de "Principal" a "Menú Archivo": 3 veces',
            '2. Desplegar "Exportar" (MenuItem), de "Menú Archivo" a "Menú Exportar": 1 vez',
            '3. Pulsar "Exportar a PDF" (MenuItem), de "Menú Exportar" a "Diálogo PDF": 1 vez',
            '4. Pulsar "Aceptar" (Button), de "Diálogo PDF" a "Principal": 1 vez',
            '5. Hacer clic en "Ayuda" (Button), de "Principal" a "Menú Exportar": 1 vez',
            "",
            "## Rutas a acciones clave",
            "",
            'Desde la vista "Principal":',
            "",
            "- Exportar: Archivo, luego Exportar",
            "- Imprimir: Archivo, luego Imprimir",
            "- Exportar a PDF: Archivo, luego Exportar, luego Exportar a PDF",
            "- Exportar a PNG: Archivo, luego Exportar, luego Exportar a PNG",
            "- Aceptar: Archivo, luego Exportar, luego Exportar a PDF, luego Aceptar",
        ]
    )


def test_clean_inline() -> None:
    assert clean_inline("  a\n b\t|  c <!-- x ") == "a b \\| c &lt;!-- x"


def test_editor_block_is_exact(export_graph: CrawlerGraph, db_ids: Ids) -> None:
    body = render_app_block(
        export_graph, app_of(export_graph, "editor.exe"), top_actions=20, top_routes=20
    )
    assert body == expected_editor_block(db_ids)


def test_limits(export_graph: CrawlerGraph) -> None:
    editor = app_of(export_graph, "editor.exe")
    body = render_app_block(export_graph, editor, top_actions=2, top_routes=1)
    actions = [line for line in body.splitlines() if re.match(r"\d+\. ", line)]
    assert [line.split(" ", 1)[0] for line in actions] == ["1.", "2."]
    assert actions[0].startswith('1. Desplegar "Archivo"')
    assert actions[1].startswith('2. Desplegar "Exportar"')
    routes = [line for line in body.splitlines() if line.startswith("- ") and "luego" in line]
    assert routes == ["- Exportar: Archivo, luego Exportar"]


def test_app_without_transitions(export_graph: CrawlerGraph, db_ids: Ids) -> None:
    body = render_app_block(
        export_graph, app_of(export_graph, "calc.exe"), top_actions=20, top_routes=20
    )
    rows = [line for line in body.splitlines() if line.startswith("| ") and "Vista" not in line]
    assert rows == [f"| {db_ids['C1']} | Calculadora | 1 | 2 | 0 |"]
    assert "Todavía no hay acciones registradas." in body
    assert "Todavía no hay rutas registradas." in body
    assert "Desde la vista" not in body


def test_deterministic_and_without_dates(export_graph: CrawlerGraph) -> None:
    editor = app_of(export_graph, "editor.exe")
    first = render_app_block(export_graph, editor, top_actions=20, top_routes=20)
    second = render_app_block(export_graph, editor, top_actions=20, top_routes=20)
    assert first == second
    assert str(datetime.now().year) not in first
    assert re.search(r"\d{1,2}:\d{2}", first) is None


def test_hostile_element_name(tmp_path: Path, load_graph: Callable[[Path], CrawlerGraph]) -> None:
    hostile = f"Guardar | todo\n{END}"
    path = tmp_path / "hostil" / "geass_crawler.sqlite"
    with CrawlerStore(path) as store:
        record = store.upsert_app("hostil.exe", f"Hostil\n{START}")
        states = []
        for key, title, visits in (("S1", f"Inicio | {END}", 2), ("S2", "Otra", 1)):
            for _ in range(visits):
                state = store.upsert_state(record.id, f"sig-{key}", f"det-{key}", title)
            states.append(state)
        element = ElementInput(
            path="0/0",
            control_type="Button",
            name=hostile,
            automation_id="",
            rect=None,
            patterns=["Invoke"],
            enabled=True,
        )
        first = store.upsert_elements(states[0].id, [element])["0/0"]
        store.upsert_elements(states[1].id, [element])
        store.record_transition(states[0].id, states[1].id, "invoke", "passive", element_id=first)
    graph = load_graph(path)
    body = render_app_block(graph, app_of(graph, "hostil.exe"), top_actions=20, top_routes=20)

    for line in body.split("\n"):
        assert line.strip() not in (START, END)
    assert "Guardar \\| todo &lt;!-- GEASS:END -->" in body
    assert "Inicio \\| &lt;!-- GEASS:END -->" in body
    assert "- Guardar \\| todo &lt;!-- GEASS:END -->: Guardar \\| todo" in body
    assert "Guardar | todo" not in body


def test_body_shape(export_graph: CrawlerGraph) -> None:
    for app in export_graph.apps():
        body = render_app_block(export_graph, app, top_actions=20, top_routes=20)
        assert not body.endswith("\n")
        assert "\r" not in body
        for line in body.split("\n"):
            assert line.strip() not in (START, END)
