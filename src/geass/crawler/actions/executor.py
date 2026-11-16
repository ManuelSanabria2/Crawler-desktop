"""Ejecutor de órdenes de Manuel sobre la interfaz (sección 6.9 de INSTRUCCIONES.md).

Resuelve cada orden en el orden de ``executor.resolution_order``: acción directa, elemento
en la ventana actual por su patrón de UI Automation, ruta del grafo, detector visual y clic
por coordenadas. Después de cada acción comprueba que pasó lo esperado; si no, reintenta
según ``executor.max_retries`` y luego reporta el fallo.

Reglas que no se negocian:

- Toda acción sobre la interfaz pasa antes por ``SafetyGate``. Solo el método privado
  ``_perform`` llama al escritorio para actuar, y exige una autorización de la puerta para
  ese mismo elemento y esa misma operación.
- Ante la duda no se hace nada: varios candidatos, una ventana que no se puede confirmar o
  una ventana en primer plano que cambia antes de actuar terminan sin actuar.
- El ejecutor no escribe texto, no pulsa teclas (salvo las multimedia de C07), no trae
  ventanas al frente y no cierra ventanas.
- Cada intento, hecho o rechazado, y cada orden quedan en la auditoría. Una simulación no
  se audita.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, cast, get_args

from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

from geass.core.paths import resolve_project_path
from geass.core.schemas.crawler import CrawlerConfig, ResolutionStep
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions import direct, launcher
from geass.crawler.actions.audit import AuditLog
from geass.crawler.actions.confirm import ConfirmationRequest, Confirmer
from geass.crawler.actions.errors import ActionDisabledError, ActionError
from geass.crawler.actions.gate import GateDecision, GateOp, SafetyGate
from geass.crawler.actions.index import AppIndex
from geass.crawler.actions.live import (
    Desktop,
    LiveElement,
    PatternOp,
    VisualDetector,
    VisualQuery,
    WindowRef,
    element_label,
)
from geass.crawler.actions.request import ActionRequest, _describe_element
from geass.crawler.actions.verify import (
    READ_ERRORS,
    Baseline,
    capture_baseline,
    expectation_met,
    process_handles,
    ui_fingerprint,
    wait_until,
)
from geass.crawler.graph.build import CrawlerGraph
from geass.crawler.graph.model import NoRouteError, Route, RouteStep
from geass.crawler.graph.text import normalize_text
from geass.crawler.passive.pointer import infer_action
from geass.crawler.signature.compute import compute_signature
from geass.crawler.signature.rules import SignatureRules
from geass.crawler.store.records import StateRecord
from geass.crawler.uia.schema import Rect

ExecutionStatus = Literal[
    "done",  # hecho y verificado
    "done_unverified",  # hecho; no había nada que verificar o la verificación está desactivada
    "would_do",  # simulación: se habría hecho
    "failed",  # se intentó (o se encontró el objetivo) y no salió
    "not_found",  # ninguna estrategia encontró el objetivo
    "ambiguous",  # varios candidatos: no se adivina
    "denied",  # requería confirmación y no se dio
    "forbidden",  # la puerta lo prohíbe, o la acción directa está desactivada
    "blocked",  # el lanzador rechazó la app por la lista de bloqueo (C07)
    "aborted",  # parada pedida, o no se pudo confirmar la ventana
]

_DIRECT_STEP: ResolutionStep = "direct_action"
_PATTERN_OPS: tuple[str, ...] = get_args(PatternOp)
# Patrón de UI Automation que necesita cada acción pedida.
_REQUIRED_PATTERN: dict[str, str] = {
    "invoke": "Invoke",
    "toggle": "Toggle",
    "expand": "ExpandCollapse",
    "collapse": "ExpandCollapse",
    "select": "SelectionItem",
}
_EXPLORER_APP = "explorer.exe"
_EXPLORER_CLASS = "CabinetWClass"
_MAX_LISTED = 5  # candidatos que se nombran en un mensaje de ambigüedad
_LEFT_BUTTON = "left"


class Attempt(BaseModel):
    """Un intento de acción, hecho o rechazado."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: ResolutionStep
    step: int  # 0: la acción final o la directa; 1 a n: paso intermedio de una ruta
    try_number: int  # 1 o 2
    description: str
    op: str | None
    gate: GateDecision | None
    confirmed: bool | None  # None si no hizo falta confirmar
    performed: bool  # se llamó a desktop.perform, desktop.click o a la acción directa
    verified: bool | None  # None si no se verificó
    result: (
        str  # done, done_unverified, failed, denied, forbidden: <regla>, aborted, would_do, error
    )


