"""Tipos que comparten los hilos del observador pasivo.

Ninguno contiene valores de campos de texto. ``PressCapture`` lleva las mismas propiedades
que el lector UIA (``NodeProps``), que por diseño no incluyen ningún valor.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from geass.crawler.uia.reader import NodeProps
from geass.crawler.uia.schema import WindowDump


@dataclass(frozen=True)
class WindowRef:
    """Identidad de una ventana de nivel superior, leída sin UI Automation."""

    handle: int
    process_id: int
    process_name: str
    class_name: str
    title: str

    @property
    def identified(self) -> bool:
        """Verdadero si se conoce con certeza qué ventana y qué proceso son."""
        return bool(self.handle) and bool(self.process_id) and bool(self.process_name.strip())


@dataclass(frozen=True)
class MousePress:
    """Un botón del mouse acaba de presionarse. Lo emite el hilo de escucha del mouse.

    ``at`` es la lectura del reloj del observador (segundos, monotónico) en ese instante.
    """

    x: int
    y: int
    button: str
    at: float


@dataclass(frozen=True)
class PressCapture:
    """Lo que había bajo el cursor al presionar, leído antes de que la interfaz cambie.

    ``window`` es ``None`` si la ventana no se pudo identificar o no se puede leer por
    privacidad; en ese caso tampoco hay ``element``. ``element`` es ``None`` además si la
    lectura falló, llegó tarde o el elemento es un campo de contraseña.
    """

    press: MousePress
    window: WindowRef | None
    element: NodeProps | None
    delay_s: float


@dataclass(frozen=True)
class ShortcutPressed:
    """Se pulsó un atajo de teclado (modificador más tecla). Nunca una tecla suelta.

    Solo se emite si ``passive.capture_shortcuts`` es verdadero.
    """

    combo: str
    at: float


HintKind = Literal["foreground_changed", "structure_changed"]


@dataclass(frozen=True)
class ChangeHint:
    """Aviso externo de que la interfaz pudo cambiar.

    Es el punto de entrada previsto para los eventos de UI Automation: quien los escuche
    solo tiene que publicar uno de estos avisos en ``PassiveObserver.post``.
    """

    kind: HintKind
    at: float


ObserverEvent = PressCapture | ShortcutPressed | ChangeHint


class Sensor(Protocol):
    """Lecturas del escritorio que necesita el observador. Solo lectura.

    La implementación real es ``geass.crawler.passive.sensor_win.WinSensor``. Los métodos se
    llaman desde dos hilos: ``window_at`` y ``element_at`` desde el de captura, y el resto
    desde el de sondeo.
    """

    def foreground(self) -> WindowRef | None:
        """Ventana en primer plano, o ``None`` si no hay ninguna."""
        ...

    def window_at(self, x: int, y: int) -> WindowRef | None:
        """Ventana de nivel superior bajo el punto. No usa UI Automation."""
        ...

    def element_at(self, x: int, y: int) -> NodeProps | None:
        """Elemento de UI Automation bajo el punto, o ``None`` si no se pudo leer."""
        ...

    def dump(self, window: WindowRef) -> WindowDump:
        """Vuelca la ventana. Lanza ``geass.crawler.uia.UiaError`` si no se puede."""
        ...

    def input_tick(self) -> int:
        """Marca de la última entrada de teclado o mouse. Solo sirve para ver si cambió."""
        ...


class PrivacyPolicy(Protocol):
    """Filtros de privacidad que el observador consulta antes de leer o registrar."""

    def allows_window(self, window: WindowRef) -> bool:
        """Falso si de esa ventana no se puede leer ni registrar nada."""
        ...

    def allows_press_element(self, element: NodeProps) -> bool:
        """Falso si el elemento bajo el cursor no se puede registrar."""
        ...
