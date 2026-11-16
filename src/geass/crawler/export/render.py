"""Contenido del bloque administrado de la nota de una app.

El texto depende solo de los datos del grafo: no lleva fecha ni hora de exportación, para que
exportar dos veces sin cambios en la base dé el mismo resultado. Los nombres que salen de la
interfaz son datos: pasan por ``clean_inline`` antes de escribirse.
"""

import re

from geass.crawler.graph import (
    ACTION_PATTERNS,
    CrawlerGraph,
    NoRouteError,
    Route,
    describe_action,
    element_label,
    normalize_text,
)
from geass.crawler.store.records import AppRecord, ElementRecord, StateRecord, TransitionRecord

_WHITESPACE = re.compile(r"\s+")

NOTICE = (
    "> Bloque generado por GEASS. No lo edites: se reescribe en cada exportación. "
    "Escribe tus notas fuera de este bloque."
)
NO_VIEWS = "Todavía no hay vistas registradas."
NO_ACTIONS = "Todavía no hay acciones registradas."
NO_ROUTES = "Todavía no hay rutas registradas."
UNTITLED = "(sin título)"


def clean_inline(text: str) -> str:
    """Texto seguro para una línea o una celda de tabla de Markdown."""
    text = _WHITESPACE.sub(" ", text).strip()
    return text.replace("|", "\\|").replace("<", "&lt;")


def render_app_block(
    graph: CrawlerGraph, app: AppRecord, *, top_actions: int, top_routes: int
) -> str:
    """Cuerpo del bloque administrado de una app, sin los marcadores y sin salto final."""
    lines = [NOTICE, ""]
    lines += _summary(graph, app)
    lines += ["", "## Vistas conocidas", ""]
    lines += _views(graph, app)
    lines += ["", "## Acciones frecuentes", ""]
    lines += _actions(graph, app, top_actions)
    lines += ["", "## Rutas a acciones clave", ""]
    lines += _routes(graph, app, top_routes)
    return "\n".join(lines)


def _summary(graph: CrawlerGraph, app: AppRecord) -> list[str]:
    entry = graph.coverage(app.executable)[0]
    percent = f"{entry.element_coverage * 100:.0f} %"
    return [
        "## Resumen",
        "",
        f"- Ejecutable: `{clean_inline(app.executable)}`",
        f"- Vistas conocidas: {entry.states}",
        f"- Transiciones: {entry.transitions} ({entry.executable_transitions} ejecutables)",
        f"- Cobertura de elementos: {percent} "
        f"({entry.exercised_elements} de {entry.actionable_elements} accionables)",
    ]


def _views(graph: CrawlerGraph, app: AppRecord) -> list[str]:
    states = sorted(graph.states_of(app), key=lambda state: (-state.visits, state.id))
    if not states:
        return [NO_VIEWS]
    exits: dict[int, int] = {}
    for transition in graph.transitions_of(app):
        if graph.is_route_edge(transition):
            exits[transition.source_state_id] = exits.get(transition.source_state_id, 0) + 1
    lines = [
        "| Id | Vista | Visitas | Elementos accionables | Salidas ejecutables |",
        "|---|---|---|---|---|",
    ]
    for state in states:
        actionable = sum(1 for element in graph.elements_of(state.id) if _actionable(element))
        lines.append(
            f"| {state.id} | {_view_label(state)} | {state.visits} | {actionable} | "
            f"{exits.get(state.id, 0)} |"
        )
    return lines


def _actions(graph: CrawlerGraph, app: AppRecord, top_actions: int) -> list[str]:
    edges = [t for t in graph.transitions_of(app) if graph.is_route_edge(t)]
    edges.sort(key=lambda transition: (-transition.count, transition.id))
    if not edges:
        return [NO_ACTIONS]
    return [
        f"{number}. {_action_line(graph, transition)}"
        for number, transition in enumerate(edges[:top_actions], 1)
    ]


def _action_line(graph: CrawlerGraph, transition: TransitionRecord) -> str:
    element = graph.element(transition.element_id) if transition.element_id is not None else None
    source = graph.state(transition.source_state_id)
    target = graph.state(transition.target_state_id)
    if element is None or source is None or target is None:  # no pasa con is_route_edge
        raise ValueError(f"La transición {transition.id} no es una arista de ruta.")
    times = "vez" if transition.count == 1 else "veces"
    return (
        f"{clean_inline(describe_action(transition.action_type, element))}, "
        f'de "{_view_label(source)}" a "{_view_label(target)}": {transition.count} {times}'
    )


def _routes(graph: CrawlerGraph, app: AppRecord, top_routes: int) -> list[str]:
    states = graph.states_of(app)
    if not states:
        return [NO_ROUTES]
    start = min(states, key=lambda state: (-state.visits, state.id))

    found: list[tuple[float, int, Route]] = []
    for state in states:
        if state.id == start.id:
            continue
        for element in graph.elements_of(state.id):
            if not _actionable(element):
                continue
            try:
                route = graph.path_to(app.executable, start.id, element.id)
            except NoRouteError:
                continue
            found.append((route.cost, element.id, route))

    best: dict[str, tuple[float, int, Route]] = {}
    for candidate in sorted(found, key=lambda item: (item[0], item[1])):
        best.setdefault(normalize_text(candidate[2].target.element.name), candidate)
    chosen = sorted(best.values(), key=lambda item: (item[0], item[1]))[:top_routes]
    if not chosen:
        return [NO_ROUTES]

    lines = [f'Desde la vista "{_view_label(start)}":', ""]
    for _, _, route in chosen:
        steps = ", luego ".join(clean_inline(element_label(step.element)) for step in route.steps)
        lines.append(f"- {clean_inline(element_label(route.target.element))}: {steps}")
    return lines


def _actionable(element: ElementRecord) -> bool:
    return element.enabled and any(pattern in ACTION_PATTERNS for pattern in element.patterns)


def _view_label(state: StateRecord) -> str:
    return clean_inline(state.title_pattern) or UNTITLED
