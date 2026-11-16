"""Dobles compartidos de los tests del crawler pasivo.

Ningún doble toca el escritorio: no hay ventanas, ni UI Automation, ni escuchas reales de
mouse o teclado. Con ``--import-mode=importlib`` los tests no pueden importar este archivo,
así que todo se entrega mediante fixtures; ``fakes`` reúne las clases y las fábricas.
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.config import load_crawler_config
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.passive import ObserverSettings, WindowRef
from geass.crawler.signature import SignatureRules
from geass.crawler.store import CrawlerStore
from geass.crawler.uia import Element, NodeProps, Rect, UiaError, WindowDump

CAPTURED_AT = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)
WINDOW_RECT = (0, 0, 800, 600)

WINDOW = WindowRef(
    handle=1001,
    process_id=4242,
    process_name="fakeapp.exe",
    class_name="FakeWindow",
    title="Vista - Fake App",
)
OTHER_WINDOW = WindowRef(
    handle=2002,
    process_id=5353,
    process_name="otherapp.exe",
    class_name="OtherWindow",
    title="Otra - Other App",
)

# Tiempos del observador para los tests sin hilos. Coinciden con los de crawler.yaml.
TICK_SETTINGS = ObserverSettings(
    poll_interval_s=0.5,
    stability_window_s=0.4,
    stability_timeout_s=5.0,
    stability_poll_s=0.1,
    resync_interval_s=2.0,
    resync_max_interval_s=30.0,
)

Box = tuple[int, int, int, int]


class FakeClock:
    """Reloj monotónico falso: devuelve ``now``, que el test avanza."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSensor:
    """Cumple ``Sensor``. El test decide qué ve y cuenta las llamadas.

    - ``window``: ventana en primer plano (y bajo el punto, salvo que se fije
      ``point_window``).
    - ``view``: volcado que devuelve ``dump``; ``dump_factory`` lo sustituye si se fija.
    - ``element``: lo que devuelve ``element_at``; ``on_element_at`` se llama antes.
    - ``tick``: marca de la última entrada.
    - ``foreground_queue``: valores que ``foreground`` devuelve antes que ``window``.
    """

    _SAME = object()

    def __init__(self, window: WindowRef | None = None, view: WindowDump | None = None) -> None:
        self._lock = threading.Lock()
        self.window = window
        self.view = view
        self.point_window: Any = self._SAME
        self.element: NodeProps | None = None
        self.tick = 0
        self.foreground_queue: list[WindowRef | None] = []
        self.dump_factory: Callable[[WindowRef], WindowDump] | None = None
        self.on_element_at: Callable[[], None] | None = None
        self.foreground_calls = 0
        self.window_at_calls = 0
        self.element_at_calls = 0
        self.dump_calls = 0

    def show(self, window: WindowRef | None, view: WindowDump | None) -> None:
        """Cambia a la vez la ventana en primer plano y su volcado."""
        with self._lock:
            self.window = window
            self.view = view

    def foreground(self) -> WindowRef | None:
        with self._lock:
            self.foreground_calls += 1
            if self.foreground_queue:
                return self.foreground_queue.pop(0)
            return self.window

    def window_at(self, x: int, y: int) -> WindowRef | None:
        with self._lock:
            self.window_at_calls += 1
            return self.window if self.point_window is self._SAME else self.point_window

    def element_at(self, x: int, y: int) -> NodeProps | None:
        with self._lock:
            self.element_at_calls += 1
        if self.on_element_at is not None:
            self.on_element_at()
        return self.element

    def dump(self, window: WindowRef) -> WindowDump:
        with self._lock:
            self.dump_calls += 1
            factory, view = self.dump_factory, self.view
        if factory is not None:
            return factory(window)
        if view is None:
            raise UiaError("El sensor falso no tiene ninguna vista.")
        return view

    def input_tick(self) -> int:
        return self.tick


class FakePolicy:
    """Política de privacidad configurable. Por defecto lo permite todo."""

    def __init__(self, excluded: Sequence[str] = (), *, allow_passwords: bool = True) -> None:
        self.excluded = {name.lower() for name in excluded}
        self.allow_passwords = allow_passwords

    def allows_window(self, window: WindowRef) -> bool:
        return window.process_name.lower() not in self.excluded

    def allows_press_element(self, element: NodeProps) -> bool:
        return self.allow_passwords or not element.is_password


def rect(box: Box) -> Rect:
    left, top, right, bottom = box
    return Rect(left=left, top=top, right=right, bottom=bottom)


def make_element(
    control_type: str,
    name: str,
    box: Box | None,
    path: str,
    *,
    automation_id: str | None = None,
    children: Sequence[Element] = (),
    patterns: Sequence[str] = (),
    is_password: bool = False,
) -> Element:
    return Element(
        control_type=control_type,
        name=name,
        automation_id=name if automation_id is None else automation_id,
        class_name=control_type,
        rect=rect(box) if box is not None else None,
        enabled=True,
        offscreen=False,
        is_password=is_password,
        patterns=list(patterns),
        path=path,
        children=list(children),
    )


