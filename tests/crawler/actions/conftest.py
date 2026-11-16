"""Dobles del sistema y del escritorio, y fixtures de los tests de acciones (C07 y C08a).

El fixture ``fake_system`` es ``autouse``: sustituye las ocho funciones de
``geass.crawler.actions.system`` por dobles que anotan sus llamadas y no hacen nada. Así
ningún test rápido puede abrir una app, pulsar una tecla, hacer clic, aplicar un patrón de
UI Automation, cerrar una ventana ni llamar a PowerShell, ni siquiera por error. No se
aplica a los tests con marker ``windows_ui``.

``FakeDesktop`` cumple el contrato de ``Desktop`` sobre un mundo en memoria. Los mundos de
prueba (Configuración, Calculadora y Bloc de notas) usan nombres reales de
``tests/fixtures/uia/``. ``make_executor`` crea ejecutores sobre esos mundos con un reloj
falso y la auditoría en ``tmp_path``; al terminar cada test comprueba que toda acción fue
precedida de una decisión de la puerta que la permitía (caso 90).

``REAL_SYSTEM`` guarda las funciones reales, tomadas al importar este archivo, antes de que
el fixture las sustituya. Solo se usan para probar sus caminos de validación, que fallan
antes de tocar el sistema.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from geass.core.config import load_crawler_config, load_safety_config
from geass.core.paths import config_dir
from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions import system
from geass.crawler.actions.audit import AuditLog
from geass.crawler.actions.confirm import ConfirmationRequest, DenyAllConfirmer
from geass.crawler.actions.errors import ActionError
from geass.crawler.actions.executor import Executor
from geass.crawler.actions.gate import SafetyGate
from geass.crawler.actions.index import AppIndex
from geass.crawler.actions.live import (
    LiveElement,
    PatternState,
    VisualCandidate,
    VisualQuery,
    WindowRef,
)
from geass.crawler.graph import CrawlerGraph
from geass.crawler.graph.text import normalize_text
from geass.crawler.signature import SignatureRules, compute_signature
from geass.crawler.store.records import AppRecord, ElementRecord, StateRecord, TransitionRecord
from geass.crawler.uia.dump import ExcludedAppError, WindowNotFoundError
from geass.crawler.uia.reader import NodeProps
from geass.crawler.uia.schema import Element, Rect, WindowDump

SYSTEM_FUNCTIONS = (
    "start_shortcut",
    "start_app_id",
    "spawn_explorer",
    "send_media_key",
    "read_start_apps",
    "perform_pattern",
    "click_at",
    "close_window",
)
REAL_SYSTEM: dict[str, Callable[..., Any]] = {
    name: getattr(system, name) for name in SYSTEM_FUNCTIONS
}
LAUNCH_FUNCTIONS = ("start_shortcut", "start_app_id")

MENU_A = (
    "Spotify.lnk",
    "Bloc de notas.lnk",
    "Herramientas/Calculadora.lnk",
    "Visual Studio Code.lnk",
    "Visual Studio 2022.lnk",
    "Desinstalar Foo.lnk",
    "leeme.txt",
)
MENU_B = ("Spotify.lnk", "Paint.lnk")
START_APPS = [
    {"Name": "Calculadora", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
    {
        "Name": "Configuración",
        "AppID": "windows.immersivecontrolpanel_cw5n1h2txyewy"
        "!microsoft.windows.immersivecontrolpanel",
    },
    {"Name": "  Fotos ", "AppID": "Microsoft.Windows.Photos_8wekyb3d8bbwe!App"},
]
PHOTOS_APP_ID = "Microsoft.Windows.Photos_8wekyb3d8bbwe!App"
ALL_NAMES = [
    "Bloc de notas",
    "Calculadora",
    "Configuración",
    "Desinstalar Foo",
    "Fotos",
    "Paint",
    "Spotify",
    "Visual Studio 2022",
    "Visual Studio Code",
]


@dataclass
class FakeSystem:
    """Dobles de las funciones de ``system``. Anotan las llamadas y no hacen nada."""

    start_apps_output: str = field(default_factory=lambda: json.dumps(START_APPS))
    start_apps_error: Exception | None = None
    errors: dict[str, Exception] = field(default_factory=dict)
    # Efecto de cada función sobre el mundo de prueba (por ejemplo, añadir una ventana).
    effects: dict[str, Callable[..., None]] = field(default_factory=dict)
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def calls_to(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args in self.calls if called == name]

    def launch_calls(self) -> list[tuple[str, tuple[Any, ...]]]:
        return [call for call in self.calls if call[0] in LAUNCH_FUNCTIONS]

    def _note(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.errors:
            raise self.errors[name]
        if name in self.effects:
            self.effects[name](*args)

    def start_shortcut(self, path: Path) -> None:
        self._note("start_shortcut", path)

    def start_app_id(self, app_id: str) -> None:
        self._note("start_app_id", app_id)

    def spawn_explorer(self, path: Path | None) -> None:
        self._note("spawn_explorer", path)

    def send_media_key(self, action: str) -> None:
        self._note("send_media_key", action)

    def perform_pattern(self, element: Any, op: str) -> None:
        self._note("perform_pattern", element, op)

    def click_at(self, x: int, y: int) -> None:
        self._note("click_at", x, y)

    def close_window(self, handle: int) -> None:
        self._note("close_window", handle)

    def read_start_apps(self, timeout_s: float) -> str:
        self._note("read_start_apps", timeout_s)
        if self.start_apps_error is not None:
            raise self.start_apps_error
        return self.start_apps_output


@pytest.fixture(autouse=True)
def fake_system(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> FakeSystem:
    """Sustituye las funciones de ``system`` por dobles, salvo en los tests ``windows_ui``."""
    fake = FakeSystem()
    if request.node.get_closest_marker("windows_ui") is None:
        for name in SYSTEM_FUNCTIONS:
            monkeypatch.setattr(system, name, getattr(fake, name))
    return fake


@pytest.fixture
def menu_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Carpetas A y B de menú Inicio de prueba, con archivos .lnk vacíos."""
    folders = (tmp_path / "menu_a", tmp_path / "menu_b")
    for folder, names in zip(folders, (MENU_A, MENU_B), strict=True):
        for name in names:
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
    return folders


