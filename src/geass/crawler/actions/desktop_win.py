"""El escritorio real sobre UI Automation (pywinauto y comtypes). Solo Windows.

Las lecturas solo piden las propiedades de ``NodeProps`` y los indicadores de patrones: nunca
el valor de un campo de texto. De una app de ``apps.excluded`` no se lee nada del árbol y su
título no se conserva. Este módulo no mueve el mouse, no pulsa teclas, no escribe texto y no
trae ventanas al frente: las tres operaciones que actúan solo llaman a ``system``.

pywinauto se importa al crear ``WinDesktop``, no al importar el módulo.
"""

import ctypes
from ctypes import wintypes
from dataclasses import replace
from pathlib import PureWindowsPath
from typing import Any, Literal

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.actions import system
from geass.crawler.actions.live import LiveElement, PatternOp, PatternState, WindowRef
from geass.crawler.graph.text import normalize_text
from geass.crawler.uia.dump import ExcludedAppError, UiaError, dump_window
from geass.crawler.uia.reader import NodeProps
from geass.crawler.uia.schema import Rect, WindowDump

_PROPERTY_NAMES = (
    "ControlType",
    "Name",
    "AutomationId",
    "ClassName",
    "BoundingRectangle",
    "IsEnabled",
    "IsOffscreen",
    "IsPassword",
)
_DWMWA_CLOAKED = 14  # DwmGetWindowAttribute: la ventana está oculta por DWM
_GA_ROOT = 2  # GetAncestor: ventana de nivel superior
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # OpenProcess: solo leer el nombre del ejecutable
_TOGGLE_STATES: dict[int, Literal["off", "on", "indeterminate"]] = {
    0: "off",
    1: "on",
    2: "indeterminate",
}
_EXPAND_STATES: dict[int, Literal["collapsed", "expanded", "partially", "leaf"]] = {
    0: "collapsed",
    1: "expanded",
    2: "partially",
    3: "leaf",
}


