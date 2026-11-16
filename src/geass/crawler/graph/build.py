"""Grafo dirigido de estados y transiciones construido desde la base del crawler.

Solo lee la base y calcula. Las rutas que devuelve son descripciones: este módulo no
ejecuta ninguna acción ni toca la interfaz. La puerta de seguridad la aplica el ejecutor.
"""

from collections.abc import Sequence

import networkx as nx
from rapidfuzz import fuzz

from geass.core.log import get_logger
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.graph.model import (
    AmbiguousAppError,
    AppCoverage,
    AppNotFoundError,
    ElementMatch,
    NoRouteError,
    Route,
    RouteStep,
    StateNotFoundError,
    TargetNotFoundError,
)
from geass.crawler.graph.text import normalize_text
from geass.crawler.passive.pointer import infer_action
from geass.crawler.store.db import CrawlerStore
from geass.crawler.store.records import (
    ActionType,
    AppRecord,
    ElementRecord,
    StateRecord,
    TransitionRecord,
)

logger = get_logger(__name__)

ACTION_PATTERNS: tuple[str, ...] = ("Invoke", "Toggle", "ExpandCollapse", "SelectionItem")
EXECUTABLE_ACTIONS: frozenset[str] = frozenset(
    {"invoke", "toggle", "expand_collapse", "select", "coordinate_click"}
)

_DEFAULT_COST = 1.0
_EXACT_SCORE = 100.0
_VERBS: dict[str, str] = {
    "invoke": "Pulsar",
    "toggle": "Alternar",
    "expand_collapse": "Desplegar",
    "select": "Seleccionar",
    "coordinate_click": "Hacer clic en",
}


def is_executable(transition: TransitionRecord) -> bool:
    """Verdadero si la transición se puede repetir: tiene elemento y una acción conocida."""
    return transition.element_id is not None and transition.action_type in EXECUTABLE_ACTIONS