@pytest.fixture
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "app_index.json"


def with_launcher(config: CrawlerConfig, **changes: Any) -> CrawlerConfig:
    """Copia de ``config`` con cambios en la sección ``launcher``."""
    return config.model_copy(update={"launcher": config.launcher.model_copy(update=changes)})


@pytest.fixture
def config(menu_dirs: tuple[Path, Path], cache_path: Path) -> CrawlerConfig:
    """crawler.yaml real con el menú Inicio y la caché apuntando a ``tmp_path``."""
    return with_launcher(
        load_crawler_config(),
        start_menu_dirs=[str(folder) for folder in menu_dirs],
        cache_path=str(cache_path),
    )


@pytest.fixture
def safety() -> SafetyConfig:
    """safety.yaml real."""
    return load_safety_config()


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit" / "audit.jsonl"


@pytest.fixture
def safety_tmp_audit(safety: SafetyConfig, audit_path: Path) -> SafetyConfig:
    """safety.yaml real con ``crawler.audit_log.path`` apuntando a ``tmp_path``."""
    crawler = safety.crawler
    audit_log = crawler.audit_log.model_copy(update={"path": str(audit_path)})
    return safety.model_copy(
        update={"crawler": crawler.model_copy(update={"audit_log": audit_log})}
    )


@pytest.fixture
def audit(audit_path: Path) -> AuditLog:
    """Registro de auditoría en ``tmp_path``."""
    return AuditLog(audit_path)


