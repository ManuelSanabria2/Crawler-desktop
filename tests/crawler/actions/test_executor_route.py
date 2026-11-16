"""Tests del ejecutor: ruta del grafo (casos 59 a 65 de la especificación C08a).

El grafo lo construye ``fakes.notepad_graph`` (conftest). Las firmas estructurales de S1
(menú cerrado) y S2 (menú Archivo desplegado) se calculan con ``compute_signature`` sobre el
volcado del mundo falso.
"""

from collections.abc import Callable
from types import SimpleNamespace

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.actions import ActionRequest, DenyAllConfirmer, Executor

TARGET = ActionRequest(kind="element", app="notepad.exe", name="Configurar página")


def test_route_opens_the_menu_and_presses_the_target(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.notepad_world()
    result = make_executor(desktop, graph=fakes.notepad_graph(config)).run(TARGET)
    assert result.status == "done"
    assert result.strategy == "graph_route"
    assert desktop.performed == [
        (fakes.NOTEPAD, "Archivo", "invoke"),
        (fakes.NOTEPAD, "Configurar página", "invoke"),
    ]
    assert [attempt.step for attempt in result.attempts] == [1, 0]
    assert all(attempt.result == "done" for attempt in result.attempts)


def test_broken_route(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.notepad_world()
    desktop.on_perform = None
    result = make_executor(desktop, graph=fakes.notepad_graph(config)).run(TARGET)
    assert result.status == "failed"
    assert "la ruta se rompió en el paso 1" in result.message
    assert desktop.performed == [(fakes.NOTEPAD, "Archivo", "invoke")] * 2
    assert all(name != "Configurar página" for _, name, _ in desktop.performed)


def test_unknown_current_state(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.notepad_world()
    graph = fakes.notepad_graph(config, signatures=("otra-1", "otra-2"))
    result = make_executor(desktop, graph=graph).run(TARGET)
    assert result.status == "not_found"
    assert "no conozco el estado actual" in result.message
    assert desktop.performed == []


def test_destructive_intermediate_step_is_denied(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.notepad_world(file_name="Borrar")
    deny = DenyAllConfirmer()
    graph = fakes.notepad_graph(config, file_name="Borrar")
    result = make_executor(desktop, graph=graph, confirmer=deny).run(TARGET)
    assert result.status == "denied"
    [asked] = deny.requests
    assert asked.is_target is False
    assert desktop.performed == []


def test_authorization_intermediate_step_is_forbidden(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.notepad_world(file_name="Permitir")
    graph = fakes.notepad_graph(config, file_name="Permitir")
    order = TARGET.model_copy(update={"authorize": True})
    result = make_executor(desktop, graph=graph).run(order)
    assert result.status == "forbidden"
    assert result.attempts[-1].result == "forbidden: authorization"
    assert desktop.performed == []


def test_similar_name_is_not_a_target(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.notepad_world()
    graph = fakes.notepad_graph(config, target_name="Configurar página 2")
    result = make_executor(desktop, graph=graph).run(TARGET)
    assert result.status == "not_found"
    assert desktop.performed == []


def test_coordinate_transition_without_coordinate_click(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.notepad_world()
    graph = fakes.notepad_graph(config, action="coordinate_click")
    result = make_executor(
        desktop, graph=graph, config=fakes.without_step(config, "coordinate_click")
    ).run(TARGET)
    assert result.status == "failed"
    assert "la ruta se rompió en el paso 1" in result.message
    assert desktop.clicks == [] and desktop.performed == []