class CrawlerGraph:
    """Grafo dirigido de estados y transiciones, con las consultas del crawler."""

    def __init__(
        self,
        apps: Sequence[AppRecord],
        states: Sequence[StateRecord],
        elements: Sequence[ElementRecord],
        transitions: Sequence[TransitionRecord],
        *,
        fuzzy_match_threshold: int,
        coordinate_click_cost: float,
    ) -> None:
        self._threshold = float(fuzzy_match_threshold)
        self._coordinate_click_cost = float(coordinate_click_cost)
        self._apps: dict[int, AppRecord] = {app.id: app for app in apps}
        self._states: dict[int, StateRecord] = {
            state.id: state for state in sorted(states, key=lambda s: s.id)
        }
        self._elements: dict[int, ElementRecord] = {
            element.id: element for element in sorted(elements, key=lambda e: e.id)
        }
        self._elements_by_state: dict[int, list[ElementRecord]] = {}
        for element in self._elements.values():
            self._elements_by_state.setdefault(element.state_id, []).append(element)
        self._transitions: list[TransitionRecord] = []
        for transition in sorted(transitions, key=lambda t: t.id):
            if (
                transition.source_state_id not in self._states
                or transition.target_state_id not in self._states
            ):
                logger.warning(
                    "Transición %d omitida: su estado de origen o de destino no está en el grafo.",
                    transition.id,
                )
                continue
            self._transitions.append(transition)

        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        for state in self._states.values():
            app = self._apps.get(state.app_id)
            self._graph.add_node(
                state.id,
                app_id=state.app_id,
                executable=app.executable if app is not None else None,
                title=state.title_pattern,
                visits=state.visits,
            )
        for transition in self._transitions:
            self._graph.add_edge(
                transition.source_state_id,
                transition.target_state_id,
                key=transition.id,
                transition_id=transition.id,
                element_id=self._element_id(transition),
                action_type=transition.action_type,
                source=transition.source,
                count=transition.count,
                mean_latency_s=transition.mean_latency_s,
                executable=self._executable(transition),
                cost=self._cost(transition),
            )
        self._routes = self._route_graph()

    @classmethod
    def from_store(cls, store: CrawlerStore, config: CrawlerConfig | None = None) -> "CrawlerGraph":
        """Construye el grafo leyendo la base con la interfaz pública de ``CrawlerStore``.

        No escribe en la base. Sin ``config`` se carga ``crawler.yaml``.
        """
        if config is None:
            from geass.core.config import load_crawler_config

            config = load_crawler_config()
        states = store.list_states()
        elements = [element for state in states for element in store.list_elements(state.id)]
        return cls(
            store.list_apps(),
            states,
            elements,
            store.list_transitions(),
            fuzzy_match_threshold=config.graph.fuzzy_match_threshold,
            coordinate_click_cost=config.graph.coordinate_click_cost,
        )

    @property
    def graph(self) -> nx.MultiDiGraph:
        return self._graph

    # Lectura de los datos del grafo

    def apps(self) -> list[AppRecord]:
        """Apps del grafo, ordenadas por ejecutable."""
        return self._apps_sorted()

    def states_of(self, app: AppRecord) -> list[StateRecord]:
        """Estados de una app, ordenados por id."""
        return [state for state in self._states.values() if state.app_id == app.id]

    def elements_of(self, state_id: int) -> list[ElementRecord]:
        """Elementos leídos de un estado, ordenados por id. Lista vacía si el estado no existe."""
        return list(self._elements_by_state.get(state_id, []))

    def transitions_of(self, app: AppRecord) -> list[TransitionRecord]:
        """Transiciones con origen en un estado de la app, ordenadas por id."""
        state_ids = {state.id for state in self.states_of(app)}
        return [t for t in self._transitions if t.source_state_id in state_ids]

    def element(self, element_id: int) -> ElementRecord | None:
        """Elemento por id, o ``None`` si no está entre los leídos."""
        return self._elements.get(element_id)

    def state(self, state_id: int) -> StateRecord | None:
        """Estado por id, o ``None`` si no está en el grafo."""
        return self._states.get(state_id)

    def is_route_edge(self, transition: TransitionRecord) -> bool:
        """Verdadero si la transición es ejecutable y su elemento está entre los leídos."""
        return self._executable(transition)

    # Apps

    def resolve_app(self, app: str) -> AppRecord:
        """App que corresponde a un texto: por ejecutable o nombre visible, exacto o difuso."""
        wanted = normalize_text(app)
        exact = [record for record in self._apps_sorted() if wanted in _app_names(record)]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise AmbiguousAppError(
                f"'{app}' coincide con varias apps: {exact[0].executable} y {exact[1].executable}."
            )

        scored = [
            (max(fuzz.WRatio(wanted, name) for name in _app_names(record)), record)
            for record in self._apps_sorted()
        ]
        candidates = sorted(
            ((score, record) for score, record in scored if score >= self._threshold),
            key=lambda item: -item[0],
        )
        if not candidates:
            known = ", ".join(record.executable for record in self._apps_sorted()) or "ninguna"
            raise AppNotFoundError(
                f"Ninguna app coincide con '{app}'. Ejecutables conocidos: {known}."
            )
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            raise AmbiguousAppError(
                f"'{app}' coincide por igual con {candidates[0][1].executable} y "
                f"{candidates[1][1].executable}."
            )
        return candidates[0][1]

    # Elementos

    def find_element(
        self, app: str, text: str, *, limit: int = 10, min_score: float | None = None
    ) -> list[ElementMatch]:
        """Elementos de una app cuyo nombre se parece al texto, de mejor a peor."""
        if limit < 1:
            raise ValueError("limit debe ser 1 o mayor.")
        matches = self._matches(app, text, min_score)
        return matches[:limit]

    def _matches(self, app: str, text: str, min_score: float | None) -> list[ElementMatch]:
        wanted = normalize_text(text)
        if not wanted:
            raise ValueError("El texto a buscar está vacío.")
        record = self.resolve_app(app)
        threshold = self._threshold if min_score is None else float(min_score)
        found: list[ElementMatch] = []
        for element in self._elements.values():
            state = self._states.get(element.state_id)
            if state is None or state.app_id != record.id:
                continue
            name = normalize_text(element.name)
            if not name:
                continue
            score = float(fuzz.WRatio(wanted, name))
            if score >= threshold:
                found.append(_match(element, state, score))
        found.sort(
            key=lambda match: (
                -match.score,
                -self._states[match.state_id].visits,
                match.element.id,
            )
        )
        return found

    # Rutas

    def path_to(self, app: str, current_state: int, target: str | int) -> Route:
        """Ruta de coste mínimo desde un estado hasta un elemento. Solo la describe."""
        record = self.resolve_app(app)
        start = self._states.get(current_state)
        if start is None or start.app_id != record.id:
            raise StateNotFoundError(
                f"El estado {current_state} no existe o no es de {record.executable}."
            )

        if isinstance(target, int):
            element = self._elements.get(target)
            state = self._states.get(element.state_id) if element is not None else None
            if element is None or state is None or state.app_id != record.id:
                raise TargetNotFoundError(
                    f"El elemento {target} no existe o no es de {record.executable}."
                )
            candidates = [_match(element, state, _EXACT_SCORE)]
        else:
            matches = self._matches(app, target, None)
            if not matches:
                raise TargetNotFoundError(
                    f"Ningún elemento de {record.executable} coincide con '{target}'."
                )
            best = matches[0].score
            candidates = [match for match in matches if match.score == best]

        costs, paths = nx.single_source_dijkstra(self._routes, current_state, weight="cost")
        reachable = [match for match in candidates if match.state_id in costs]
        if not reachable:
            raise NoRouteError(
                f"No hay ruta ejecutable desde el estado {current_state} hasta el objetivo: "
                f"{len(candidates)} candidato(s), ninguno alcanzable."
            )
        chosen = min(
            reachable,
            key=lambda match: (
                costs[match.state_id],
                -self._states[match.state_id].visits,
                match.element.id,
            ),
        )
        nodes = paths[chosen.state_id]
        steps: list[RouteStep] = []
        for source, destination in zip(nodes, nodes[1:], strict=False):
            edge = self._routes.edges[source, destination]
            transition_element = self._elements[edge["element_id"]]
            steps.append(
                RouteStep(
                    state_id=source,
                    element=transition_element,
                    action_type=edge["action_type"],
                    expected_state_id=destination,
                    transition_id=edge["transition_id"],
                    description=describe_action(edge["action_type"], transition_element),
                )
            )
        final_action = infer_action("left", tuple(chosen.element.patterns))
        steps.append(
            RouteStep(
                state_id=chosen.state_id,
                element=chosen.element,
                action_type=final_action,
                expected_state_id=None,
                transition_id=None,
                description=describe_action(final_action, chosen.element),
            )
        )
        return Route(
            app=record,
            start_state_id=current_state,
            target=chosen,
            steps=steps,
            cost=float(costs[chosen.state_id]),
        )

    # Cobertura

    def coverage(self, app: str | None = None) -> list[AppCoverage]:
        """Estadísticas de cobertura por app, ordenadas por ejecutable."""
        records = self._apps_sorted() if app is None else [self.resolve_app(app)]
        return [self._app_coverage(record) for record in records]

    def _app_coverage(self, record: AppRecord) -> AppCoverage:
        states = [state for state in self._states.values() if state.app_id == record.id]
        state_ids = {state.id for state in states}
        elements = [element for element in self._elements.values() if element.state_id in state_ids]
        actionable = {
            element.id
            for element in elements
            if element.enabled and any(pattern in ACTION_PATTERNS for pattern in element.patterns)
        }
        transitions = [t for t in self._transitions if t.source_state_id in state_ids]
        used_elements = {self._element_id(t) for t in transitions}
        exercised = len(actionable & used_elements)

        touched: set[int] = set()
        with_exit: set[int] = set()
        for transition in self._transitions:
            touched.add(transition.source_state_id)
            touched.add(transition.target_state_id)
            if self._executable(transition):
                with_exit.add(transition.source_state_id)

        return AppCoverage(
            app=record,
            states=len(states),
            visits=sum(state.visits for state in states),
            elements=len(elements),
            actionable_elements=len(actionable),
            exercised_elements=exercised,
            element_coverage=exercised / len(actionable) if actionable else 0.0,
            transitions=len(transitions),
            observations=sum(t.count for t in transitions),
            passive_transitions=sum(1 for t in transitions if t.source == "passive"),
            active_transitions=sum(1 for t in transitions if t.source == "active"),
            executable_transitions=sum(1 for t in transitions if self._executable(t)),
            transitions_without_element=sum(1 for t in transitions if self._element_id(t) is None),
            isolated_states=len(state_ids - touched),
            dead_end_states=len(state_ids - with_exit),
        )

    # Internos

    def _apps_sorted(self) -> list[AppRecord]:
        return sorted(self._apps.values(), key=lambda record: (record.executable, record.id))

    def _element_id(self, transition: TransitionRecord) -> int | None:
        """Elemento de la transición, o ``None`` si no tiene o ya no está entre los leídos."""
        if transition.element_id is None or transition.element_id not in self._elements:
            return None
        return transition.element_id

    def _executable(self, transition: TransitionRecord) -> bool:
        return is_executable(transition) and self._element_id(transition) is not None

    def _cost(self, transition: TransitionRecord) -> float:
        if transition.action_type == "coordinate_click":
            return self._coordinate_click_cost
        return _DEFAULT_COST

    def _route_graph(self) -> nx.DiGraph:
        """Grafo simple para la búsqueda: una arista ejecutable por par de estados.

        Entre varias se queda la de menor coste; a igual coste, la de mayor conteo; después,
        la de menor id.
        """
        routes = nx.DiGraph()
        routes.add_nodes_from(self._graph.nodes)
        best: dict[tuple[int, int], tuple[tuple[float, int, int], dict[str, object]]] = {}
        for source, destination, data in self._graph.edges(data=True):
            if not data["executable"]:
                continue
            rank = (data["cost"], -data["count"], data["transition_id"])
            current = best.get((source, destination))
            if current is None or rank < current[0]:
                best[(source, destination)] = (rank, data)
        for (source, destination), (_, data) in best.items():
            routes.add_edge(source, destination, **data)
        return routes


def _app_names(record: AppRecord) -> set[str]:
    executable = normalize_text(record.executable)
    return {executable, executable.removesuffix(".exe"), normalize_text(record.display_name)}


def _match(element: ElementRecord, state: StateRecord, score: float) -> ElementMatch:
    return ElementMatch(
        element=element, state_id=state.id, state_title=state.title_pattern, score=score
    )


def element_label(element: ElementRecord) -> str:
    """Nombre del elemento para mostrar: su nombre, o [AutomationId], o [sin nombre]."""
    name = element.name.strip()
    if name:
        return name
    if element.automation_id.strip():
        return f"[{element.automation_id}]"
    return "[sin nombre]"


def describe_action(action_type: ActionType, element: ElementRecord) -> str:
    """Texto legible de una acción, por ejemplo: Desplegar "Archivo" (MenuItem)."""
    verb = _VERBS[action_type]
    # El nombre va entre comillas tal como está guardado; las otras dos formas, tal cual.
    label = f'"{element.name}"' if element.name.strip() else element_label(element)
    return f"{verb} {label} ({element.control_type})"
