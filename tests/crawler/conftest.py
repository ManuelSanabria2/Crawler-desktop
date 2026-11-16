"""Fixtures compartidas de los tests del crawler.

Las de apps reales solo se usan en tests ``windows_ui``. Abren la app por su ejecutable o su
URI y la cierran con el mensaje WM_CLOSE: nunca usan el mouse ni el teclado. Si la app ya
estaba abierta, el test se omite para no tocar una ventana de Manuel.

``build_synthetic_db`` crea la base sintética de la especificación C05, que usan los tests
del grafo y los de la exportación. Se construye con la interfaz pública de ``CrawlerStore``
y devuelve los ids reales en un mapa con claves legibles:

- apps: ``editor`` y ``calc``;
- estados: ``E1`` a ``E5`` y ``C1``;
- elementos: ``<estado>.<nombre>``, por ejemplo ``E3.Exportar a PDF``;
- transiciones: ``T1`` a ``T6``.
"""

import re
import time
from collections.abc import Callable, Collection, Iterator
from pathlib import Path
from typing import Any

import pytest

OPEN_TIMEOUT_S = 25.0
SETTLE_TIMEOUT_S = 15.0
CLOSE_TIMEOUT_S = 10.0
POLL_S = 0.3
WM_CLOSE = 0x0010
DWMWA_CLOAKED = 14

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "uia"