def make_dump(
    buttons: Sequence[tuple[str, Box]],
    *,
    window: WindowRef = WINDOW,
    list_items: Sequence[tuple[str, Box]] = (),
    partial: bool = False,
) -> WindowDump:
    """Volcado de una ventana con botones y, si se dan, una lista con elementos.

    Los botones tienen el patrón Invoke y su AutomationId es su nombre. Los rectángulos
    están en coordenadas de pantalla.
    """
    root_path = "Window[0]"
    children = [
        make_element("Button", name, box, f"{root_path}/Button[{index}]", patterns=("Invoke",))
        for index, (name, box) in enumerate(buttons)
    ]
    if list_items:
        list_path = f"{root_path}/List[0]"
        items = [
            make_element(
                "ListItem",
                name,
                box,
                f"{list_path}/ListItem[{index}]",
                automation_id="",
                patterns=("SelectionItem", "Invoke"),
            )
            for index, (name, box) in enumerate(list_items)
        ]
        children.append(
            make_element("List", "Archivos", (0, 300, 800, 600), list_path, children=items)
        )
    root = make_element(
        "Window", window.title, WINDOW_RECT, root_path, automation_id="", children=children
    )
    return WindowDump(
        handle=window.handle,
        title=window.title,
        class_name=window.class_name,
        process_id=window.process_id,
        process_name=window.process_name,
        captured_at=CAPTURED_AT,
        elapsed_s=0.01,
        node_count=len(list(root.iter_tree())),
        max_depth_reached=2 if list_items else 1,
        partial=partial,
        partial_reasons=["max_nodes"] if partial else [],
        error_count=0,
        root=root,
    )


def find(dump: WindowDump, name: str) -> Element:
    (found,) = [element for element in dump.root.iter_tree() if element.name == name]
    return found


def props_of(element: Element, *, is_password: bool | None = None) -> NodeProps:
    """Lo que ``element_at`` leería de ese elemento."""
    return NodeProps(
        control_type=element.control_type,
        name=element.name,
        automation_id=element.automation_id,
        class_name=element.class_name,
        rect=element.rect,
        enabled=element.enabled,
        offscreen=element.offscreen,
        is_password=element.is_password if is_password is None else is_password,
        patterns=tuple(element.patterns),
    )


def center(element: Element) -> tuple[int, int]:
    assert element.rect is not None
    box = element.rect
    return (box.left + box.right) // 2, (box.top + box.bottom) // 2


# Vistas de ejemplo: A y B son vistas distintas de la misma ventana.
VIEW_A_BUTTONS: list[tuple[str, Box]] = [("ok", (10, 10, 110, 50)), ("cancel", (120, 10, 220, 50))]
VIEW_B_BUTTONS: list[tuple[str, Box]] = [("back", (10, 10, 110, 50))]
VIEW_C_BUTTONS: list[tuple[str, Box]] = [("next", (300, 10, 400, 50))]


@dataclass(frozen=True)
class _Views:
    a: WindowDump
    b: WindowDump
    c: WindowDump


@pytest.fixture(scope="session")
def crawler_config() -> CrawlerConfig:
    return load_crawler_config()


@pytest.fixture(scope="session")
def rules(crawler_config: CrawlerConfig) -> SignatureRules:
    return SignatureRules.from_config(crawler_config.signature)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def views() -> _Views:
    return _Views(
        a=make_dump(VIEW_A_BUTTONS), b=make_dump(VIEW_B_BUTTONS), c=make_dump(VIEW_C_BUTTONS)
    )


@pytest.fixture
def sensor(views: _Views) -> FakeSensor:
    return FakeSensor(WINDOW, views.a)


@pytest.fixture
def policy() -> FakePolicy:
    return FakePolicy()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CrawlerStore]:
    with CrawlerStore(tmp_path / "crawler" / "geass_crawler.sqlite") as opened:
        yield opened


@pytest.fixture
def fakes() -> SimpleNamespace:
    """Clases, fábricas y constantes de este archivo."""
    return SimpleNamespace(
        FakeClock=FakeClock,
        FakeSensor=FakeSensor,
        FakePolicy=FakePolicy,
        WINDOW=WINDOW,
        OTHER_WINDOW=OTHER_WINDOW,
        TICK_SETTINGS=TICK_SETTINGS,
        VIEW_A_BUTTONS=VIEW_A_BUTTONS,
        VIEW_B_BUTTONS=VIEW_B_BUTTONS,
        VIEW_C_BUTTONS=VIEW_C_BUTTONS,
        make_dump=make_dump,
        make_element=make_element,
        find=find,
        props_of=props_of,
        center=center,
        rect=rect,
    )