def audit_lines(path: Path) -> list[dict[str, Any]]:
    """Líneas del registro de auditoría ya leídas como JSON. Vacía si no existe."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def index(config: CrawlerConfig, safety: SafetyConfig) -> AppIndex:
    """Índice construido con el menú Inicio de prueba."""
    return AppIndex.build(config, safety)


# Los archivos de test no pueden importar este conftest (--import-mode=importlib): las
# funciones auxiliares y las constantes se les entregan como fixtures.


@pytest.fixture
def real_system() -> dict[str, Callable[..., Any]]:
    """Funciones reales de ``system``. Solo para probar sus caminos de validación."""
    return dict(REAL_SYSTEM)


@pytest.fixture
def read_audit() -> Callable[[Path], list[dict[str, Any]]]:
    return audit_lines


@pytest.fixture
def launcher_with() -> Callable[..., CrawlerConfig]:
    return with_launcher


@pytest.fixture
def all_names() -> list[str]:
    """Las 9 apps del índice de prueba, en el orden del índice."""
    return list(ALL_NAMES)


@pytest.fixture
def photos_app_id() -> str:
    return PHOTOS_APP_ID


# Escritorio falso (especificación C08a)

FULL_RECT = (0, 0, 1000, 800)
_OP_PATTERN = {
    "invoke": "Invoke",
    "toggle": "Toggle",
    "expand": "ExpandCollapse",
    "collapse": "ExpandCollapse",
    "select": "SelectionItem",
}


def make_props(
    control_type: str,
    name: str,
    automation_id: str = "",
    patterns: tuple[str, ...] = (),
    *,
    rect: tuple[int, int, int, int] | None = None,
    enabled: bool = True,
    offscreen: bool = False,
    is_password: bool = False,
) -> NodeProps:
    return NodeProps(
        control_type=control_type,
        name=name,
        automation_id=automation_id,
        class_name="",
        rect=None
        if rect is None
        else Rect(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3]),
        enabled=enabled,
        offscreen=offscreen,
        is_password=is_password,
        patterns=tuple(patterns),
    )


@dataclass(eq=False)
class FakeElement:
    """Elemento de un mundo de prueba. Con ``findable`` falso, ``find`` no lo ve, pero
    ``element_at`` sí."""

    props: NodeProps
    state: PatternState = field(default_factory=PatternState)
    findable: bool = True

    def rename(self, name: str) -> None:
        self.props = replace(self.props, name=name)

    def change(self, **changes: Any) -> None:
        self.props = replace(self.props, **changes)


def make_element(
    control_type: str,
    name: str,
    automation_id: str = "",
    patterns: tuple[str, ...] = (),
    *,
    state: PatternState | None = None,
    findable: bool = True,
    **props: Any,
) -> FakeElement:
    return FakeElement(
        props=make_props(control_type, name, automation_id, patterns, **props),
        state=state if state is not None else PatternState(),
        findable=findable,
    )


@dataclass(eq=False)
class FakeWindow:
    ref: WindowRef
    elements: list[FakeElement] = field(default_factory=list)
    listed: bool = True  # aparece en windows()


def make_window(
    handle: int,
    process_name: str,
    title: str,
    elements: list[FakeElement] | None = None,
    *,
    class_name: str = "",
    process_id: int | None = None,
    rect: tuple[int, int, int, int] | None = FULL_RECT,
) -> FakeWindow:
    ref = WindowRef(
        handle=handle,
        process_id=process_id if process_id is not None else handle * 10,
        process_name=process_name,
        class_name=class_name,
        title=title,
        rect=None
        if rect is None
        else Rect(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3]),
    )
    return FakeWindow(ref=ref, elements=list(elements or []))


Hook = Callable[["FakeDesktop", LiveElement, str], None]
ClickHook = Callable[["FakeDesktop", int, int], None]


class FakeDesktop:
    """``Desktop`` sobre un mundo en memoria. Anota todas las lecturas y acciones."""

    def __init__(
        self,
        windows: list[FakeWindow],
        *,
        foreground: int | None = None,
        excluded: tuple[str, ...] = ("chrome.exe",),
    ) -> None:
        self.windows_by_handle: dict[int, FakeWindow] = {}
        for window in windows:
            self.add_window(window)
        self.foreground_handle = foreground
        self.excluded = {name.casefold() for name in excluded}
        self.on_perform: Hook | None = None
        self.on_click: ClickHook | None = None
        self.after_find: Callable[[FakeDesktop], None] | None = None
        self.point_override: dict[tuple[int, int], FakeElement] = {}
        self.performed: list[tuple[int, str, str]] = []
        self.clicks: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.reads: list[tuple[Any, ...]] = []
        self.events: list[tuple[Any, ...]] | None = None  # registro compartido del caso 90

    # Mundo

    def add_window(self, window: FakeWindow, *, foreground: bool = False) -> None:
        self.windows_by_handle[window.ref.handle] = window
        if foreground:
            self.foreground_handle = window.ref.handle

    def remove_window(self, handle: int) -> None:
        self.windows_by_handle.pop(handle, None)
        if self.foreground_handle == handle:
            self.foreground_handle = None

    def window(self, handle: int) -> FakeWindow:
        return self.windows_by_handle[handle]

    def get(self, name: str, control_type: str | None = None) -> FakeElement:
        """El único elemento del mundo con ese nombre (y tipo, si se da)."""
        found = [
            element
            for window in self.windows_by_handle.values()
            for element in window.elements
            if element.props.name == name
            and (control_type is None or element.props.control_type == control_type)
        ]
        assert len(found) == 1, f"{name}: {len(found)} elementos"
        return found[0]

    def owner(self, element: FakeElement) -> FakeWindow:
        for window in self.windows_by_handle.values():
            if any(item is element for item in window.elements):
                return window
        raise KeyError(element.props.name)

    def live(self, element: FakeElement) -> LiveElement:
        window = self.owner(element)
        return LiveElement(
            props=element.props,
            process_id=window.ref.process_id,
            window_handle=window.ref.handle,
            ref=element,
        )

    def _check(self, process_name: str) -> None:
        if process_name.casefold() in self.excluded:
            raise ExcludedAppError(f"La app {process_name} está excluida.")

    def _at(self, x: int, y: int) -> FakeElement | None:
        if (x, y) in self.point_override:
            return self.point_override[(x, y)]
        found = None
        for window in self.windows_by_handle.values():
            for element in window.elements:
                rect = element.props.rect
                if rect is not None and rect.left <= x < rect.right and rect.top <= y < rect.bottom:
                    found = element
        return found

    # Desktop: lecturas

    def foreground(self) -> WindowRef | None:
        self.reads.append(("foreground",))
        window = self.windows_by_handle.get(self.foreground_handle or -1)
        return window.ref if window is not None else None

    def windows(self) -> list[WindowRef]:
        self.reads.append(("windows",))
        return [window.ref for window in self.windows_by_handle.values() if window.listed]

    def find(
        self, window: WindowRef, *, name: str = "", automation_id: str = "", control_type: str = ""
    ) -> list[LiveElement]:
        self.reads.append(("find", window.handle))
        if not (name or automation_id or control_type):
            raise ValueError("find sin criterios")
        self._check(window.process_name)
        others = sorted(
            handle
            for handle, item in self.windows_by_handle.items()
            if handle != window.handle and item.ref.process_id == window.process_id
        )
        wanted = normalize_text(name)
        found: list[LiveElement] = []
        for handle in [window.handle, *others]:
            item = self.windows_by_handle.get(handle)
            if item is None:
                continue
            for element in item.elements:
                props = element.props
                if not element.findable:
                    continue
                if automation_id and props.automation_id != automation_id:
                    continue
                if name and normalize_text(props.name) != wanted:
                    continue
                if control_type and props.control_type != control_type:
                    continue
                found.append(self.live(element))
        if self.after_find is not None:
            self.after_find(self)
        return found

    def element_at(self, x: int, y: int) -> LiveElement | None:
        self.reads.append(("element_at", x, y))
        element = self._at(x, y)
        if element is None:
            return None
        if self.owner(element).ref.process_name.casefold() in self.excluded:
            return None
        return self.live(element)

    def state(self, element: LiveElement) -> PatternState:
        self.reads.append(("state", element.window_handle))
        window = self.windows_by_handle.get(element.window_handle)
        if window is None:
            raise WindowNotFoundError(f"La ventana {element.window_handle} ya no existe.")
        self._check(window.ref.process_name)
        return element.ref.state

    def snapshot(self, window: WindowRef) -> WindowDump:
        self.reads.append(("snapshot", window.handle))
        self._check(window.process_name)
        item = self.windows_by_handle.get(window.handle)
        if item is None:
            raise WindowNotFoundError(f"La ventana {window.handle} ya no existe.")
        children = []
        counts: dict[str, int] = {}
        for element in item.elements:
            props = element.props
            index = counts.get(props.control_type, 0)
            counts[props.control_type] = index + 1
            children.append(
                Element(
                    control_type=props.control_type,
                    name=props.name,
                    automation_id=props.automation_id,
                    class_name=props.class_name,
                    rect=props.rect,
                    enabled=props.enabled,
                    offscreen=props.offscreen,
                    is_password=props.is_password,
                    patterns=list(props.patterns),
                    path=f"Window[0]/{props.control_type}[{index}]",
                )
            )
        ref = item.ref
        root = Element(
            control_type="Window",
            name=ref.title,
            automation_id="",
            class_name=ref.class_name,
            rect=ref.rect,
            enabled=True,
            offscreen=False,
            is_password=False,
            patterns=["Window"],
            path="Window[0]",
            children=children,
        )
        return WindowDump(
            handle=ref.handle,
            title=ref.title,
            class_name=ref.class_name,
            process_id=ref.process_id,
            process_name=ref.process_name,
            captured_at=datetime(2026, 11, 14, tzinfo=UTC),
            elapsed_s=0.0,
            node_count=len(children) + 1,
            max_depth_reached=1 if children else 0,
            partial=False,
            partial_reasons=[],
            error_count=0,
            root=root,
        )

    # Desktop: acciones

    def perform(self, element: LiveElement, op: str) -> None:
        self.performed.append((element.window_handle, element.props.name, op))
        if self.events is not None:
            self.events.append(("act", element.props))
        if _OP_PATTERN[op] not in element.props.patterns:
            raise ActionError(f"El elemento no admite el patrón {_OP_PATTERN[op]}.")
        if self.on_perform is not None:
            self.on_perform(self, element, op)

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        if self.events is not None:
            under = self._at(x, y)
            self.events.append(("act", under.props if under is not None else None))
        if self.on_click is not None:
            self.on_click(self, x, y)

    def close(self, window: WindowRef) -> None:
        self.closed.append(window.handle)


def apply_patterns(desktop: FakeDesktop, element: LiveElement, op: str) -> None:
    """Efecto natural de un patrón: seleccionar, desplegar, contraer o alternar."""
    fake: FakeElement = element.ref
    if op == "select":
        for other in desktop.owner(fake).elements:
            if other.props.control_type == fake.props.control_type and other.state.selected:
                other.state = replace(other.state, selected=False)
        fake.state = replace(fake.state, selected=True)
    elif op == "expand":
        fake.state = replace(fake.state, expand="expanded")
    elif op == "collapse":
        fake.state = replace(fake.state, expand="collapsed")
    elif op == "toggle":
        fake.state = replace(fake.state, toggle="off" if fake.state.toggle == "on" else "on")


SETTINGS = 100
CALC = 200
NOTEPAD = 300
NOTEPAD_DIALOG = 301
PAINT = 400
SETTINGS_ITEMS = (
    "Inicio",
    "Sistema",
    "Bluetooth y dispositivos",
    "Personalización",
    "Windows Update",
)


def settings_world() -> FakeDesktop:
    """Configuración en primer plano. Seleccionar un ListItem desmarca los demás."""
    elements = [
        make_element(
            "ListItem",
            name,
            patterns=("SelectionItem",),
            state=PatternState(selected=index == 0),
            rect=(10, 100 + 40 * index, 300, 130 + 40 * index),
        )
        for index, name in enumerate(SETTINGS_ITEMS)
    ]
    elements.append(
        make_element(
            "MenuItem",
            "Sistema",
            patterns=("ExpandCollapse",),
            state=PatternState(expand="collapsed"),
            rect=(0, 0, 30, 30),
        )
    )
    elements.append(
        make_element("Button", "Personalización", patterns=("Invoke",), rect=(400, 100, 600, 140))
    )
    desktop = FakeDesktop(
        [
            make_window(
                SETTINGS,
                "SystemSettings.exe",
                "Configuración",
                elements,
                class_name="ApplicationFrameWindow",
            )
        ],
        foreground=SETTINGS,
    )
    desktop.on_perform = apply_patterns
    return desktop


def calc_effect(desktop: FakeDesktop, element: LiveElement, op: str) -> None:
    """Calculadora mínima: dígitos, suma, igual y borrar actualizan el texto del resultado."""
    window = desktop.owner(element.ref)
    results = next(
        item for item in window.elements if item.props.automation_id == "CalculatorResults"
    )
    memory = window.__dict__.setdefault("memory", {"entry": 0, "total": 0})
    automation_id = element.props.automation_id
    if automation_id.startswith("num") and automation_id.endswith("Button"):
        memory["entry"] = int(automation_id[3:-6])
    elif automation_id == "plusButton":
        memory["total"] += memory["entry"]
        memory["entry"] = 0
    elif automation_id == "equalButton":
        memory["entry"] += memory["total"]
        memory["total"] = 0
    elif automation_id == "clearButton":
        memory["entry"] = memory["total"] = 0
    results.rename(f"Se muestra {memory['entry']}")


def calc_window(handle: int = CALC) -> FakeWindow:
    buttons = (
        ("Dos", "num2Button"),
        ("Tres", "num3Button"),
        ("Más", "plusButton"),
        ("Es igual a", "equalButton"),
        ("Borrar", "clearButton"),
    )
    elements = [
        make_element(
            "Button",
            name,
            automation_id,
            ("Invoke",),
            rect=(10 + 60 * index, 400, 60 + 60 * index, 450),
        )
        for index, (name, automation_id) in enumerate(buttons)
    ]
    elements.append(
        make_element("Text", "Se muestra 0", "CalculatorResults", rect=(10, 100, 400, 200))
    )
    return make_window(
        handle, "CalculatorApp.exe", "Calculadora", elements, class_name="ApplicationFrameWindow"
    )


def calc_world() -> FakeDesktop:
    desktop = FakeDesktop([calc_window()], foreground=CALC)
    desktop.on_perform = calc_effect
    return desktop


def notepad_menu_items() -> list[FakeElement]:
    return [
        make_element(
            "MenuItem", name, patterns=("Invoke",), rect=(10, 60 + 30 * i, 200, 85 + 30 * i)
        )
        for i, name in enumerate(("Configurar página", "Guardar", "Eliminar"))
    ]


def notepad_effect(desktop: FakeDesktop, element: LiveElement, op: str) -> None:
    """Abrir Archivo añade su menú a la misma ventana; Configurar página abre un diálogo."""
    fake: FakeElement = element.ref
    window = desktop.owner(fake)
    if fake.props.automation_id == "File" and op in ("invoke", "expand"):
        fake.state = replace(fake.state, expand="expanded")
        if len(window.elements) == 2:
            window.elements.extend(notepad_menu_items())
    elif fake.props.name == "Configurar página":
        desktop.add_window(
            make_window(
                NOTEPAD_DIALOG,
                "Notepad.exe",
                "Configurar página",
                class_name="#32770",
                process_id=window.ref.process_id,
            )
        )


def notepad_world(*, menu_open: bool = False, file_name: str = "Archivo") -> FakeDesktop:
    """Bloc de notas con el menú Archivo cerrado (o abierto con ``menu_open``)."""
    elements = [
        make_element(
            "MenuItem",
            file_name,
            "File",
            ("Invoke", "ExpandCollapse"),
            state=PatternState(expand="expanded" if menu_open else "collapsed"),
            rect=(10, 30, 60, 50),
        ),
        make_element(
            "MenuItem",
            "Editar",
            "Edit",
            ("Invoke", "ExpandCollapse"),
            state=PatternState(expand="collapsed"),
            rect=(70, 30, 120, 50),
        ),
    ]
    if menu_open:
        elements.extend(notepad_menu_items())
    desktop = FakeDesktop(
        [
            make_window(
                NOTEPAD, "Notepad.exe", "Sin título: Bloc de notas", elements, class_name="Notepad"
            )
        ],
        foreground=NOTEPAD,
    )
    desktop.on_perform = notepad_effect
    return desktop


PANEL_CENTER = (200, 150)


def paint_world(panel_name: str = "Panel") -> FakeDesktop:
    """Una ventana con un Panel sin patrones; el clic cambia el nombre del texto de estado."""
    elements = [
        make_element("Custom", panel_name, rect=(100, 100, 300, 200)),
        make_element("Text", "Color 1", "StatusText", rect=(10, 700, 300, 750)),
        make_element(
            "Button", "Lienzo", "", ("Invoke",), findable=False, rect=(500, 300, 700, 500)
        ),
    ]
    desktop = FakeDesktop(
        [make_window(PAINT, "mspaint.exe", "Sin título: Paint", elements, class_name="MSPaintApp")],
        foreground=PAINT,
    )

    def on_click(desktop: FakeDesktop, x: int, y: int) -> None:
        desktop.get("Color 1").rename("Color 2")

    desktop.on_click = on_click
    return desktop


class FakeClock:
    """Reloj falso: ``sleep`` avanza el reloj, así que las esperas terminan al instante."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeDetector:
    def __init__(self, candidates: list[VisualCandidate]) -> None:
        self.candidates = candidates
        self.queries: list[VisualQuery] = []

    def locate(self, window: WindowRef, query: VisualQuery) -> list[VisualCandidate]:
        self.queries.append(query)
        return list(self.candidates)