@pytest.fixture(scope="session")
def uia_fixture() -> Callable[[str], Any]:
    """Carga un volcado guardado en tests/fixtures/uia/ por su nombre, sin extensión."""
    from geass.crawler.uia import WindowDump

    cache: dict[str, Any] = {}

    def load(name: str) -> Any:
        if name not in cache:
            text = (FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")
            cache[name] = WindowDump.model_validate_json(text)
        return cache[name]

    return load


def visible_windows() -> dict[int, str]:
    """Ventanas con título que el usuario ve. Excluye las apps de la tienda suspendidas."""
    import ctypes
    from ctypes import wintypes

    import win32gui

    found: dict[int, str] = {}

    def collect(handle: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(handle):
            return True
        cloaked = wintypes.DWORD(0)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(handle), DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        title = win32gui.GetWindowText(handle)
        if not cloaked.value and title:
            found[handle] = title
        return True

    win32gui.EnumWindows(collect, None)
    return found


@pytest.fixture
def open_app() -> Iterator[Callable[[Callable[[], object], str], int]]:
    """Devuelve una función que abre una app y entrega el handle de su ventana.

    Al terminar el test se cierran las ventanas abiertas con ella.
    """
    import win32gui

    opened: list[int] = []

    def launch_and_wait(launch: Callable[[], object], title_pattern: str) -> int:
        pattern = re.compile(title_pattern, re.IGNORECASE)
        before = visible_windows()
        if any(pattern.search(title) for title in before.values()):
            pytest.skip(f"Ya hay una ventana '{title_pattern}' abierta; ciérrala primero.")
        launch()
        deadline = time.monotonic() + OPEN_TIMEOUT_S
        while time.monotonic() < deadline:
            time.sleep(POLL_S)
            for handle, title in visible_windows().items():
                if handle not in before and pattern.search(title):
                    opened.append(handle)
                    return handle
        pytest.fail(f"No apareció la ventana '{title_pattern}' en {OPEN_TIMEOUT_S} s.")

    yield launch_and_wait

    for handle in opened:
        win32gui.PostMessage(handle, WM_CLOSE, 0, 0)
    deadline = time.monotonic() + CLOSE_TIMEOUT_S
    while time.monotonic() < deadline and any(h in visible_windows() for h in opened):
        time.sleep(POLL_S)
    still_open = [handle for handle in opened if handle in visible_windows()]
    assert not still_open, f"Quedaron ventanas sin cerrar: {still_open}"


@pytest.fixture
def settled_dump() -> Callable[..., Any]:
    """Devuelve una función que vuelca una ventana cuando su interfaz deja de cambiar.

    Con ``differs_from`` espera además a que el contenido sea distinto al de ese volcado,
    para esperar una navegación.
    """
    from geass.core.config import load_crawler_config
    from geass.crawler.uia import dump_window

    config = load_crawler_config()

    def shape(result: Any) -> list[tuple[str, str, str]]:
        return [(e.path, e.automation_id, e.name) for e in result.root.iter_tree()]

    def dump(handle: int, differs_from: Any = None) -> Any:
        previous = None
        deadline = time.monotonic() + SETTLE_TIMEOUT_S
        while True:
            time.sleep(POLL_S)
            result = dump_window(handle, config=config)
            current = shape(result)
            navigated = differs_from is None or current != shape(differs_from)
            if (current == previous and navigated) or time.monotonic() > deadline:
                return result
            previous = current

    return dump


# Base sintética (especificación C05)

Ids = dict[str, int]

# Estado lógico: (título, [(tipo, nombre, patrones, habilitado)]).
EDITOR_STATES: dict[str, tuple[str, list[tuple[str, str, list[str], bool]]]] = {
    "E1": (
        "Principal",
        [
            ("MenuItem", "Archivo", ["ExpandCollapse"], True),
            ("Button", "Guardar", ["Invoke"], True),
            ("Button", "Ayuda", ["Invoke"], True),
            ("Text", "Listo", [], True),
        ],
    ),
    "E2": (
        "Menú Archivo",
        [
            ("MenuItem", "Exportar", ["ExpandCollapse"], True),
            ("MenuItem", "Imprimir", ["Invoke"], True),
        ],
    ),
    "E3": (
        "Menú Exportar",
        [
            ("MenuItem", "Exportar a PDF", ["Invoke"], True),
            ("MenuItem", "Exportar a PNG", ["Invoke"], True),
        ],
    ),
    "E4": (
        "Diálogo PDF",
        [
            ("Button", "Aceptar", ["Invoke"], True),
            ("Edit", "Nombre de archivo", ["Value"], True),
            ("Button", "Configuración avanzada", ["Invoke"], False),
        ],
    ),
    "E5": ("Suelto", [("Button", "Exportar a PDF", ["Invoke"], True)]),
}
E1_VISITS = 3

# Transición lógica: (origen, destino, elemento o None, acción, fuente, veces).
EDITOR_TRANSITIONS: dict[str, tuple[str, str, str | None, str, str, int]] = {
    "T1": ("E1", "E2", "E1.Archivo", "expand_collapse", "passive", 3),
    "T2": ("E2", "E3", "E2.Exportar", "expand_collapse", "passive", 1),
    "T3": ("E3", "E4", "E3.Exportar a PDF", "invoke", "passive", 1),
    "T4": ("E4", "E1", "E4.Aceptar", "invoke", "passive", 1),
    "T5": ("E1", "E3", None, "unknown", "passive", 1),
    "T6": ("E1", "E3", "E1.Ayuda", "coordinate_click", "active", 1),
}

CALC_ELEMENTS: list[tuple[str, str, list[str], bool]] = [
    ("Button", "Exportar PDF", ["Invoke"], True),
    ("Button", "Igual", ["Invoke"], True),
]


def _element_input(
    index: int, control_type: str, name: str, patterns: list[str], enabled: bool = True
) -> Any:
    from geass.crawler.store import ElementInput

    return ElementInput(
        path=f"0/{index}",
        control_type=control_type,
        name=name,
        automation_id="",
        rect=None,
        patterns=patterns,
        enabled=enabled,
    )


def _add_state(
    store: Any,
    ids: Ids,
    app_id: int,
    key: str,
    title: str,
    elements: list[tuple[str, str, list[str], bool]],
    visits: int = 1,
) -> None:
    for _ in range(visits):
        state = store.upsert_state(app_id, f"sig-{key}", f"det-{key}", title)
    ids[key] = state.id
    inputs = [
        _element_input(index, control_type, name, patterns, enabled)
        for index, (control_type, name, patterns, enabled) in enumerate(elements)
    ]
    by_path = store.upsert_elements(state.id, inputs)
    for item in inputs:
        ids[f"{key}.{item.name}"] = by_path[item.path]


def _build_synthetic(path: Path, skip: Collection[str] = ()) -> Ids:
    """Crea la base sintética en ``path``. ``skip`` lista transiciones lógicas a omitir."""
    from geass.crawler.store import CrawlerStore

    ids: Ids = {}
    with CrawlerStore(path) as store:
        editor = store.upsert_app("editor.exe", "Editor de Prueba")
        calc = store.upsert_app("calc.exe", "Calculadora")
        ids["editor"], ids["calc"] = editor.id, calc.id
        for key, (title, elements) in EDITOR_STATES.items():
            visits = E1_VISITS if key == "E1" else 1
            _add_state(store, ids, editor.id, key, title, elements, visits)
        _add_state(store, ids, calc.id, "C1", "Calculadora", CALC_ELEMENTS)
        for key, (source, target, element, action, origin, times) in EDITOR_TRANSITIONS.items():
            if key in skip:
                continue
            for _ in range(times):
                record = store.record_transition(
                    ids[source],
                    ids[target],
                    action,
                    origin,
                    element_id=None if element is None else ids[element],
                )
            ids[key] = record.id
    return ids


@pytest.fixture
def build_synthetic_db() -> Callable[[Path, Collection[str]], Ids]:
    """Función que crea la base sintética en una ruta, omitiendo las transiciones dadas.

    Devuelve el mapa de ids reales.
    """
    return _build_synthetic
