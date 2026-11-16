"""Lo que el ejecutor ve del escritorio: ventanas, elementos vivos y sus estados.

``Desktop`` es el contrato con el escritorio. El real es ``WinDesktop`` (desktop_win.py);
los tests usan un doble en memoria. Las lecturas nunca leen valores de texto, y solo
``perform``, ``click`` y ``close`` actúan.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from geass.crawler.uia.reader import NodeProps
from geass.crawler.uia.schema import Rect, WindowDump

PatternOp = Literal["invoke", "toggle", "expand", "collapse", "select"]


@dataclass(frozen=True)
class WindowRef:
    """Una ventana de nivel superior."""

    handle: int
    process_id: int
    process_name: str  # p. ej. "Notepad.exe"; vacío si no se pudo leer
    class_name: str
    title: str  # vacío si el proceso está en apps.excluded: no se conserva
    rect: Rect | None


@dataclass(frozen=True)
class PatternState:
    """Estado de los patrones de un elemento. ``None`` en los que no tiene."""

    toggle: Literal["off", "on", "indeterminate"] | None = None
    expand: Literal["collapsed", "expanded", "partially", "leaf"] | None = None
    selected: bool | None = None


@dataclass(frozen=True)
class LiveElement:
    """Un elemento vivo de la interfaz.

    ``ref`` es el elemento COM; fuera de desktop_win es opaco.
    """

    props: NodeProps
    process_id: int
    window_handle: int  # ventana de nivel superior en la que se encontró
    ref: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class VisualQuery:
    """Lo que se le pide al detector visual que encuentre."""

    name: str
    automation_id: str
    control_type: str


@dataclass(frozen=True)
class VisualCandidate:
    """Un elemento que el detector visual cree ver."""

    rect: Rect  # coordenadas de pantalla
    label: str  # clase del detector, p. ej. "button"
    score: float  # 0 a 1


class VisualDetector(Protocol):
    """Detector visual de elementos (sección 6.11). En esta versión no hay ninguno real."""

    def locate(self, window: WindowRef, query: VisualQuery) -> list[VisualCandidate]: ...


class Desktop(Protocol):
    """Lo que el ejecutor necesita del escritorio. El real es ``WinDesktop``."""

    def foreground(self) -> WindowRef | None: ...
    def windows(self) -> list[WindowRef]: ...
    def find(
        self, window: WindowRef, *, name: str = "", automation_id: str = "", control_type: str = ""
    ) -> list[LiveElement]: ...
    def element_at(self, x: int, y: int) -> LiveElement | None: ...
    def state(self, element: LiveElement) -> PatternState: ...
    def snapshot(self, window: WindowRef) -> WindowDump: ...
    def perform(self, element: LiveElement, op: PatternOp) -> None: ...
    def click(self, x: int, y: int) -> None: ...
    def close(self, window: WindowRef) -> None: ...


def element_label(props: NodeProps) -> str:
    """El nombre, o [AutomationId], o [sin nombre]."""
    name = props.name.strip()
    if name:
        return name
    if props.automation_id.strip():
        return f"[{props.automation_id}]"
    return "[sin nombre]"
