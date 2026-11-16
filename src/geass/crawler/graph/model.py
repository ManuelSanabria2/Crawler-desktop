"""Errores y modelos de las consultas al grafo del crawler."""

from pydantic import BaseModel, ConfigDict

from geass.core.errors import GeassError
from geass.crawler.store.records import ActionType, AppRecord, ElementRecord


class GraphError(GeassError):
    """Problema al consultar el grafo del crawler."""


class AppNotFoundError(GraphError):
    """Ninguna app coincide con el texto."""


class AmbiguousAppError(GraphError):
    """Dos o más apps empatan como mejor coincidencia."""


class StateNotFoundError(GraphError):
    """El estado no existe o no es de esa app."""


class TargetNotFoundError(GraphError):
    """Ningún elemento coincide con el objetivo."""


class NoRouteError(GraphError):
    """Hay objetivo, pero no ruta ejecutable hasta él."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ElementMatch(_Frozen):
    """Elemento encontrado por una búsqueda, con el estado que lo contiene."""

    element: ElementRecord
    state_id: int
    state_title: str  # StateRecord.title_pattern
    score: float  # 0 a 100


class RouteStep(_Frozen):
    """Una acción de una ruta."""

    state_id: int  # estado en el que se hace la acción
    element: ElementRecord
    action_type: ActionType
    expected_state_id: int | None  # estado al que lleva; None en el paso final
    transition_id: int | None  # None en el paso final
    description: str  # texto legible en español


class Route(_Frozen):
    """Ruta desde un estado hasta un elemento objetivo. Solo la describe; no la ejecuta."""

    app: AppRecord
    start_state_id: int
    target: ElementMatch
    steps: list[RouteStep]
    cost: float  # suma de los costes de las transiciones; el paso final no suma

    def describe(self) -> list[str]:
        """Descripciones de los pasos, numeradas desde 1."""
        return [f"{number}. {step.description}" for number, step in enumerate(self.steps, 1)]


class AppCoverage(_Frozen):
    """Cuánto de una app está mapeado."""

    app: AppRecord
    states: int
    visits: int
    elements: int
    actionable_elements: int
    exercised_elements: int
    element_coverage: float
    transitions: int
    observations: int
    passive_transitions: int
    active_transitions: int
    executable_transitions: int
    transitions_without_element: int
    isolated_states: int
    dead_end_states: int