class ExecutionResult(BaseModel):
    """Lo que pasó con una orden."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: ActionRequest
    status: ExecutionStatus
    strategy: ResolutionStep | None  # la que resolvió o intentó la acción final
    message: str
    attempts: list[Attempt]

    @property
    def performed_count(self) -> int:
        """Intentos en los que se actuó."""
        return sum(1 for attempt in self.attempts if attempt.performed)


# Internos

_CLEARANCE_TOKEN = object()


@dataclass(frozen=True)
class _Target:
    """Elemento vivo y operación sobre la que se consulta la puerta y se actúa."""

    element: LiveElement
    op: GateOp
    point: tuple[int, int] | None = None  # solo con coordinate_click


@dataclass(frozen=True)
class _Clearance:
    """Autorización de la puerta para un elemento y una operación. Solo la crea ``_clear``."""

    token: object = field(repr=False)
    process_id: int
    window_handle: int
    props: object
    op: str
    point: tuple[int, int] | None

    def __post_init__(self) -> None:
        if self.token is not _CLEARANCE_TOKEN:
            raise RuntimeError("Una autorización solo la crea la consulta a la puerta.")

    def covers(self, target: _Target) -> bool:
        element = target.element
        return (
            self.process_id == element.process_id
            and self.window_handle == element.window_handle
            and self.props == element.props
            and self.op == target.op
            and self.point == target.point
        )


@dataclass(frozen=True)
class _Outcome:
    status: ExecutionStatus
    message: str
    strategy: ResolutionStep | None


@dataclass(frozen=True)
class _AttemptResult:
    outcome: _Outcome
    retry: bool = False  # la verificación falló y la acción se puede repetir


@dataclass
class _Run:
    """Estado de una llamada a ``run``."""

    request: ActionRequest
    attempts: list[Attempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # por qué no aplicó cada estrategia
    failure: str | None = None  # se encontró el objetivo, pero no se pudo actuar
    failure_strategy: ResolutionStep | None = None
    found: bool = False  # la última búsqueda en la ventana encontró algo
    saved: LiveElement | None = None  # elemento guardado para coordinate_click
    saved_window: WindowRef | None = None

    def fail(self, message: str, strategy: ResolutionStep) -> None:
        self.failure = message
        self.failure_strategy = strategy


Verifier = Callable[[Baseline, _Target], bool]
Locate = Callable[[], "_Target | str"]


def _same_executable(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _forbidden_message(decision: GateDecision) -> str:
    return f"{decision.reason} (regla {decision.rule})"


def _center(rect: Rect) -> tuple[int, int]:
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def _inside(rect: Rect, x: int, y: int) -> bool:
    return rect.left <= x < rect.right and rect.top <= y < rect.bottom


class Executor:
    """Ejecuta órdenes de Manuel, una a una, pasando cada acción por la puerta de seguridad."""

    def __init__(
        self,
        *,
        authorized_by: str,
        confirmer: Confirmer,
        desktop: Desktop,
        config: CrawlerConfig,
        safety: SafetyConfig,
        audit: AuditLog,
        graph: CrawlerGraph | None = None,
        index: AppIndex | None = None,
        detector: VisualDetector | None = None,
        dry_run: bool = False,
        should_stop: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        Raises:
            ValueError: ``authorized_by`` está vacío, o safety.yaml tiene una entrada de
                ``never_automate`` desconocida.
        """
        if not authorized_by.strip():
            raise ValueError("Toda acción necesita quién la autorizó: authorized_by está vacío.")
        self._authorized_by = authorized_by
        self._confirmer = confirmer
        self._desktop = desktop
        self._config = config
        self._safety = safety
        self._audit = audit
        self._graph = graph
        self._index = index
        self._detector = detector
        self._dry_run = dry_run
        self._should_stop = should_stop
        self._clock = clock
        self._sleep = sleep
        self._gate = SafetyGate(safety, config)
        self._rules = SignatureRules.from_config(config.signature)
        self._excluded = tuple(config.apps.excluded)
        self._excluded_keys = frozenset(name.strip().casefold() for name in self._excluded)
        self._run_request: ActionRequest | None = None

    @classmethod
    def from_config(
        cls,
        *,
        authorized_by: str,
        confirmer: Confirmer,
        dry_run: bool = False,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> "Executor":
        """Ejecutor sobre el escritorio real, con crawler.yaml, safety.yaml y la base si existe.

        La base solo se abre si su archivo ya existe; no se crea. Sin detector visual.
        """
        from geass.core.config import load_crawler_config, load_safety_config
        from geass.crawler.actions.desktop_win import WinDesktop
        from geass.crawler.store.db import CrawlerStore

        config = load_crawler_config()
        safety = load_safety_config()
        desktop = WinDesktop(config)
        audit = AuditLog.from_config(safety)
        index = AppIndex.load(config, safety)
        graph: CrawlerGraph | None = None
        if resolve_project_path(config.store.db_path).is_file():
            with CrawlerStore.from_config(config) as store:
                graph = CrawlerGraph.from_store(store, config)
        return cls(
            authorized_by=authorized_by,
            confirmer=confirmer,
            desktop=desktop,
            config=config,
            safety=safety,
            audit=audit,
            graph=graph,
            index=index,
            dry_run=dry_run,
            should_stop=should_stop,
        )

    # Entrada

    def run(self, request: ActionRequest) -> ExecutionResult:
        """Ejecuta una orden y devuelve qué pasó.

        Raises:
            ConfigIncompleteError: hace falta un dato que sigue como TODO (por ejemplo,
                ``authorization.allowed_windows``). Se audita antes de dejarlo pasar.
        """
        run = _Run(request)
        # La orden en curso: authorize y expect solo valen para ella.
        self._run_request = request
        try:
            outcome = self._resolve(run)
        except BaseException as error:
            strategy = run.attempts[-1].strategy if run.attempts else None
            self._audit_final(run, f"error: {error}", strategy, str(error))
            raise
        self._audit_final(run, outcome.status, outcome.strategy, outcome.message)
        return ExecutionResult(
            request=request,
            status=outcome.status,
            strategy=outcome.strategy,
            message=outcome.message,
            attempts=list(run.attempts),
        )

    def _resolve(self, run: _Run) -> _Outcome:
        if self._should_stop():
            return _Outcome("aborted", "Parada pedida.", None)
        kind = run.request.kind
        if kind == "element":
            return self._element(run)
        if _DIRECT_STEP not in self._config.executor.resolution_order:
            return _Outcome(
                "forbidden", "direct_action no está en executor.resolution_order.", None
            )
        if kind == "launch":
            return self._launch(run)
        if kind == "explorer":
            return self._explorer(run)
        return self._media(run)

    # Órdenes sobre elementos

    def _element(self, run: _Run) -> _Outcome:
        request = run.request
        decision = self._gate.check_app(request.app)
        if decision is not None:
            return _Outcome("forbidden", _forbidden_message(decision), None)
        window, outcome = self._confirm_window(request, None)
        if outcome is not None or window is None:
            return outcome or _Outcome("forbidden", "No hay ventana en primer plano.", None)
        strategies: dict[str, Callable[[_Run, WindowRef], _Outcome | None]] = {
            "current_window_element": self._current_window_element,
            "graph_route": self._graph_route,
            "visual_detector": self._visual_detector,
            "coordinate_click": self._coordinate_click,
        }
        for step in self._config.executor.resolution_order:
            strategy = strategies.get(step)
            if strategy is None:
                continue
            outcome = strategy(run, window)
            if outcome is not None:
                return outcome
        if run.failure is not None:
            return _Outcome("failed", run.failure, run.failure_strategy)
        detail = "; ".join(run.notes) if run.notes else "ninguna estrategia lo encontró"
        return _Outcome("not_found", f"No se encontró el objetivo: {detail}.", None)

    def _confirm_window(
        self, request: ActionRequest, strategy: ResolutionStep | None
    ) -> tuple[WindowRef | None, _Outcome | None]:
        """Ventana en primer plano, si es de la app de la orden y la puerta la admite."""
        window = self._desktop.foreground()
        decision = self._gate.check_window(window)
        if decision is not None or window is None:
            reason = (
                _forbidden_message(decision)
                if decision is not None
                else "No hay ventana en primer plano."
            )
            return None, _Outcome("forbidden", reason, strategy)
        if not _same_executable(window.process_name, request.app):
            return None, _Outcome(
                "aborted",
                f"La ventana en primer plano no es de {request.app}: no actúo sobre ventanas "
                "que no puedo confirmar.",
                strategy,
            )
        return window, None

    def _find_request(self, window: WindowRef) -> list[LiveElement]:
        request = self._current_request()
        return self._desktop.find(
            window,
            name=request.name,
            automation_id=request.automation_id,
            control_type=request.control_type,
        )

    @staticmethod
    def _prefer(candidates: list[LiveElement]) -> list[LiveElement]:
        """Los habilitados; si hay varios, los que están en pantalla (si alguno lo está)."""
        enabled = [element for element in candidates if element.props.enabled]
        if len(enabled) > 1:
            visible = [element for element in enabled if not element.props.offscreen]
            if visible:
                enabled = visible
        return enabled

    def _current_window_element(
        self,
        run: _Run,
        window: WindowRef,
        strategy: ResolutionStep = "current_window_element",
    ) -> _Outcome | None:
        run.found = False
        try:
            candidates = self._find_request(window)
        except READ_ERRORS as error:
            run.notes.append(f"{strategy}: no se pudo leer la ventana ({error})")
            return None
        chosen = self._prefer(candidates)
        if len(chosen) > 1:
            listed = ", ".join(
                f"{element_label(element.props)} ({element.props.control_type})"
                for element in chosen[:_MAX_LISTED]
            )
            return _Outcome(
                "ambiguous",
                f"Hay {len(chosen)} elementos que coinciden con la orden y no adivino cuál: "
                f"{listed}.",
                strategy,
            )
        if not chosen:
            if candidates:
                run.found = True
                label = element_label(candidates[0].props)
                run.fail(f'"{label}": se encontró, pero está deshabilitado.', strategy)
            else:
                run.notes.append(f"{strategy}: no está en la ventana")
            return None

        element = chosen[0]
        run.found = True
        label = element_label(element.props)
        op = self._operation(run, window, element, strategy)
        if op is None:
            return None
        target = _Target(element=element, op=op)

        def relocate() -> _Target | str:
            try:
                again = self._prefer(self._find_request(window))
            except READ_ERRORS as error:
                return f"no se pudo leer la ventana ({error})"
            if len(again) != 1:
                return f'"{label}" ya no está una sola vez en la ventana'
            return _Target(element=again[0], op=op)

        return self._act(
            run,
            strategy=strategy,
            step=0,
            window=window,
            target=target,
            relocate=relocate,
            is_target=True,
            verify=self._final_verifier(window),
        )

    def _operation(
        self, run: _Run, window: WindowRef, element: LiveElement, strategy: ResolutionStep
    ) -> GateOp | None:
        """Operación sobre el elemento, o ``None`` si queda para el clic por coordenadas."""
        action = run.request.action
        label = element_label(element.props)
        if action == "coordinate_click":
            self._save(run, element, window)
            run.fail(f'"{label}": se encontró; la orden pide un clic por coordenadas.', strategy)
            return None
        if action is not None:
            if _REQUIRED_PATTERN[action] not in element.props.patterns:
                self._save(run, element, window)
                run.fail(f'"{label}": se encontró, pero no admite {action}.', strategy)
                return None
            return action
        inferred = infer_action(_LEFT_BUTTON, element.props.patterns)
        if inferred == "expand_collapse":
            return self._expand_or_collapse(element)
        if inferred in ("invoke", "toggle", "select"):
            return cast(GateOp, inferred)
        self._save(run, element, window)
        run.fail(
            f'"{label}": se encontró, pero no tiene patrones de acción de UI Automation.', strategy
        )
        return None

    def _expand_or_collapse(self, element: LiveElement) -> GateOp:
        try:
            expanded = self._desktop.state(element).expand == "expanded"
        except READ_ERRORS:
            expanded = False
        return "collapse" if expanded else "expand"

    @staticmethod
    def _save(run: _Run, element: LiveElement, window: WindowRef) -> None:
        run.saved = element
        run.saved_window = window

    # Ruta del grafo

    def _graph_route(self, run: _Run, window: WindowRef) -> _Outcome | None:
        strategy: ResolutionStep = "graph_route"
        request = run.request
        graph = self._graph
        if graph is None:
            run.notes.append("graph_route: no hay grafo del crawler")
            return None
        app = next(
            (record for record in graph.apps() if _same_executable(record.executable, request.app)),
            None,
        )
        if app is None:
            run.notes.append(f"graph_route: el grafo no tiene {request.app}")
            return None
        try:
            signature = compute_signature(self._desktop.snapshot(window), self._rules).structural
        except READ_ERRORS as error:
            run.notes.append(f"graph_route: no se pudo leer la ventana ({error})")
            return None
        states = graph.states_of(app)
        current = next((state for state in states if state.structural_signature == signature), None)
        if current is None:
            run.notes.append("graph_route: no conozco el estado actual")
            return None

        wanted_name = normalize_text(request.name)
        targets = [
            element
            for state in states
            for element in graph.elements_of(state.id)
            if (not request.automation_id or element.automation_id == request.automation_id)
            and (not request.name or normalize_text(element.name) == wanted_name)
            and (not request.control_type or element.control_type == request.control_type)
        ]
        if not targets:
            run.notes.append("graph_route: el objetivo no está en el grafo")
            return None
        routes: list[Route] = []
        for element in targets:
            try:
                routes.append(graph.path_to(app.executable, current.id, element.id))
            except NoRouteError:
                continue
        if not routes:
            run.notes.append("graph_route: no hay ruta ejecutable hasta el objetivo")
            return None
        route = min(routes, key=lambda item: (item.cost, item.target.element.id))
        if len(route.steps) == 1:
            run.notes.append(
                "graph_route: el objetivo está en la vista actual, pero no se encontró en la "
                "ventana"
            )
            return None

        for number, step in enumerate(route.steps[:-1], start=1):
            outcome = self._route_step(run, number, step)
            if outcome.status != "done":
                return outcome

        final_window, outcome = self._confirm_window(request, strategy)
        if outcome is not None or final_window is None:
            return outcome or _Outcome("forbidden", "No hay ventana en primer plano.", strategy)
        outcome = self._current_window_element(run, final_window, strategy)
        if outcome is not None or run.found:
            # Si se encontró pero no se pudo actuar, sigue la resolución (clic por coordenadas).
            return outcome
        return _Outcome(
            "failed",
            "Ruta del grafo: la ruta llegó, pero el objetivo no está en la ventana.",
            strategy,
        )

    def _route_step(self, run: _Run, number: int, step: RouteStep) -> _Outcome:
        strategy: ResolutionStep = "graph_route"
        request = run.request
        broken = f"Ruta del grafo: la ruta se rompió en el paso {number}"
        window, outcome = self._confirm_window(request, strategy)
        if outcome is not None or window is None:
            return outcome or _Outcome("forbidden", "No hay ventana en primer plano.", strategy)
        expected = (
            self._graph.state(step.expected_state_id)
            if self._graph is not None and step.expected_state_id is not None
            else None
        )
        if expected is None:
            return _Outcome(
                "failed", f"{broken}: el estado esperado no está en el grafo.", strategy
            )
        record = step.element

        def locate() -> _Target | str:
            if not record.automation_id and not record.name:
                return "el elemento del paso no tiene nombre ni AutomationId"
            try:
                if record.automation_id:
                    found = self._desktop.find(
                        window,
                        automation_id=record.automation_id,
                        control_type=record.control_type,
                    )
                else:
                    found = self._desktop.find(
                        window, name=record.name, control_type=record.control_type
                    )
            except READ_ERRORS as error:
                return f"no se pudo leer la ventana ({error})"
            enabled = [element for element in found if element.props.enabled]
            if len(enabled) != 1:
                return (
                    f'se esperaba un solo "{record.name or record.automation_id}" habilitado y '
                    f"hay {len(enabled)}"
                )
            element = enabled[0]
            action = step.action_type
            if action in ("invoke", "toggle", "select"):
                return _Target(element=element, op=cast(GateOp, action))
            if action == "expand_collapse":
                return _Target(element=element, op=self._expand_or_collapse(element))
            if action == "coordinate_click":
                if "coordinate_click" not in self._config.executor.resolution_order:
                    return (
                        "el paso es un clic por coordenadas y coordinate_click no está en "
                        "executor.resolution_order"
                    )
                return self._coordinate_target(element, window)
            return f"la acción {action} no se puede repetir"

        target = locate()
        if isinstance(target, str):
            return _Outcome("failed", f"{broken}: {target}.", strategy)
        outcome = self._act(
            run,
            strategy=strategy,
            step=number,
            window=window,
            target=target,
            relocate=locate,
            is_target=False,
            verify=lambda baseline, target: self._reached(expected, request.app),
        )
        if outcome.status == "failed":
            return _Outcome("failed", f"{broken}: {outcome.message}", strategy)
        return outcome

    def _reached(self, expected: StateRecord, app: str) -> bool:
        """La ventana en primer plano es de la app y su firma es la del estado esperado."""
        current = self._desktop.foreground()
        if current is None or not _same_executable(current.process_name, app):
            return False
        dump = self._desktop.snapshot(current)
        return compute_signature(dump, self._rules).structural == expected.structural_signature

    # Detector visual

    def _visual_detector(self, run: _Run, window: WindowRef) -> _Outcome | None:
        strategy: ResolutionStep = "visual_detector"
        request = run.request
        detector = self._detector
        if detector is None:
            run.notes.append("visual_detector: no hay detector visual")
            return None
        threshold = self._config.vision.score_threshold
        query = VisualQuery(
            name=request.name,
            automation_id=request.automation_id,
            control_type=request.control_type,
        )

        def locate() -> _Target | str:
            good = [item for item in detector.locate(window, query) if item.score >= threshold]
            if not good:
                return "ningún candidato del detector llega a vision.score_threshold"
            best = max(good, key=lambda item: item.score)
            x, y = _center(best.rect)
            try:
                element = self._desktop.element_at(x, y)
            except READ_ERRORS:
                element = None
            if (
                element is None
                or element.process_id != window.process_id
                or (
                    request.name
                    and normalize_text(element.props.name) != normalize_text(request.name)
                )
                or (request.automation_id and element.props.automation_id != request.automation_id)
            ):
                return "el detector no coincide con lo que hay en pantalla"
            if window.rect is not None and not _inside(window.rect, x, y):
                return "el candidato del detector queda fuera de la ventana"
            return _Target(element=element, op="coordinate_click", point=(x, y))

        target = locate()
        if isinstance(target, str):
            run.notes.append(f"visual_detector: {target}")
            return None
        return self._act(
            run,
            strategy=strategy,
            step=0,
            window=window,
            target=target,
            relocate=locate,
            is_target=True,
            verify=self._final_verifier(window),
        )

    # Clic por coordenadas

    def _coordinate_click(self, run: _Run, window: WindowRef) -> _Outcome | None:
        strategy: ResolutionStep = "coordinate_click"
        saved, saved_window = run.saved, run.saved_window
        if saved is None or saved_window is None:
            run.notes.append("coordinate_click: no hay ningún elemento vivo guardado")
            return None
        label = element_label(saved.props)
        target = self._coordinate_target(saved, saved_window)
        if isinstance(target, str):
            run.fail(f'"{label}": {target}.', strategy)
            return None

        def relocate() -> _Target | str:
            try:
                again = self._prefer(self._find_request(saved_window))
            except READ_ERRORS as error:
                return f"no se pudo leer la ventana ({error})"
            if len(again) != 1:
                return f'"{label}" ya no está una sola vez en la ventana'
            return self._coordinate_target(again[0], saved_window)

        return self._act(
            run,
            strategy=strategy,
            step=0,
            window=saved_window,
            target=target,
            relocate=relocate,
            is_target=True,
            verify=self._final_verifier(saved_window),
        )

    def _coordinate_target(self, element: LiveElement, window: WindowRef) -> _Target | str:
        """Centro del elemento, si se puede comprobar que es él lo que hay en ese punto."""
        props = element.props
        if props.rect is None:
            return "no tiene rectángulo en pantalla"
        if props.offscreen:
            return "está fuera de pantalla"
        if not props.enabled:
            return "está deshabilitado"
        x, y = _center(props.rect)
        if window.rect is not None and not _inside(window.rect, x, y):
            return "su centro queda fuera de la ventana"
        try:
            under = self._desktop.element_at(x, y)
        except READ_ERRORS:
            under = None
        if (
            under is None
            or under.process_id != element.process_id
            or under.props.control_type != props.control_type
            or under.props.name != props.name
            or under.props.automation_id != props.automation_id
        ):
            return "otro elemento tapa el objetivo"
        return _Target(element=element, op="coordinate_click", point=(x, y))

    # Un intento: puerta, acción y verificación

    def _act(
        self,
        run: _Run,
        *,
        strategy: ResolutionStep,
        step: int,
        window: WindowRef,
        target: _Target,
        relocate: Locate,
        is_target: bool,
        verify: Verifier,
    ) -> _Outcome:
        """Intenta la acción y la repite si la verificación falla, según las reglas."""
        tries = 1 + self._config.executor.max_retries
        try_number = 1
        while True:
            result = self._attempt(
                run,
                strategy=strategy,
                step=step,
                try_number=try_number,
                window=window,
                target=target,
                is_target=is_target,
                verify=verify,
            )
            if not result.retry or try_number >= tries:
                return result.outcome
            found = relocate()
            if isinstance(found, str):
                return _Outcome("failed", f"No se pudo repetir la acción: {found}.", strategy)
            target = found
            try_number += 1

    def _attempt(
        self,
        run: _Run,
        *,
        strategy: ResolutionStep,
        step: int,
        try_number: int,
        window: WindowRef,
        target: _Target,
        is_target: bool,
        verify: Verifier,
    ) -> _AttemptResult:
        props = target.element.props
        description = _describe_element(
            target.op, props.name, props.automation_id, props.control_type, window.process_name
        )

        def note(
            result: str,
            *,
            decision: GateDecision | None = None,
            confirmed: bool | None = None,
            performed: bool = False,
            verified: bool | None = None,
        ) -> None:
            attempt = Attempt(
                strategy=strategy,
                step=step,
                try_number=try_number,
                description=description,
                op=target.op,
                gate=decision,
                confirmed=confirmed,
                performed=performed,
                verified=verified,
                result=result,
            )
            run.attempts.append(attempt)
            self._audit_attempt(attempt, target, window)

        def outcome(status: ExecutionStatus, message: str, retry: bool = False) -> _AttemptResult:
            return _AttemptResult(_Outcome(status, message, strategy), retry=retry)

        if self._should_stop():
            note("aborted")
            return outcome("aborted", "Parada pedida.")

        clearance, decision, confirmed = self._clear(target, window, is_target, description)
        if decision.verdict == "forbidden":
            note(f"forbidden: {decision.rule}", decision=decision)
            return outcome("forbidden", f"{description}: {_forbidden_message(decision)}")
        if self._dry_run:
            note("would_do", decision=decision)
            verdict = "libre" if decision.verdict == "free" else "requeriría confirmación"
            return outcome("would_do", f"{description}: se haría ({verdict}).")
        if clearance is None:
            note("denied", decision=decision, confirmed=confirmed)
            return outcome("denied", f"{description}: no se confirmó, así que no se hizo.")

        baseline = capture_baseline(self._desktop, window, target.element)
        current = self._desktop.foreground()
        if current is None or current.handle != window.handle:
            note("aborted", decision=decision, confirmed=confirmed)
            return outcome(
                "aborted", "La ventana en primer plano cambió antes de actuar: no actúo."
            )

        try:
            self._perform(clearance, target)
        except ActionError as error:
            note(f"error: {error}", decision=decision, confirmed=confirmed, performed=True)
            return outcome("failed", f"{description}: {error}")

        if not self._config.executor.verify_transition:
            note("done_unverified", decision=decision, confirmed=confirmed, performed=True)
            return outcome("done_unverified", f"{description}: hecho, sin verificar.")
        verified = wait_until(
            lambda: verify(baseline, target),
            timeout_s=self._config.executor.verify_timeout_s,
            poll_s=self._config.executor.verify_poll_s,
            clock=self._clock,
            sleep=self._sleep,
        )
        if verified:
            note("done", decision=decision, confirmed=confirmed, performed=True, verified=True)
            return outcome("done", f"{description}: hecho y verificado.")
        note("failed", decision=decision, confirmed=confirmed, performed=True, verified=False)
        # Lo que necesitó confirmación no se repite nunca.
        return outcome(
            "failed",
            f"{description}: se hizo, pero no se vio el resultado esperado.",
            retry=decision.verdict == "free",
        )

    def _clear(
        self, target: _Target, window: WindowRef, is_target: bool, description: str
    ) -> tuple[_Clearance | None, GateDecision, bool | None]:
        """Consulta la puerta y, si hace falta, al confirmador. Es lo único que autoriza.

        Devuelve la autorización (o ``None``), la decisión y si se confirmó (``None`` si no
        hizo falta preguntar). En simulación nunca pregunta ni autoriza una confirmación.
        """
        element = target.element
        decision = self._gate.check(
            props=element.props,
            op=target.op,
            window=window,
            is_target=is_target,
            authorize=self._run_authorize(is_target),
        )
        if decision.verdict == "forbidden":
            return None, decision, None
        confirmed: bool | None = None
        if decision.verdict == "confirm":
            if self._dry_run:
                return None, decision, None
            props = element.props
            confirmed = bool(
                self._confirmer(
                    ConfirmationRequest(
                        description=description,
                        app=window.process_name,
                        element=element_label(props),
                        control_type=props.control_type,
                        rule=decision.rule,
                        pattern=decision.pattern,
                        reason=decision.reason,
                        is_target=is_target,
                    )
                )
            )
            if not confirmed:
                return None, decision, False
        clearance = _Clearance(
            token=_CLEARANCE_TOKEN,
            process_id=element.process_id,
            window_handle=element.window_handle,
            props=element.props,
            op=target.op,
            point=target.point,
        )
        return clearance, decision, confirmed

    def _run_authorize(self, is_target: bool) -> bool:
        # authorize solo vale para el objetivo de la orden; la puerta lo vuelve a comprobar.
        return is_target and self._current_request().authorize

    def _perform(self, clearance: _Clearance, target: _Target) -> None:
        """El único sitio que actúa sobre la interfaz. Exige la autorización de la puerta."""
        if not isinstance(clearance, _Clearance) or not clearance.covers(target):
            raise RuntimeError("La autorización de la puerta no corresponde a esta acción.")
        if target.op == "coordinate_click":
            if target.point is None:
                raise RuntimeError("Un clic por coordenadas necesita su punto.")
            x, y = target.point
            self._desktop.click(x, y)
            return
        if target.op not in _PATTERN_OPS:
            raise RuntimeError(f"Operación que el ejecutor no hace: {target.op}.")
        self._desktop.perform(target.element, cast(PatternOp, target.op))

    # Verificación

    def _final_verifier(self, window: WindowRef) -> Verifier:
        expectation = self._current_request().expect

        def verify(baseline: Baseline, target: _Target) -> bool:
            if expectation is not None:
                return expectation_met(
                    self._desktop, expectation, baseline, excluded=self._excluded
                )
            return self._default_verified(window, baseline, target)

        return verify

    def _default_verified(self, window: WindowRef, baseline: Baseline, target: _Target) -> bool:
        op = target.op
        if op == "toggle":
            before = baseline.state.toggle if baseline.state is not None else None
            if before is None:
                return False
            now = self._desktop.state(target.element).toggle
            return now is not None and now != before
        if op == "expand":
            return self._desktop.state(target.element).expand == "expanded"
        if op == "collapse":
            return self._desktop.state(target.element).expand == "collapsed"
        if op == "select":
            return self._desktop.state(target.element).selected is True
        return self._something_changed(window, baseline)

    def _something_changed(self, window: WindowRef, baseline: Baseline) -> bool:
        """Verificación de invoke y del clic: cambió algo en el escritorio o en la ventana."""
        current = self._desktop.foreground()
        if (
            current is not None
            and baseline.foreground_handle is not None
            and current.handle != baseline.foreground_handle
        ):
            return True
        windows = self._desktop.windows()
        if process_handles(windows, window.process_id) != baseline.process_handles:
            return True
        if window.handle not in {item.handle for item in windows}:
            return True
        if baseline.fingerprint is None:
            return False
        return ui_fingerprint(self._desktop.snapshot(window)) != baseline.fingerprint

    # Acciones directas

    def _launch(self, run: _Run) -> _Outcome:
        request = run.request
        tries = 1 + self._config.executor.max_retries
        baseline = self._direct_baseline()
        for try_number in range(1, tries + 1):
            if self._should_stop():
                return _Outcome("aborted", "Parada pedida.", _DIRECT_STEP)
            try:
                result = launcher.launch(
                    request.app,
                    authorized_by=self._authorized_by,
                    index=self._index,
                    config=self._config,
                    safety=self._safety,
                    audit=self._audit,
                    dry_run=self._dry_run,
                )
            except ActionError as error:
                self._note_direct(run, try_number, "launch", f"error: {error}", performed=True)
                return _Outcome("failed", f"No se pudo abrir {request.app}: {error}", _DIRECT_STEP)
            if result.status in ("ambiguous", "not_found", "blocked"):
                self._note_direct(run, try_number, "launch", result.status)
                options = ", ".join(option.entry.name for option in result.options)
                message = {
                    "ambiguous": f'"{request.app}" coincide con varias apps: {options}.',
                    "not_found": f'Ninguna app coincide con "{request.app}". Parecidas: '
                    f"{options or 'ninguna'}.",
                    "blocked": f'"{request.app}" coincide con la lista de bloqueo: no se abre.',
                }[result.status]
                return _Outcome(cast(ExecutionStatus, result.status), message, _DIRECT_STEP)
            if self._dry_run:
                self._note_direct(run, try_number, "launch", "would_do")
                name = result.entry.name if result.entry is not None else request.app
                return _Outcome("would_do", f'Se abriría "{name}".', _DIRECT_STEP)

            name = result.entry.name if result.entry is not None else request.app
            if not self._config.executor.verify_transition:
                self._note_direct(run, try_number, "launch", "done_unverified", performed=True)
                return _Outcome(
                    "done_unverified", f'Se abrió "{name}", sin verificar.', _DIRECT_STEP
                )
            verified = wait_until(
                lambda name=name: self._launch_verified(request, name, baseline),
                timeout_s=self._config.executor.launch_timeout_s,
                poll_s=self._config.executor.verify_poll_s,
                clock=self._clock,
                sleep=self._sleep,
            )
            if verified:
                self._note_direct(run, try_number, "launch", "done", performed=True, verified=True)
                return _Outcome("done", f'Se abrió "{name}" y se vio su ventana.', _DIRECT_STEP)
            if request.expect is None and self._new_excluded_window(baseline):
                self._note_direct(run, try_number, "launch", "done_unverified", performed=True)
                return _Outcome(
                    "done_unverified",
                    f'Se abrió "{name}": apareció una ventana de una app excluida y su título '
                    "no se lee.",
                    _DIRECT_STEP,
                )
            self._note_direct(run, try_number, "launch", "failed", performed=True, verified=False)
        return _Outcome(
            "failed", f'Se lanzó "{request.app}", pero no se vio su ventana.', _DIRECT_STEP
        )

    def _launch_verified(self, request: ActionRequest, name: str, baseline: Baseline) -> bool:
        if request.expect is not None:
            return expectation_met(self._desktop, request.expect, baseline, excluded=self._excluded)
        wanted = normalize_text(name)
        candidates = [
            window
            for window in self._desktop.windows()
            if window.handle not in baseline.window_handles
        ]
        current = self._desktop.foreground()
        if current is not None:
            candidates.append(current)
        threshold = self._config.launcher.fuzzy_match_threshold
        return any(
            not self._is_excluded(window.process_name)
            and fuzz.partial_ratio(wanted, normalize_text(window.title)) >= threshold
            for window in candidates
        )

    def _new_excluded_window(self, baseline: Baseline) -> bool:
        try:
            windows = self._desktop.windows()
        except READ_ERRORS:
            return False
        return any(
            window.handle not in baseline.window_handles and self._is_excluded(window.process_name)
            for window in windows
        )

    def _explorer(self, run: _Run) -> _Outcome:
        request = run.request
        if "open_explorer" not in self._config.launcher.direct_actions:
            return _Outcome(
                "forbidden",
                "La acción directa open_explorer no está activada en launcher.direct_actions.",
                _DIRECT_STEP,
            )
        if self._dry_run:
            self._note_direct(run, 1, "open_explorer", "would_do")
            return _Outcome("would_do", f"Se haría: {request.describe()}.", _DIRECT_STEP)
        tries = 1 + self._config.executor.max_retries
        baseline = self._direct_baseline()
        for try_number in range(1, tries + 1):
            if self._should_stop():
                return _Outcome("aborted", "Parada pedida.", _DIRECT_STEP)
            try:
                direct.open_explorer(
                    request.path,
                    authorized_by=self._authorized_by,
                    config=self._config,
                    audit=self._audit,
                )
            except ActionDisabledError as error:
                self._note_direct(run, try_number, "open_explorer", "forbidden: disabled")
                return _Outcome("forbidden", str(error), _DIRECT_STEP)
            except ActionError as error:
                self._note_direct(
                    run, try_number, "open_explorer", f"error: {error}", performed=True
                )
                return _Outcome("failed", f"No se pudo abrir el explorador: {error}", _DIRECT_STEP)
            if not self._config.executor.verify_transition:
                self._note_direct(
                    run, try_number, "open_explorer", "done_unverified", performed=True
                )
                return _Outcome(
                    "done_unverified", "Se abrió el explorador, sin verificar.", _DIRECT_STEP
                )
            verified = wait_until(
                lambda: self._explorer_verified(request, baseline),
                timeout_s=self._config.executor.launch_timeout_s,
                poll_s=self._config.executor.verify_poll_s,
                clock=self._clock,
                sleep=self._sleep,
            )
            if verified:
                self._note_direct(
                    run, try_number, "open_explorer", "done", performed=True, verified=True
                )
                return _Outcome("done", "Se abrió el explorador y se vio su ventana.", _DIRECT_STEP)
            self._note_direct(
                run, try_number, "open_explorer", "failed", performed=True, verified=False
            )
        return _Outcome(
            "failed", "Se pidió abrir el explorador, pero no se vio su ventana.", _DIRECT_STEP
        )

    def _explorer_verified(self, request: ActionRequest, baseline: Baseline) -> bool:
        if request.expect is not None:
            return expectation_met(self._desktop, request.expect, baseline, excluded=self._excluded)
        return any(
            window.handle not in baseline.window_handles
            and _same_executable(window.process_name, _EXPLORER_APP)
            and window.class_name == _EXPLORER_CLASS
            for window in self._desktop.windows()
        )

    def _media(self, run: _Run) -> _Outcome:
        request = run.request
        action = request.media
        if action is None:  # la validación de ActionRequest lo impide
            return _Outcome("failed", "Falta la acción multimedia.", _DIRECT_STEP)
        if self._dry_run:
            if direct.DIRECT_ACTION_KEYS[action] not in self._config.launcher.direct_actions:
                return _Outcome(
                    "forbidden",
                    f"La acción directa '{direct.DIRECT_ACTION_KEYS[action]}' no está activada "
                    "en launcher.direct_actions.",
                    _DIRECT_STEP,
                )
            self._note_direct(run, 1, "media", "would_do")
            return _Outcome("would_do", f"Se haría: {request.describe()}.", _DIRECT_STEP)
        try:
            direct.media(
                action, authorized_by=self._authorized_by, config=self._config, audit=self._audit
            )
        except ActionDisabledError as error:
            self._note_direct(run, 1, "media", "forbidden: disabled")
            return _Outcome("forbidden", str(error), _DIRECT_STEP)
        except ActionError as error:
            self._note_direct(run, 1, "media", f"error: {error}", performed=True)
            return _Outcome("failed", f"No se pudo pulsar la tecla: {error}", _DIRECT_STEP)
        self._note_direct(run, 1, "media", "done_unverified", performed=True)
        return _Outcome("done_unverified", f"{request.describe()}: hecho.", _DIRECT_STEP)

    def _direct_baseline(self) -> Baseline:
        """Lo que había antes de una acción directa. De una ventana prohibida no se lee nada."""
        try:
            current = self._desktop.foreground()
        except READ_ERRORS:
            current = None
        if current is not None and self._gate.check_window(current) is None:
            return capture_baseline(self._desktop, current, None)
        try:
            handles = frozenset(window.handle for window in self._desktop.windows())
        except READ_ERRORS:
            handles = frozenset()
        return Baseline(
            foreground_handle=None,
            window_handles=handles,
            process_handles=frozenset(),
            fingerprint=None,
            state=None,
        )

    def _note_direct(
        self,
        run: _Run,
        try_number: int,
        op: str,
        result: str,
        *,
        performed: bool = False,
        verified: bool | None = None,
    ) -> None:
        # Las acciones directas ya las audita C07: aquí solo se anotan en el resultado.
        run.attempts.append(
            Attempt(
                strategy=_DIRECT_STEP,
                step=0,
                try_number=try_number,
                description=run.request.describe(),
                op=op,
                gate=None,
                confirmed=None,
                performed=performed,
                verified=verified,
                result=result,
            )
        )

    # Auditoría

    def _audit_attempt(self, attempt: Attempt, target: _Target, window: WindowRef) -> None:
        if self._dry_run:
            return
        props = target.element.props
        gate = attempt.gate
        self._audit.record(
            action=f"ui:{attempt.op}",
            target=element_label(props),
            app=window.process_name,
            result=attempt.result,
            authorized_by=self._authorized_by,
            details={
                "strategy": attempt.strategy,
                "step": attempt.step,
                "try": attempt.try_number,
                "control_type": props.control_type,
                "automation_id": props.automation_id,
                "gate_verdict": gate.verdict if gate is not None else None,
                "gate_rule": gate.rule if gate is not None else None,
                "pattern": gate.pattern if gate is not None else None,
                "confirmed_by": self._confirmer.who if attempt.confirmed else None,
                "verified": attempt.verified,
            },
        )

    def _audit_final(
        self, run: _Run, result: str, strategy: ResolutionStep | None, message: str
    ) -> None:
        if self._dry_run:
            return
        request = run.request
        app = {"explorer": _EXPLORER_APP, "media": ""}.get(request.kind, request.app)
        self._audit.record(
            action="execute",
            target=request.describe(),
            app=app,
            result=result,
            authorized_by=self._authorized_by,
            details={
                "strategy": strategy,
                "attempts": len(run.attempts),
                "performed": sum(1 for attempt in run.attempts if attempt.performed),
                "message": message,
            },
        )

    def _current_request(self) -> ActionRequest:
        if self._run_request is None:
            raise RuntimeError("No hay ninguna orden en curso.")
        return self._run_request

    def _is_excluded(self, process_name: str) -> bool:
        return process_name.strip().casefold() in self._excluded_keys