class YesConfirmer:
    def __init__(self, who: str = "test:manuel") -> None:
        self.who = who
        self.requests: list[ConfirmationRequest] = []

    def __call__(self, request: ConfirmationRequest) -> bool:
        self.requests.append(request)
        return True


class _RecordingConfirmer:
    """Envuelve al confirmador del test para anotar sus respuestas en el registro del caso 90."""

    def __init__(self, inner: Any, events: list[tuple[Any, ...]]) -> None:
        self._inner = inner
        self._events = events
        self.who = inner.who

    def __call__(self, request: ConfirmationRequest) -> bool:
        answer = bool(self._inner(request))
        self._events.append(("confirm", answer))
        return answer


def check_gate_before_actions(events: list[tuple[Any, ...]]) -> None:
    """Caso 90: cada acción va precedida de una decisión de la puerta para ese mismo
    elemento, ``free`` o ``confirm`` con el confirmador diciendo que sí."""
    for index, event in enumerate(events):
        if event[0] != "act":
            continue
        previous = [item for item in events[:index] if item[0] == "gate"]
        assert previous, f"acción sin decisión previa de la puerta: {event}"
        gate_index = max(i for i, item in enumerate(events[:index]) if item[0] == "gate")
        _, props, decision = events[gate_index]
        assert props == event[1], f"la decisión era para otro elemento: {props} != {event[1]}"
        if decision.verdict == "confirm":
            answers = [item[1] for item in events[gate_index:index] if item[0] == "confirm"]
            assert answers == [True], f"acción con confirmación no dada: {answers}"
        else:
            assert decision.verdict == "free", f"acción con veredicto {decision.verdict}"


