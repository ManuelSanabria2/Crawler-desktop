"""Captura del elemento bajo el cursor al presionar el mouse.

Intervienen dos hilos, además del de sondeo:

- El de escucha (pynput) solo anota la posición y la hora y vuelve enseguida. Corre dentro
  del gancho de mouse de Windows: si tardara, el sistema retrasaría el mouse de todo el
  escritorio y acabaría retirando el gancho. Por eso ahí no se llama a UI Automation.
- El de captura espera esas anotaciones y lee el elemento bajo el punto en cuanto llegan.
  Está separado del de sondeo para no quedar detrás de un volcado, que puede tardar cientos
  de milisegundos: el elemento hay que leerlo antes de que la interfaz reaccione al clic.

Este módulo solo escucha. Nunca mueve el mouse ni genera clics.
"""

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from geass.core.log import get_logger
from geass.crawler.passive.control import PassiveStats, PauseState
from geass.crawler.passive.events import (
    MousePress,
    PressCapture,
    PrivacyPolicy,
    Sensor,
    WindowRef,
)
from geass.crawler.store.records import ActionType
from geass.crawler.uia.reader import NodeProps
from geass.crawler.uia.schema import Element, WindowDump

logger = get_logger(__name__)

Clock = Callable[[], float]

_STOP = object()
_JOIN_TIMEOUT_S = 5.0

# Patrón de UI Automation que decide el tipo de acción de un clic izquierdo, por prioridad.
_ACTION_BY_PATTERN: tuple[tuple[str, ActionType], ...] = (
    ("Invoke", "invoke"),
    ("Toggle", "toggle"),
    ("ExpandCollapse", "expand_collapse"),
    ("SelectionItem", "select"),
)
LEFT_BUTTON = "left"