class WinDesktop:
    """``Desktop`` sobre el escritorio real de Windows."""

    def __init__(self, config: CrawlerConfig) -> None:
        from comtypes import COMError
        from pywinauto.uia_defines import IUIA, NoPatternInterfaceError

        from geass.crawler.uia.backend_win import PATTERN_NAMES, WindowInfo

        self._config = config
        self._excluded = frozenset(name.strip().casefold() for name in config.apps.excluded)
        self._window_info = WindowInfo
        self._read_errors: tuple[type[BaseException], ...] = (
            COMError,
            OSError,
            ValueError,
            AttributeError,
            NoPatternInterfaceError,
        )
        uia = IUIA()
        dll = uia.UIA_dll
        self._iuia = uia.iuia
        self._control_type_names: dict[int, str] = dict(uia.known_control_type_ids)
        self._pattern_ids: list[tuple[str, int]] = [
            (name, getattr(dll, f"UIA_Is{name}PatternAvailablePropertyId"))
            for name in PATTERN_NAMES
        ]
        self._process_id_property: int = dll.UIA_ProcessIdPropertyId
        self._scope_descendants: int = dll.TreeScope_Descendants
        self._condition = (
            self._iuia.ControlViewCondition
            if config.uia.tree_view == "control"
            else uia.true_condition
        )
        self._cache = self._iuia.CreateCacheRequest()
        self._point_cache = self._iuia.CreateCacheRequest()
        for request in (self._cache, self._point_cache):
            for name in _PROPERTY_NAMES:
                request.AddProperty(getattr(dll, f"UIA_{name}PropertyId"))
            for _, property_id in self._pattern_ids:
                request.AddProperty(property_id)
        self._point_cache.AddProperty(self._process_id_property)

    # Lecturas

    def foreground(self) -> WindowRef | None:
        import win32gui

        try:
            handle = int(win32gui.GetForegroundWindow())
            if not handle:
                return None
            return self._window_ref(handle)
        except (win32gui.error, *self._read_errors):
            return None

    def windows(self) -> list[WindowRef]:
        import win32gui

        found: list[WindowRef] = []
        try:
            for handle in _top_level_handles():
                if not _is_shown(handle) or not win32gui.GetWindowText(handle):
                    continue
                found.append(self._window_ref(handle))
        except (win32gui.error, *self._read_errors) as error:
            raise UiaError(f"No se pudieron leer las ventanas: {error!r}") from error
        return found

    def find(
        self, window: WindowRef, *, name: str = "", automation_id: str = "", control_type: str = ""
    ) -> list[LiveElement]:
        if not (name or automation_id or control_type):
            raise ValueError("find necesita al menos uno de name, automation_id o control_type.")
        self._require_allowed(window.process_name)
        wanted_name = normalize_text(name)
        found: list[LiveElement] = []
        for handle in self._process_windows(window):
            for element in self._elements_of(handle):
                props = self._read(element)
                if automation_id and props.automation_id != automation_id:
                    continue
                if name and normalize_text(props.name) != wanted_name:
                    continue
                if control_type and props.control_type != control_type:
                    continue
                found.append(
                    LiveElement(
                        props=props,
                        process_id=window.process_id,
                        window_handle=handle,
                        ref=element,
                    )
                )
        return found

    def element_at(self, x: int, y: int) -> LiveElement | None:
        import win32gui

        try:
            element = self._iuia.ElementFromPointBuildCache(wintypes.POINT(x, y), self._point_cache)
            if not element:
                return None
            process_id = int(element.GetCachedPropertyValue(self._process_id_property))
            if self._is_excluded(_process_name(process_id)):
                return None
            props = self._read(element)
            under = win32gui.WindowFromPoint((x, y))
            root = int(win32gui.GetAncestor(under, _GA_ROOT)) if under else 0
        except (win32gui.error, *self._read_errors) as error:
            raise UiaError(f"No se pudo leer el punto ({x}, {y}): {error!r}") from error
        return LiveElement(props=props, process_id=process_id, window_handle=root, ref=element)

    def state(self, element: LiveElement) -> PatternState:
        from pywinauto.uia_defines import get_elem_interface

        self._require_allowed(_process_name(element.process_id))
        patterns = element.props.patterns
        state = PatternState()
        try:
            if "Toggle" in patterns:
                toggle = get_elem_interface(element.ref, "Toggle")
                state = replace(state, toggle=_TOGGLE_STATES.get(int(toggle.CurrentToggleState)))
            if "ExpandCollapse" in patterns:
                expand = get_elem_interface(element.ref, "ExpandCollapse")
                value = int(expand.CurrentExpandCollapseState)
                state = replace(state, expand=_EXPAND_STATES.get(value))
            if "SelectionItem" in patterns:
                item = get_elem_interface(element.ref, "SelectionItem")
                state = replace(state, selected=bool(item.CurrentIsSelected))
        except self._read_errors as error:
            raise UiaError(f"No se pudo leer el estado del elemento: {error!r}") from error
        return state

    def snapshot(self, window: WindowRef) -> WindowDump:
        self._require_allowed(window.process_name)
        return dump_window(window.handle, config=self._config)

    # Acciones: solo llaman a system

    def perform(self, element: LiveElement, op: PatternOp) -> None:
        system.perform_pattern(element.ref, op)

    def click(self, x: int, y: int) -> None:
        system.click_at(x, y)

    def close(self, window: WindowRef) -> None:
        system.close_window(window.handle)

    # Internos

    def _is_excluded(self, process_name: str) -> bool:
        key = process_name.strip().casefold()
        return key in self._excluded or (not key and bool(self._excluded))

    def _require_allowed(self, process_name: str) -> None:
        """Lanza ``ExcludedAppError`` antes de leer nada de una app excluida."""
        if self._is_excluded(process_name):
            if not process_name.strip():
                raise ExcludedAppError(
                    "No se pudo identificar el proceso de la ventana; no se lee por precaución."
                )
            raise ExcludedAppError(f"La app {process_name} está excluida en crawler.yaml.")

    def _window_ref(self, handle: int) -> WindowRef:
        import win32gui

        info = self._window_info(handle)
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        rect = (
            Rect(left=left, top=top, right=right, bottom=bottom)
            if right > left and bottom > top
            else None
        )
        return WindowRef(
            handle=handle,
            process_id=info.process_id,
            process_name=info.process_name,
            class_name=info.class_name,
            title="" if self._is_excluded(info.process_name) else info.title,
            rect=rect,
        )

    def _process_windows(self, window: WindowRef) -> list[int]:
        """``window`` y después las demás ventanas visibles del mismo proceso, por handle."""
        import win32gui

        others: list[int] = []
        try:
            for handle in _top_level_handles():
                if handle == window.handle or not _is_shown(handle):
                    continue
                if self._window_info(handle).process_id == window.process_id:
                    others.append(handle)
        except (win32gui.error, *self._read_errors) as error:
            raise UiaError(f"No se pudieron leer las ventanas del proceso: {error!r}") from error
        return [window.handle, *sorted(others)]

    def _elements_of(self, handle: int) -> list[Any]:
        """La raíz de la ventana y sus descendientes, en orden de recorrido."""
        try:
            root = self._iuia.ElementFromHandleBuildCache(handle, self._cache)
            found = root.FindAllBuildCache(self._scope_descendants, self._condition, self._cache)
            descendants = (
                [] if found is None else [found.GetElement(i) for i in range(found.Length)]
            )
        except self._read_errors as error:
            raise UiaError(f"No se pudo leer la ventana {handle}: {error!r}") from error
        return [root, *descendants]

    def _read(self, element: Any) -> NodeProps:
        try:
            type_id = element.CachedControlType
            raw = element.CachedBoundingRectangle
            left, top, right, bottom = int(raw.left), int(raw.top), int(raw.right), int(raw.bottom)
            return NodeProps(
                control_type=self._control_type_names.get(type_id, f"Unknown{type_id}"),
                name=element.CachedName or "",
                automation_id=element.CachedAutomationId or "",
                class_name=element.CachedClassName or "",
                rect=(
                    Rect(left=left, top=top, right=right, bottom=bottom)
                    if right > left and bottom > top
                    else None
                ),
                enabled=bool(element.CachedIsEnabled),
                offscreen=bool(element.CachedIsOffscreen),
                is_password=bool(element.CachedIsPassword),
                patterns=tuple(
                    name
                    for name, property_id in self._pattern_ids
                    if element.GetCachedPropertyValue(property_id)
                ),
            )
        except self._read_errors as error:
            raise UiaError(f"No se pudo leer el elemento: {error!r}") from error


def _top_level_handles() -> list[int]:
    import win32gui

    handles: list[int] = []

    def collect(handle: int, _: object) -> bool:
        handles.append(int(handle))
        return True

    win32gui.EnumWindows(collect, None)
    return handles


def _is_shown(handle: int) -> bool:
    """Visible y no oculta por DWM."""
    import win32gui

    if not win32gui.IsWindowVisible(handle):
        return False
    cloaked = wintypes.DWORD(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(handle), _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return not cloaked.value


def _process_name(process_id: int) -> str:
    """Nombre del ejecutable del proceso, por ejemplo ``notepad.exe``. Vacío si no se puede leer."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return ""
        return PureWindowsPath(buffer.value).name
    finally:
        kernel32.CloseHandle(process)