def with_executor(config: CrawlerConfig, **changes: Any) -> CrawlerConfig:
    """Copia de ``config`` con cambios en la sección ``executor``."""
    return config.model_copy(update={"executor": config.executor.model_copy(update=changes)})


def without_step(config: CrawlerConfig, step: str) -> CrawlerConfig:
    order = [item for item in config.executor.resolution_order if item != step]
    return with_executor(config, resolution_order=order)


def safety_with_allowed_windows(windows: Any) -> SafetyConfig:
    """safety.yaml real con ``authorization.allowed_windows`` dado."""
    data = yaml.safe_load((config_dir() / "safety.yaml").read_text(encoding="utf-8"))
    data["crawler"]["authorization"]["allowed_windows"] = windows
    return SafetyConfig.model_validate(data)


def safety_from_yaml(**crawler_changes: Any) -> SafetyConfig:
    data = yaml.safe_load((config_dir() / "safety.yaml").read_text(encoding="utf-8"))
    data["crawler"].update(crawler_changes)
    return SafetyConfig.model_validate(data)


GRAPH_TIME = datetime(2026, 11, 14, tzinfo=UTC)


def notepad_signature(config: CrawlerConfig, **world: Any) -> str:
    desktop = notepad_world(**world)
    dump = desktop.snapshot(desktop.foreground())
    return compute_signature(dump, SignatureRules.from_config(config.signature)).structural