class PressCapturer:
    """Convierte cada ``MousePress`` en un ``PressCapture`` y lo entrega al observador."""

    def __init__(
        self,
        sensor: Sensor,
        privacy: PrivacyPolicy,
        pause: PauseState,
        sink: Callable[[PressCapture], None],
        *,
        max_delay_s: float,
        stats: PassiveStats | None = None,
        clock: Clock = time.perf_counter,
        thread_setup: Callable[[], None] | None = None,
        thread_teardown: Callable[[], None] | None = None,
    ) -> None:
        """
        Args:
            sensor: lecturas del escritorio. Solo se usan ``window_at`` y ``element_at``.
            privacy: filtros de privacidad.
            pause: interruptor de pausa. En pausa no se lee nada.
            sink: destino de las capturas; normalmente ``PassiveObserver.post``.
            max_delay_s: retraso máximo entre el clic y la lectura del elemento.
            stats: contadores compartidos.
            clock: el mismo reloj que usa quien crea los ``MousePress``.
            thread_setup: se llama al empezar el hilo de captura (inicialización de COM).
            thread_teardown: se llama al terminar el hilo de captura.
        """
        self._sensor = sensor
        self._privacy = privacy
        self._pause = pause
        self._sink = sink
        self._max_delay_s = max_delay_s
        self._stats = stats if stats is not None else PassiveStats()
        self._clock = clock
        self._thread_setup = thread_setup
        self._thread_teardown = thread_teardown
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def submit(self, press: MousePress) -> None:
        """Encola un clic. Es lo único que se llama desde el hilo de escucha: no bloquea."""
        self._queue.put(press)

    def capture(self, press: MousePress) -> PressCapture | None:
        """Lee qué había bajo el cursor. Devuelve ``None`` si el observador está en pausa.

        El orden importa para la privacidad: primero se identifica la ventana sin usar
        UI Automation y se consulta el filtro; solo si lo permite se lee el elemento.
        """
        self._stats.increment("presses_seen")
        if self._pause.is_paused():
            self._stats.increment("presses_dropped_paused")
            return None

        window = self._sensor.window_at(press.x, press.y)
        if window is None or not window.identified:
            self._stats.increment("presses_unidentified_window")
            return self._result(press, None, None)
        if not self._privacy.allows_window(window):
            self._stats.increment("presses_blocked_privacy")
            return self._result(press, None, None)

        element = self._sensor.element_at(press.x, press.y)
        if element is not None and self._clock() - press.at > self._max_delay_s:
            self._stats.increment("press_elements_late")
            element = None
        if element is not None and not self._privacy.allows_press_element(element):
            self._stats.increment("press_elements_blocked_privacy")
            element = None
        if element is not None:
            self._stats.increment("press_elements_read")
        return self._result(press, window, element)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("El hilo de captura ya está en marcha.")
        self._thread = threading.Thread(target=self._run, name="geass-passive-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Pide al hilo que termine y espera a que lo haga."""
        thread = self._thread
        if thread is None:
            return
        self._queue.put(_STOP)
        thread.join(_JOIN_TIMEOUT_S)
        self._thread = None

    def _result(
        self, press: MousePress, window: WindowRef | None, element: NodeProps | None
    ) -> PressCapture:
        return PressCapture(
            press=press, window=window, element=element, delay_s=self._clock() - press.at
        )

    def _run(self) -> None:
        if self._thread_setup is not None:
            self._thread_setup()
        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    return
                try:
                    captured = self.capture(item)
                    if captured is not None:
                        self._sink(captured)
                except Exception:
                    self._stats.increment("capture_errors")
                    logger.exception("Falló la captura de un clic; se descarta.")
        finally:
            if self._thread_teardown is not None:
                self._thread_teardown()


class MouseListener:
    """Escucha del mouse con pynput. Solo avisa de los botones presionados.

    No se suscribe al movimiento ni a la rueda, y no usa la clase de pynput que genera
    movimientos y clics.
    """

    def __init__(
        self, on_press: Callable[[MousePress], None], *, clock: Clock = time.perf_counter
    ) -> None:
        self._on_press = on_press
        self._clock = clock
        self._listener: Any = None

    def start(self) -> None:
        if self._listener is not None:
            raise RuntimeError("La escucha del mouse ya está en marcha.")
        from pynput import mouse

        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        listener.stop()
        self._listener = None

    def _on_click(self, x: float, y: float, button: Any, pressed: bool) -> None:
        # Corre dentro del gancho de Windows: nada lento y ninguna excepción hacia fuera.
        if not pressed:
            return
        try:
            name = str(getattr(button, "name", button))
            self._on_press(MousePress(x=int(x), y=int(y), button=name, at=self._clock()))
        except Exception:
            logger.exception("Falló el aviso de un clic; se descarta.")


def match_element(dump: WindowDump, element: NodeProps, x: int, y: int) -> Element | None:
    """Busca en un volcado el elemento que se leyó bajo el cursor.

    Coinciden el tipo de control, el AutomationId, el nombre y la clase, y el rectángulo del
    volcado contiene el punto. Si hay varios, gana el más profundo y, a igual profundidad,
    el de menor área. Devuelve ``None`` si no hay ninguno: por ejemplo, si la ventana se
    movió después del volcado o el elemento pertenece a una ventana emergente aparte.
    """
    best: Element | None = None
    best_rank: tuple[int, int] | None = None
    for candidate in dump.root.iter_tree():
        rect = candidate.rect
        if rect is None or not (rect.left <= x < rect.right and rect.top <= y < rect.bottom):
            continue
        if (
            candidate.control_type != element.control_type
            or candidate.automation_id != element.automation_id
            or candidate.name != element.name
            or candidate.class_name != element.class_name
        ):
            continue
        rank = (-candidate.path.count("/"), rect.width * rect.height)
        if best_rank is None or rank < best_rank:
            best, best_rank = candidate, rank
    return best


def infer_action(button: str, patterns: tuple[str, ...] | list[str] | None) -> ActionType:
    """Tipo de acción de un clic según el botón y los patrones del elemento.

    Solo el botón izquierdo sobre un elemento conocido da un tipo concreto. Sin elemento, o
    con otro botón, es ``unknown``. Un clic izquierdo sobre un elemento sin ninguno de los
    patrones de acción es ``coordinate_click``.
    """
    if button != LEFT_BUTTON or patterns is None:
        return "unknown"
    for pattern, action in _ACTION_BY_PATTERN:
        if pattern in patterns:
            return action
    return "coordinate_click"
