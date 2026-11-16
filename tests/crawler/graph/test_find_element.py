"""Tests de la búsqueda de apps y elementos (casos 8 a 15 de la especificación C05)."""

from collections.abc import Callable

import pytest

from geass.crawler.graph import AmbiguousAppError, AppNotFoundError, CrawlerGraph
from geass.crawler.store import CrawlerStore, ElementInput

Ids = dict[str, int]


@pytest.mark.parametrize("text", ["editor.exe", "EDITOR", "Editor de Prueba", "editor de prueva"])
def test_resolve_app_by_executable_name_or_typo(graph: CrawlerGraph, text: str) -> None:
    assert graph.resolve_app(text).executable == "editor.exe"


def test_resolve_app_unknown(graph: CrawlerGraph) -> None:
    with pytest.raises(AppNotFoundError, match="zzzz") as raised:
        graph.resolve_app("zzzz")
    assert "editor.exe" in str(raised.value)
    assert "calc.exe" in str(raised.value)


def test_resolve_app_ambiguous(make_store: Callable[[str], CrawlerStore]) -> None:
    with make_store("notas") as store:
        store.upsert_app("notasa.exe", "Notas A")
        store.upsert_app("notasb.exe", "Notas B")
        notes = CrawlerGraph.from_store(store)
    with pytest.raises(AmbiguousAppError) as raised:
        notes.resolve_app("notas")
    assert "notasa.exe" in str(raised.value)
    assert "notasb.exe" in str(raised.value)


def test_resolve_app_two_exact_matches_are_ambiguous(
    make_store: Callable[[str], CrawlerStore],
) -> None:
    with make_store("exactas") as store:
        store.upsert_app("uno.exe", "Visor")
        store.upsert_app("dos.exe", "Visor")
        viewers = CrawlerGraph.from_store(store)
    with pytest.raises(AmbiguousAppError):
        viewers.resolve_app("visor")


def test_find_element_ties_the_two_export_to_pdf(graph: CrawlerGraph, ids: Ids) -> None:
    matches = graph.find_element("editor", "exportar pdf")
    first, second = matches[0], matches[1]
    assert {first.element.id, second.element.id} == {
        ids["E3.Exportar a PDF"],
        ids["E5.Exportar a PDF"],
    }
    assert first.score == second.score >= 90
    # A igual puntuación y visitas, gana el id de elemento menor.
    assert first.element.id == ids["E3.Exportar a PDF"]
    assert first.state_id == ids["E3"]
    assert first.state_title == "Menú Exportar"
    calc_states = {ids["C1"]}
    assert all(match.state_id not in calc_states for match in matches)


def test_find_element_png_after_pdf_or_absent(graph: CrawlerGraph, ids: Ids) -> None:
    matches = graph.find_element("editor", "exportar pdf")
    found = [match.element.id for match in matches]
    if ids["E3.Exportar a PNG"] in found:
        assert found.index(ids["E3.Exportar a PNG"]) > 1


def test_find_element_ignores_accents_and_includes_disabled(graph: CrawlerGraph, ids: Ids) -> None:
    matches = graph.find_element("editor", "configuracion avanzada")
    assert matches[0].element.id == ids["E4.Configuración avanzada"]
    assert matches[0].score == 100.0


def test_find_element_without_matches(graph: CrawlerGraph) -> None:
    assert graph.find_element("editor", "xyzzy") == []


def test_find_element_limit_and_min_score(graph: CrawlerGraph) -> None:
    assert len(graph.find_element("editor", "exportar pdf", limit=1)) == 1
    assert graph.find_element("editor", "exportar pdf", min_score=100) == []


def test_find_element_rejects_empty_text_and_bad_limit(graph: CrawlerGraph) -> None:
    with pytest.raises(ValueError):
        graph.find_element("editor", "   ")
    with pytest.raises(ValueError):
        graph.find_element("editor", "guardar", limit=0)


def test_find_element_orders_ties_by_state_visits(
    make_store: Callable[[str], CrawlerStore],
) -> None:
    """El mismo nombre en dos estados da dos resultados; primero el del estado más visitado."""
    button = ElementInput(
        path="0/0",
        control_type="Button",
        name="Abrir",
        automation_id="",
        rect=None,
        patterns=["Invoke"],
        enabled=True,
    )
    with make_store("visitas") as store:
        app = store.upsert_app("visor.exe", "Visor")
        rare = store.upsert_state(app.id, "s1", "d1", "Poco visitado")
        frequent = store.upsert_state(app.id, "s2", "d2", "Muy visitado")
        store.upsert_state(app.id, "s2", "d2", "Muy visitado")
        store.upsert_elements(rare.id, [button])
        store.upsert_elements(frequent.id, [button])
        viewer = CrawlerGraph.from_store(store)
    matches = viewer.find_element("visor", "abrir")
    assert [match.state_id for match in matches] == [frequent.id, rare.id]
    assert matches[0].element.id > matches[1].element.id
