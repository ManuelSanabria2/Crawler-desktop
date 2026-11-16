"""Comprobación de lo que pasó después de una acción.

Solo lee el escritorio. Un error de lectura nunca se toma como éxito: cuenta como "no se
cumple" y, durante una espera, como "todavía no".
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Collection
from dataclasses import dataclass

from geass.crawler.actions.errors import ActionError
from geass.crawler.actions.live import Desktop, LiveElement, PatternState, WindowRef
from geass.crawler.actions.request import Expectation
from geass.crawler.uia.dump import UiaError
from geass.crawler.uia.reader import UiaNodeError
from geass.crawler.uia.schema import WindowDump

READ_ERRORS: tuple[type[Exception], ...] = (UiaError, UiaNodeError, ActionError)


@dataclass(frozen=True)
class Baseline:
    """Lo que había antes de actuar."""

    foreground_handle: int | None
    window_handles: frozenset[int]  # handles de desktop.windows()
    process_handles: frozenset[int]  # handles de desktop.windows() del proceso de la ventana
    fingerprint: str | None  # ui_fingerprint del volcado de la ventana; None si no se pudo
    state: PatternState | None  # estado del elemento; None si no hay elemento o no se pudo


def ui_fingerprint(dump: WindowDump) -> str:
    """Hash SHA-256 (hex) del volcado.

    Es el hash del multiconjunto de (tipo, nombre, AutomationId, habilitado) de sus elementos:
    el orden de los hermanos no influye.
    """
    counts = Counter(
        (element.control_type, element.name, element.automation_id, element.enabled)
        for element in dump.root.iter_tree()
    )
    items = sorted([list(key), count] for key, count in counts.items())
    text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def process_handles(windows: Collection[WindowRef], process_id: int) -> frozenset[int]:
    """Handles de las ventanas de un proceso."""
    return frozenset(window.handle for window in windows if window.process_id == process_id)


def capture_baseline(desktop: Desktop, window: WindowRef, element: LiveElement | None) -> Baseline:
    """Lee lo que hay antes de actuar. Un error de lectura deja ese campo en ``None`` o vacío."""
    try:
        current = desktop.foreground()
        foreground_handle = current.handle if current is not None else None
    except READ_ERRORS:
        foreground_handle = None
    try:
        windows = desktop.windows()
    except READ_ERRORS:
        windows = []
    try:
        fingerprint: str | None = ui_fingerprint(desktop.snapshot(window))
    except READ_ERRORS:
        fingerprint = None
    state: PatternState | None = None
    if element is not None:
        try:
            state = desktop.state(element)
        except READ_ERRORS:
            state = None
    return Baseline(
        foreground_handle=foreground_handle,
        window_handles=frozenset(item.handle for item in windows),
        process_handles=process_handles(windows, window.process_id),
        fingerprint=fingerprint,
        state=state,
    )


def expectation_met(
    desktop: Desktop,
    expectation: Expectation,
    baseline: Baseline,
    *,
    excluded: Collection[str],
) -> bool:
    """Verdadero si alguna ventana candidata cumple todas las condiciones de la expectativa.

    Con ``new_window`` las candidatas son las ventanas que no estaban en ``baseline``; sin
    él, solo la ventana en primer plano.
    """
    blocked = {name.strip().casefold() for name in excluded}
    try:
        if expectation.new_window:
            candidates = [
                window
                for window in desktop.windows()
                if window.handle not in baseline.window_handles
            ]
        else:
            current = desktop.foreground()
            candidates = [current] if current is not None else []
    except READ_ERRORS:
        return False
    return any(_window_meets(desktop, expectation, window, blocked) for window in candidates)


def _window_meets(
    desktop: Desktop, expectation: Expectation, window: WindowRef, blocked: set[str]
) -> bool:
    process = window.process_name.strip().casefold()
    if expectation.process and process != expectation.process.strip().casefold():
        return False
    if expectation.window_class and window.class_name != expectation.window_class:
        return False
    if expectation.title:
        # El título de una app excluida no se lee: la condición no se puede cumplir.
        if process in blocked:
            return False
        if not re.search(expectation.title, window.title, re.IGNORECASE):
            return False
    wanted = expectation.element
    if wanted is not None:
        # Nada de una app excluida se lee, tampoco su árbol.
        if process in blocked:
            return False
        try:
            found = desktop.find(
                window,
                name=wanted.name,
                automation_id=wanted.automation_id,
                control_type=wanted.control_type,
            )
        except READ_ERRORS:
            return False
        if not found:
            return False
    return True


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    poll_s: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> bool:
    """Evalúa el predicado hasta que sea verdadero o se acabe el tiempo.

    Lo evalúa al menos una vez. Un error de lectura del predicado cuenta como falso; otras
    excepciones se propagan.
    """
    start = clock()
    while True:
        try:
            if predicate():
                return True
        except READ_ERRORS:
            pass
        if clock() > start + timeout_s:
            return False
        sleep(poll_s)