def element_record(
    element_id: int, state_id: int, name: str, automation_id: str = "", patterns: Any = None
) -> ElementRecord:
    return ElementRecord(
        id=element_id,
        state_id=state_id,
        path=f"Window[0]/MenuItem[{element_id}]",
        control_type="MenuItem",
        name=name,
        automation_id=automation_id,
        rect=None,
        patterns=list(patterns or ["Invoke"]),
        enabled=True,
    )


def state_record(state_id: int, structural: str) -> StateRecord:
    return StateRecord(
        id=state_id,
        app_id=1,
        structural_signature=structural,
        detailed_signature=f"det-{state_id}",
        title_pattern="bloc de notas",
        first_seen=GRAPH_TIME,
        last_seen=GRAPH_TIME,
        visits=1,
        screenshot_path=None,
    )


def notepad_graph(
    config: CrawlerConfig,
    *,
    file_name: str = "Archivo",
    action: str = "invoke",
    target_name: str = "Configurar página",
    signatures: tuple[str, str] | None = None,
) -> CrawlerGraph:
    s1, s2 = signatures or (
        notepad_signature(config, file_name=file_name),
        notepad_signature(config, file_name=file_name, menu_open=True),
    )
    app = AppRecord(
        id=1, executable="Notepad.exe", display_name="Bloc de notas", app_user_model_id=None
    )
    patterns = ["Invoke", "ExpandCollapse"]
    elements = [
        element_record(11, 1, file_name, "File", patterns),
        element_record(12, 1, "Editar", "Edit", patterns),
        element_record(21, 2, file_name, "File", patterns),
        element_record(22, 2, "Editar", "Edit", patterns),
        element_record(23, 2, target_name),
        element_record(24, 2, "Guardar"),
        element_record(25, 2, "Eliminar"),
    ]
    transition = TransitionRecord(
        id=1,
        source_state_id=1,
        target_state_id=2,
        element_id=11,
        action_type=action,
        source="passive",
        count=1,
        mean_latency_s=None,
        latency_samples=0,
        last_seen=GRAPH_TIME,
    )
    """Grafo del Bloc de notas: S1 (menú cerrado) va a S2 (menú Archivo desplegado) por el
    elemento Archivo. Las firmas salen del volcado del mundo falso."""
    return CrawlerGraph(
        [app],
        [state_record(1, s1), state_record(2, s2)],
        elements,
        [transition],
        fuzzy_match_threshold=80,
        coordinate_click_cost=3.0,
    )


