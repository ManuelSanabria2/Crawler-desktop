"""Tests de las rutas (casos 16 a 26 de la especificación C05).

Las rutas solo se describen: ningún test ejecuta acciones.
"""

from collections.abc import Callable, Collection
from pathlib import Path

import pytest

from geass.crawler.graph import (
    CrawlerGraph,
    NoRouteError,
    StateNotFoundError,
    TargetNotFoundError,
)
from geass.crawler.store import CrawlerStore, ElementInput

Ids = dict[str, int]

EXPORT_PDF_ROUTE = [
    '1. Desplegar "Archivo" (MenuItem)',
    '2. Desplegar "Exportar" (MenuItem)',
    '3. Pulsar "Exportar a PDF" (MenuItem)',
]


def test_route_to_export_pdf(graph: CrawlerGraph, ids: Ids) -> None:
    route = graph.path_to("editor", ids["E1"], "exportar pdf")
    assert route.cost == 2.0
    assert len(route.steps) == 3
    assert route.describe() == EXPORT_PDF_ROUTE
    assert route.target.element.id == ids["E3.Exportar a PDF"]
    assert route.target.state_id == ids["E3"]
    assert route.start_state_id == ids["E1"]
    assert route.app.executable == "editor.exe"


def test_route_step_states_and_transitions(graph: CrawlerGraph, ids: Ids) -> None:
    route = graph.path_to("editor", ids["E1"], "exportar pdf")
    assert [step.expected_state_id for step in route.steps] == [ids["E2"], ids["E3"], None]
    assert [step.transition_id for step in route.steps] == [ids["T1"], ids["T2"], None]
    assert [step.state_id for step in route.steps] == [ids["E1"], ids["E2"], ids["E3"]]
    assert [step.action_type for step in route.steps] == [
        "expand_collapse",
        "expand_collapse",
        "invoke",
    ]
    assert route.steps[0].element.id == ids["E1.Archivo"]


def test_route_skips_non_executable_and_costlier_transitions(graph: CrawlerGraph, ids: Ids) -> None:
    route = graph.path_to("editor", ids["E1"], "exportar pdf")
    used = {step.transition_id for step in route.steps}
    assert ids["T5"] not in used
    assert ids["T6"] not in used


def test_route_uses_coordinate_click_when_it_is_the_only_way(
    tmp_path: Path,
    build_db: Callable[[Path, Collection[str]], Ids],
    load_graph: Callable[[Path], CrawlerGraph],
) -> None:
    path = tmp_path / "sin_t2" / "geass_crawler.sqlite"
    ids = build_db(path, {"T2"})
    route = load_graph(path).path_to("editor", ids["E1"], "exportar pdf")
    assert len(route.steps) == 2
    assert route.cost == 3.0
    assert route.steps[0].description == 'Hacer clic en "Ayuda" (Button)'
    assert route.steps[0].transition_id == ids["T6"]


def test_target_in_the_current_state(graph: CrawlerGraph, ids: Ids) -> None:
    route = graph.path_to("editor", ids["E3"], "exportar pdf")
    assert len(route.steps) == 1
    assert route.cost == 0.0
    assert route.steps[0].expected_state_id is None
    assert route.steps[0].transition_id is None


def test_target_by_element_id(graph: CrawlerGraph, ids: Ids) -> None:
    route = graph.path_to("editor", ids["E1"], ids["E4.Aceptar"])
    assert len(route.steps) == 4
    assert route.steps[-1].description == 'Pulsar "Aceptar" (Button)'
    assert route.target.score == 100.0
    assert route.cost == 3.0


def test_route_through_a_cycle(graph: CrawlerGraph, ids: Ids) -> None:
    route = graph.path_to("editor", ids["E4"], "guardar")
    assert route.describe() == ['1. Pulsar "Aceptar" (Button)', '2. Pulsar "Guardar" (Button)']


def test_no_route_from_an_isolated_state(graph: CrawlerGraph, ids: Ids) -> None:
    with pytest.raises(NoRouteError, match="1 candidato"):
        graph.path_to("editor", ids["E5"], "guardar")


def test_state_not_found(graph: CrawlerGraph, ids: Ids) -> None:
    with pytest.raises(StateNotFoundError):
        graph.path_to("editor", ids["C1"], "guardar")
    with pytest.raises(StateNotFoundError):
        graph.path_to("editor", 9999, "guardar")


def test_target_not_found(graph: CrawlerGraph, ids: Ids) -> None:
    with pytest.raises(TargetNotFoundError):
        graph.path_to("editor", ids["E1"], "xyzzy")
    with pytest.raises(TargetNotFoundError):
        graph.path_to("editor", ids["E1"], ids["C1.Igual"])


def test_final_step_on_an_element_without_action_patterns(graph: CrawlerGraph, ids: Ids) -> None:
    route = graph.path_to("editor", ids["E1"], "listo")
    assert route.steps[-1].description == 'Hacer clic en "Listo" (Text)'
    assert route.steps[-1].action_type == "coordinate_click"


def test_description_uses_the_automation_id_when_the_name_is_empty(
    make_store: Callable[[str], CrawlerStore],
) -> None:
    unnamed = [
        ElementInput(
            path="0/0",
            control_type="Button",
            name="",
            automation_id="btnSave",
            rect=None,
            patterns=["Invoke"],
            enabled=True,
        ),
        ElementInput(
            path="0/1",
            control_type="Button",
            name="",
            automation_id="",
            rect=None,
            patterns=["Invoke"],
            enabled=True,
        ),
    ]
    with make_store("sin_nombre") as store:
        app = store.upsert_app("visor.exe", "Visor")
        state = store.upsert_state(app.id, "s1", "d1", "Principal")
        by_path = store.upsert_elements(state.id, unnamed)
        viewer = CrawlerGraph.from_store(store)
    route = viewer.path_to("visor", state.id, by_path["0/0"])
    assert route.describe() == ["1. Pulsar [btnSave] (Button)"]
    route = viewer.path_to("visor", state.id, by_path["0/1"])
    assert route.describe() == ["1. Pulsar [sin nombre] (Button)"]
