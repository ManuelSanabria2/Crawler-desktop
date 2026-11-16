"""Sensor real del observador pasivo: Win32 y UI Automation. Solo Windows y solo lectura.

Hilos y COM: el hilo de captura y el de sondeo usan UI Automation a la vez. Los dos entran
en el apartamento multihilo de COM (``thread_setup``), donde un mismo objeto se puede usar
desde cualquier hilo. El backend de UI Automation se crea en el primero de esos hilos que
lo necesita, nunca en el hilo principal.
"""

import contextlib
import ctypes
import threading
from collections.abc import Sequence
from ctypes import wintypes

import comtypes
import pywintypes
import win32gui

from geass.core.log import get_logger
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.passive.events import WindowRef
from geass.crawler.uia.backend_win import TreeView, WindowInfo, WinUiaBackend
from geass.crawler.uia.dump import dump_window, limits_from_config
from geass.crawler.uia.reader import DumpLimits, NodeProps, UiaNodeError
from geass.crawler.uia.schema import WindowDump

logger = get_logger(__name__)

_GA_ROOT = 2
_PROCESS_PER_MONITOR_DPI_AWARE = 2


class _LastInputInfo(ctypes.Structure):
    _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))


class WinSensor:
    """Implementación de ``Sensor`` sobre el escritorio real."""

    def __init__(
        self,
        *,
        limits: DumpLimits,
        excluded_apps: Sequence[str],
        tree_view: TreeView = "control",
    ) -> None:
        """
        Args:
            limits: límites de cada volcado.
            excluded_apps: ejecutables de los que ``dump`` no lee nada.
            tree_view: vista del árbol de UI Automation.
        """
        self._limits = limits
        self._excluded_apps = list(excluded_apps)
        self._tree_view: TreeView = tree_view
        self._backend: WinUiaBackend | None = None
        self._backend_lock = threading.Lock()
        _ensure_dpi_awareness()

    @classmethod
    def from_config(cls, config: CrawlerConfig) -> "WinSensor":
        """Sensor con los límites, las apps excluidas y la vista de ``crawler.yaml``."""
        return cls(
            limits=limits_from_config(config),
            excluded_apps=config.apps.excluded,
            tree_view=config.uia.tree_view,
        )

    def thread_setup(self) -> None:
        """Entra en el apartamento multihilo de COM. Se llama al empezar cada hilo que lea."""
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError as error:
            # El hilo ya tenía COM iniciado en otro modo. Se sigue con el que tenga.
            logger.warning("No se pudo iniciar COM en modo multihilo: %s", error)

    def thread_teardown(self) -> None:
        """Sale de COM. Se llama al terminar cada hilo que llamó a ``thread_setup``."""
        comtypes.CoUninitialize()

    def foreground(self) -> WindowRef | None:
        handle = int(win32gui.GetForegroundWindow())
        return _window_ref(handle) if handle else None

    def window_at(self, x: int, y: int) -> WindowRef | None:
        try:
            handle = win32gui.WindowFromPoint((x, y))
            root = win32gui.GetAncestor(handle, _GA_ROOT) if handle else 0
        except pywintypes.error:
            return None
        return _window_ref(int(root)) if root else None

    def element_at(self, x: int, y: int) -> NodeProps | None:
        try:
            return self._uia().node_at(x, y).read()
        except UiaNodeError:
            return None

    def dump(self, window: WindowRef) -> WindowDump:
        return dump_window(
            window.handle,
            limits=self._limits,
            excluded_apps=self._excluded_apps,
            backend=self._uia(),
        )

    def input_tick(self) -> int:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        return int(info.dwTime)

    def _uia(self) -> WinUiaBackend:
        with self._backend_lock:
            if self._backend is None:
                self._backend = WinUiaBackend(tree_view=self._tree_view)
            return self._backend


def _window_ref(handle: int) -> WindowRef | None:
    try:
        info = WindowInfo(handle)
    except pywintypes.error:
        return None
    return WindowRef(
        handle=handle,
        process_id=info.process_id,
        process_name=info.process_name,
        class_name=info.class_name,
        title=info.title,
    )


def _ensure_dpi_awareness() -> None:
    """Hace que este proceso vea coordenadas físicas de pantalla.

    Sin esto, en monitores con escala distinta de 100 % la posición del clic y los
    rectángulos de UI Automation no coinciden. Solo afecta a este proceso.
    """
    # Falla si ya estaba fijado (por ejemplo, por pynput) o si la función no existe.
    with contextlib.suppress(OSError, AttributeError):
        ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE)
