"""Backend de lectura sobre UI Automation, mediante pywinauto (backend uia). Solo Windows.

Solo lee. Nunca mueve el mouse, nunca pulsa teclas y nunca escribe texto.

Privacidad: las únicas propiedades que se piden a UI Automation son las de
``_PROPERTY_NAMES`` y los indicadores de patrones disponibles. Nunca se pide el valor de
ValuePattern, TextPattern ni LegacyIAccessible, así que el contenido de los campos de texto
y de contraseña no llega a leerse.
"""

import ctypes
from collections.abc import Sequence
from ctypes import wintypes
from pathlib import PureWindowsPath
from typing import Any, Literal

import win32gui
import win32process
from comtypes import COMError
from pywinauto.uia_defines import IUIA

from geass.crawler.uia.reader import NodeProps, RawNode, UiaNodeError
from geass.crawler.uia.schema import Rect

TreeView = Literal["control", "raw"]

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

PATTERN_NAMES = (
    "Invoke",
    "Toggle",
    "ExpandCollapse",
    "SelectionItem",
    "Selection",
    "Value",
    "RangeValue",
    "Scroll",
    "ScrollItem",
    "Text",
    "Window",
    "Transform",
    "Grid",
    "GridItem",
    "Table",
)

_UWP_FRAME_CLASS = "ApplicationFrameWindow"
_UWP_CORE_CLASS = "Windows.UI.Core.CoreWindow"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_READ_ERRORS = (COMError, OSError, ValueError, AttributeError)


class WindowInfo:
    """Datos de la ventana que no salen del árbol de UI Automation."""

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self.title: str = win32gui.GetWindowText(handle)
        self.class_name: str = win32gui.GetClassName(handle)
        self.process_id: int = _owner_process_id(handle, self.class_name)
        self.process_name: str = _process_name(self.process_id)


class _UiaNode:
    """Elemento de UI Automation con sus propiedades ya en caché."""

    def __init__(self, backend: "WinUiaBackend", element: Any) -> None:
        self._backend = backend
        self._element = element

    def read(self) -> NodeProps:
        element = self._element
        try:
            type_id = element.CachedControlType
            control_type = self._backend.control_type_names.get(type_id, f"Unknown{type_id}")
            patterns = tuple(
                name
                for name, property_id in self._backend.pattern_ids
                if element.GetCachedPropertyValue(property_id)
            )
            return NodeProps(
                control_type=control_type,
                name=element.CachedName or "",
                automation_id=element.CachedAutomationId or "",
                class_name=element.CachedClassName or "",
                rect=_to_rect(element.CachedBoundingRectangle),
                enabled=bool(element.CachedIsEnabled),
                offscreen=bool(element.CachedIsOffscreen),
                is_password=bool(element.CachedIsPassword),
                patterns=patterns,
            )
        except _READ_ERRORS as error:
            raise UiaNodeError(f"no se pudo leer el elemento: {error!r}") from error

    def children(self) -> Sequence[RawNode]:
        try:
            found = self._element.FindAllBuildCache(
                self._backend.scope_children, self._backend.condition, self._backend.cache_request
            )
            if found is None:
                return []
            return [_UiaNode(self._backend, found.GetElement(i)) for i in range(found.Length)]
        except _READ_ERRORS as error:
            raise UiaNodeError(f"no se pudieron leer los hijos: {error!r}") from error


class WinUiaBackend:
    """Acceso de solo lectura a UI Automation."""

    def __init__(self, tree_view: TreeView = "control") -> None:
        uia = IUIA()
        self._iuia = uia.iuia
        dll = uia.UIA_dll
        self.control_type_names: dict[int, str] = dict(uia.known_control_type_ids)
        self.pattern_ids: list[tuple[str, int]] = [
            (name, getattr(dll, f"UIA_Is{name}PatternAvailablePropertyId"))
            for name in PATTERN_NAMES
        ]
        self.scope_children: int = dll.TreeScope_Children
        self.condition = (
            self._iuia.ControlViewCondition if tree_view == "control" else uia.true_condition
        )
        self.cache_request = self._iuia.CreateCacheRequest()
        for name in _PROPERTY_NAMES:
            self.cache_request.AddProperty(getattr(dll, f"UIA_{name}PropertyId"))
        for _, property_id in self.pattern_ids:
            self.cache_request.AddProperty(property_id)

    def foreground_handle(self) -> int:
        """Handle de la ventana en primer plano, o 0 si no se puede determinar."""
        return int(win32gui.GetForegroundWindow())

    def is_window(self, handle: int) -> bool:
        return bool(win32gui.IsWindow(handle))

    def window_info(self, handle: int) -> WindowInfo:
        return WindowInfo(handle)

    def root_node(self, handle: int) -> RawNode:
        try:
            element = self._iuia.ElementFromHandleBuildCache(handle, self.cache_request)
        except _READ_ERRORS as error:
            raise UiaNodeError(f"no se pudo abrir la ventana {handle}: {error!r}") from error
        return _UiaNode(self, element)

    def node_at(self, x: int, y: int) -> RawNode:
        """Elemento bajo un punto de la pantalla (píxeles físicos). Solo lee sus propiedades."""
        try:
            element = self._iuia.ElementFromPointBuildCache(
                wintypes.POINT(x, y), self.cache_request
            )
        except _READ_ERRORS as error:
            raise UiaNodeError(f"no se pudo leer el punto ({x}, {y}): {error!r}") from error
        if not element:
            raise UiaNodeError(f"no hay elemento en el punto ({x}, {y})")
        return _UiaNode(self, element)


def _to_rect(raw: Any) -> Rect | None:
    left, top, right, bottom = int(raw.left), int(raw.top), int(raw.right), int(raw.bottom)
    if right <= left or bottom <= top:
        return None
    return Rect(left=left, top=top, right=right, bottom=bottom)


def _owner_process_id(handle: int, class_name: str) -> int:
    """Proceso dueño de la ventana.

    Las apps de la tienda viven dentro de un marco de ApplicationFrameHost.exe. En ese caso
    se devuelve el proceso de la app real, que es el que importa para la lista de excluidas.
    """
    _, process_id = win32process.GetWindowThreadProcessId(handle)
    if class_name == _UWP_FRAME_CLASS:
        core = win32gui.FindWindowEx(handle, 0, _UWP_CORE_CLASS, None)
        if core:
            _, core_process_id = win32process.GetWindowThreadProcessId(core)
            if core_process_id:
                return int(core_process_id)
    return int(process_id)


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