@pytest.fixture
def fakes() -> SimpleNamespace:
    """Dobles y mundos de prueba del ejecutor (no se pueden importar desde los tests)."""
    return SimpleNamespace(
        FakeDesktop=FakeDesktop,
        FakeClock=FakeClock,
        FakeDetector=FakeDetector,
        YesConfirmer=YesConfirmer,
        make_props=make_props,
        make_element=make_element,
        make_window=make_window,
        apply_patterns=apply_patterns,
        settings_world=settings_world,
        calc_world=calc_world,
        calc_window=calc_window,
        notepad_world=notepad_world,
        paint_world=paint_world,
        notepad_graph=notepad_graph,
        with_executor=with_executor,
        without_step=without_step,
        safety_with_allowed_windows=safety_with_allowed_windows,
        safety_from_yaml=safety_from_yaml,
        check_gate_before_actions=check_gate_before_actions,
        SETTINGS=SETTINGS,
        CALC=CALC,
        NOTEPAD=NOTEPAD,
        NOTEPAD_DIALOG=NOTEPAD_DIALOG,
        PAINT=PAINT,
        PANEL_CENTER=PANEL_CENTER,
    )


@pytest.fixture
def gate_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[Any, ...]]]:
    """Registro de decisiones de la puerta, confirmaciones y acciones (caso 90).

    Al terminar el test comprueba que toda acción tuvo antes una decisión que la permitía.
    """
    events: list[tuple[Any, ...]] = []
    original = SafetyGate.check

    def check(self: SafetyGate, **kwargs: Any) -> Any:
        decision = original(self, **kwargs)
        events.append(("gate", kwargs["props"], decision, kwargs["op"]))
        return decision

    monkeypatch.setattr(SafetyGate, "check", check)
    yield events
    check_gate_before_actions([event[:3] for event in events])


@pytest.fixture
def make_executor(
    config: CrawlerConfig,
    safety: SafetyConfig,
    audit: AuditLog,
    gate_events: list[tuple[Any, ...]],
) -> Callable[..., Executor]:
    """Fábrica de ejecutores sobre un ``FakeDesktop``, con reloj falso y auditoría de prueba."""

    def build(desktop: FakeDesktop, **kwargs: Any) -> Executor:
        desktop.events = gate_events
        confirmer = kwargs.pop("confirmer", None) or DenyAllConfirmer()
        clock = kwargs.pop("fake_clock", None) or FakeClock()
        kwargs.setdefault("config", config)
        kwargs.setdefault("safety", safety)
        kwargs.setdefault("audit", audit)
        kwargs.setdefault("authorized_by", "test")
        return Executor(
            confirmer=_RecordingConfirmer(confirmer, gate_events),
            desktop=desktop,
            clock=clock.clock,
            sleep=clock.sleep,
            **kwargs,
        )

    return build
