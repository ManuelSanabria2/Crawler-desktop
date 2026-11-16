"""Tests de la construcción del grafo (casos 3 a 7 de la especificación C05)."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from geass.crawler.graph import AppNotFoundError, CrawlerGraph, is_executable
from geass.crawler.store import CrawlerStore, TransitionRecord

Ids = dict[str, int]


def transition(action_type: str, element_id: int | None) -> TransitionRecord:
    return TransitionRecord(
        id=1,
        source_state_id=1,
        target_state_id=2,
        element_id=element_id,
        action_type=action_type,
        source="passive",
        count=1,
        mean_latency_s=None,
        latency_samples=0,
        last_seen=datetime(2026, 11, 12, tzinfo=UTC),
    )


def test_graph_has_a_node_per_state_and_an_edge_per_transition(
    graph: CrawlerGraph, ids: Ids
) -> None:
    assert graph.graph.number_of_nodes() == 6
    assert graph.graph.number_of_edges() == 6
    node = graph.graph.nodes[ids["E1"]]
    assert node["visits"] == 3
    assert node["executable"] == "editor.exe"
    assert node["title"] == "Principal"
    assert node["app_id"] == ids["editor"]
    assert list(graph.graph.nodes) == sorted(graph.graph.nodes)


def test_edge_attributes(graph: CrawlerGraph, ids: Ids) -> None:
    t1 = graph.graph.edges[ids["E1"], ids["E2"], ids["T1"]]
    assert t1["executable"] is True
    assert t1["cost"] == 1.0
    assert t1["count"] == 3
    assert t1["transition_id"] == ids["T1"]
    assert t1["element_id"] == ids["E1.Archivo"]
    assert t1["action_type"] == "expand_collapse"
    assert t1["source"] == "passive"
    t5 = graph.graph.edges[ids["E1"], ids["E3"], ids["T5"]]
    assert t5["executable"] is False
    t6 = graph.graph.edges[ids["E1"], ids["E3"], ids["T6"]]
    assert t6["executable"] is True
    assert t6["cost"] == 3.0
    assert t6["source"] == "active"


def test_is_executable() -> None:
    assert not is_executable(transition("unknown", 7))
    assert not is_executable(transition("set_value", 7))
    assert not is_executable(transition("invoke", None))
    assert is_executable(transition("toggle", 7))


def test_from_store_does_not_modify_the_database(synthetic_db: tuple[Path, Ids]) -> None:
    with CrawlerStore(synthetic_db[0]) as store:
        before = store.counts()
        CrawlerGraph.from_store(store)
        assert store.counts() == before


def test_empty_database(make_store: Callable[[str], CrawlerStore]) -> None:
    with make_store("vacia") as store:
        empty = CrawlerGraph.from_store(store)
    assert empty.graph.number_of_nodes() == 0
    assert empty.coverage() == []
    with pytest.raises(AppNotFoundError):
        empty.resolve_app("x")


def test_transition_with_a_missing_element_is_not_executable(
    synthetic_db: tuple[Path, Ids],
) -> None:
    """Una transición cuyo elemento no está entre los leídos se trata como sin elemento."""
    path, ids = synthetic_db
    with CrawlerStore(path) as store:
        states = store.list_states()
        elements = [e for state in states for e in store.list_elements(state.id)]
        apps, transitions = store.list_apps(), store.list_transitions()
    without_archivo = [e for e in elements if e.id != ids["E1.Archivo"]]
    rebuilt = CrawlerGraph(
        apps,
        states,
        without_archivo,
        transitions,
        fuzzy_match_threshold=80,
        coordinate_click_cost=3.0,
    )
    t1 = rebuilt.graph.edges[ids["E1"], ids["E2"], ids["T1"]]
    assert t1["executable"] is False
    assert t1["element_id"] is None


def test_data_accessors(graph: CrawlerGraph, ids: Ids) -> None:
    """Caso 39 de la especificación C06: lectura de apps, estados, elementos y transiciones."""
    from geass.crawler.graph import describe_action, element_label

    apps = graph.apps()
    assert [app.executable for app in apps] == ["calc.exe", "editor.exe"]
    editor = apps[1]
    states = graph.states_of(editor)
    assert [state.id for state in states] == sorted(ids[f"E{n}"] for n in range(1, 6))
    elements = graph.elements_of(ids["E1"])
    assert len(elements) == 4
    assert [element.id for element in elements] == sorted(element.id for element in elements)
    assert graph.elements_of(9999) == []
    transitions = graph.transitions_of(editor)
    assert len(transitions) == 6
    assert [t.id for t in transitions] == sorted(ids[f"T{n}"] for n in range(1, 7))
    by_id = {t.id: t for t in transitions}
    assert graph.is_route_edge(by_id[ids["T5"]]) is False
    assert graph.is_route_edge(by_id[ids["T1"]]) is True
    archivo = graph.element(ids["E1.Archivo"])
    assert archivo is not None
    assert element_label(archivo) == "Archivo"
    assert describe_action("expand_collapse", archivo) == 'Desplegar "Archivo" (MenuItem)'
    assert graph.element(9999) is None
    state = graph.state(ids["E1"])
    assert state is not None and state.title_pattern == "Principal"
    assert graph.state(9999) is None
