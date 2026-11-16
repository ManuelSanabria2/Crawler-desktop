"""Las únicas funciones del paquete que tocan el sistema operativo.

Los demás módulos las llaman siempre como atributos de este módulo
(``system.start_shortcut(...)``), nunca importando la función, para que los tests puedan
sustituirlas por dobles que no hacen nada.

Cada función valida sus argumentos antes de tocar el sistema y convierte los fallos de
Windows en ``ActionError``. Ninguna pasa por una shell ni acepta un comando libre.

``SendInput`` solo envía dos cosas: las seis teclas multimedia de ``MEDIA_KEYS`` y el clic
izquierdo de ``click_at``. Sobre la interfaz solo se aplican las cinco operaciones de
``PATTERN_OPS``; ninguna escribe texto.
"""

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path
from typing import Any

from geass.crawler.actions.errors import ActionError

# Lista cerrada de teclas multimedia: son las únicas teclas que se envían con SendInput.
# Aparte de ellas, SendInput solo envía el clic izquierdo de click_at.
MEDIA_KEYS: dict[str, int] = {
    "play_pause": 0xB3,  # VK_MEDIA_PLAY_PAUSE
    "next": 0xB0,  # VK_MEDIA_NEXT_TRACK
    "previous": 0xB1,  # VK_MEDIA_PREV_TRACK
    "volume_up": 0xAF,  # VK_VOLUME_UP
    "volume_down": 0xAE,  # VK_VOLUME_DOWN
    "mute": 0xAD,  # VK_VOLUME_MUTE
}

# Lista cerrada de operaciones de UI Automation: patrón y método, que se llama sin argumentos.
# No hay ninguna otra; en particular, ninguna escribe texto.
PATTERN_OPS: dict[str, tuple[str, str]] = {
    "invoke": ("Invoke", "Invoke"),
    "toggle": ("Toggle", "Toggle"),
    "expand": ("ExpandCollapse", "Expand"),
    "collapse": ("ExpandCollapse", "Collapse"),
    "select": ("SelectionItem", "Select"),
}

# Clases de la barra de tareas y del escritorio: close_window nunca les pide que se cierren.
SHELL_WINDOW_CLASSES: frozenset[str] = frozenset(
    {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
)

_SHORTCUT_SUFFIX = ".lnk"
_EXPLORER = "explorer.exe"
_APPS_FOLDER = "shell:AppsFolder\\"
_FORBIDDEN_APP_ID_CHARS = frozenset("\"'\r\n")

_START_APPS_COMMAND = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"
)

# Constantes de Win32 (winuser.h).
_INPUT_MOUSE = 0  # INPUT.type de un evento de mouse
_INPUT_KEYBOARD = 1  # INPUT.type de un evento de teclado
_KEYEVENTF_KEYUP = 0x0002  # soltar la tecla
_MOUSEEVENTF_MOVE = 0x0001  # mover el cursor
_MOUSEEVENTF_LEFTDOWN = 0x0002  # bajar el botón izquierdo
_MOUSEEVENTF_LEFTUP = 0x0004  # subir el botón izquierdo
_MOUSEEVENTF_VIRTUALDESK = 0x4000  # coordenadas sobre todo el escritorio virtual
_MOUSEEVENTF_ABSOLUTE = 0x8000  # coordenadas absolutas normalizadas de 0 a 65535
_ABSOLUTE_SCALE = 65535  # mayor coordenada normalizada de un movimiento absoluto
_SM_XVIRTUALSCREEN = 76  # GetSystemMetrics: borde izquierdo del escritorio virtual
_SM_YVIRTUALSCREEN = 77  # GetSystemMetrics: borde superior del escritorio virtual
_SM_CXVIRTUALSCREEN = 78  # GetSystemMetrics: ancho del escritorio virtual
_SM_CYVIRTUALSCREEN = 79  # GetSystemMetrics: alto del escritorio virtual
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4  # píxeles físicos en cada monitor
_CURSOR_TOLERANCE_PX = 1  # distancia máxima admitida entre el cursor y el punto pedido
_WM_CLOSE = 0x0010  # mensaje que pide a una ventana que se cierre


class _KeybdInput(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _MouseInput(ctypes.Structure):
    # MOUSEINPUT. Solo la usa click_at: un movimiento absoluto y un clic izquierdo.
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _InputUnion(ctypes.Union):
    _fields_ = (("ki", _KeybdInput), ("mi", _MouseInput))


class _Input(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", _InputUnion))


def start_shortcut(path: Path) -> None:
    """Abre un acceso directo .lnk, como si se hiciera doble clic sobre él."""
    if path.suffix.lower() != _SHORTCUT_SUFFIX:
        raise ActionError(f"Solo se abren accesos directos .lnk: {path}")
    if not path.is_file():
        raise ActionError(f"El acceso directo no existe: {path}")
    try:
        os.startfile(path)  # solo existe en Windows
    except OSError as error:
        raise ActionError(f"Windows no pudo abrir {path}: {error}") from error


def start_app_id(app_id: str) -> None:
    """Abre una app por su AppID de la carpeta de aplicaciones de Windows."""
    if not app_id or app_id != app_id.strip():
        raise ActionError(f"AppID inválido: {app_id!r}")
    if any(char in _FORBIDDEN_APP_ID_CHARS for char in app_id):
        raise ActionError(f"AppID inválido: {app_id!r}")
    try:
        subprocess.Popen([_EXPLORER, f"{_APPS_FOLDER}{app_id}"])
    except OSError as error:
        raise ActionError(f"Windows no pudo abrir la app {app_id}: {error}") from error


def spawn_explorer(path: Path | None) -> None:
    """Abre el explorador de archivos, en una carpeta si se da."""
    arguments = [_EXPLORER] if path is None else [_EXPLORER, str(path)]
    try:
        subprocess.Popen(arguments)
    except OSError as error:
        raise ActionError(f"Windows no pudo abrir el explorador: {error}") from error


def send_media_key(action: str) -> None:
    """Pulsa y suelta una tecla multimedia con SendInput."""
    if action not in MEDIA_KEYS:
        valid = ", ".join(MEDIA_KEYS)
        raise ActionError(f"Tecla multimedia desconocida: '{action}'. Válidas: {valid}.")
    code = MEDIA_KEYS[action]
    events = (_Input * 2)()
    for event, flags in zip(events, (0, _KEYEVENTF_KEYUP), strict=True):
        event.type = _INPUT_KEYBOARD
        event.union.ki = _KeybdInput(wVk=code, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    sent = user32.SendInput(len(events), events, ctypes.sizeof(_Input))
    if sent < len(events):
        raise ActionError(
            f"Windows no aceptó la tecla multimedia '{action}' (error {ctypes.get_last_error()})."
        )


def read_start_apps(timeout_s: float) -> str:
    """Salida JSON de Get-StartApps en PowerShell."""
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _START_APPS_COMMAND]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ActionError(f"Get-StartApps tardó más de {timeout_s} s.") from error
    except (OSError, UnicodeDecodeError) as error:
        raise ActionError(f"No se pudo ejecutar Get-StartApps: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()
        raise ActionError(f"Get-StartApps salió con código {completed.returncode}: {detail}")
    return completed.stdout


def perform_pattern(element: Any, op: str) -> None:
    """Aplica un patrón de UI Automation a un elemento vivo.

    Las operaciones son las de ``PATTERN_OPS``: invocar, alternar, desplegar, contraer o
    seleccionar. No hay ninguna otra.
    """
    if op not in PATTERN_OPS:
        valid = ", ".join(PATTERN_OPS)
        raise ActionError(f"Operación desconocida: '{op}'. Válidas: {valid}.")
    if element is None:
        raise ActionError("No hay elemento sobre el que aplicar la operación.")
    pattern, method = PATTERN_OPS[op]

    from comtypes import COMError
    from pywinauto.uia_defines import NoPatternInterfaceError, get_elem_interface

    try:
        interface = get_elem_interface(element, pattern)
    except NoPatternInterfaceError as error:
        raise ActionError(f"El elemento no admite el patrón {pattern}.") from error
    except (COMError, OSError, AttributeError) as error:
        raise ActionError(f"No se pudo obtener el patrón {pattern}: {error!r}") from error
    try:
        getattr(interface, method)()
    except (COMError, OSError) as error:
        raise ActionError(f"UI Automation no pudo aplicar {pattern}.{method}: {error!r}") from error


def click_at(x: int, y: int) -> None:
    """Clic izquierdo en un punto de la pantalla (píxeles físicos), con SendInput."""
    if type(x) is not int or type(y) is not int:
        raise ActionError(f"Las coordenadas del clic deben ser enteros: ({x!r}, {y!r}).")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext(
            wintypes.HANDLE(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        )
    except (AttributeError, OSError):
        pass  # Windows antiguo, o la conciencia de DPI del proceso ya estaba fijada.

    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int
    left = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    if width <= 1 or height <= 1:
        raise ActionError("No se pudo leer el tamaño del escritorio virtual.")
    if not (left <= x < left + width and top <= y < top + height):
        raise ActionError(f"El punto ({x}, {y}) está fuera del escritorio virtual.")

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    move = (_Input * 1)()
    move[0].type = _INPUT_MOUSE
    move[0].union.mi = _MouseInput(
        dx=round((x - left) * _ABSOLUTE_SCALE / (width - 1)),
        dy=round((y - top) * _ABSOLUTE_SCALE / (height - 1)),
        mouseData=0,
        dwFlags=_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK,
        time=0,
        dwExtraInfo=0,
    )
    if user32.SendInput(len(move), move, ctypes.sizeof(_Input)) < len(move):
        raise ActionError(f"Windows no aceptó mover el cursor (error {ctypes.get_last_error()}).")

    cursor = wintypes.POINT()
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    if not user32.GetCursorPos(ctypes.byref(cursor)):
        raise ActionError(f"No se pudo leer el cursor (error {ctypes.get_last_error()}).")
    if abs(cursor.x - x) > _CURSOR_TOLERANCE_PX or abs(cursor.y - y) > _CURSOR_TOLERANCE_PX:
        raise ActionError(
            f"El cursor quedó en ({cursor.x}, {cursor.y}) y no en ({x}, {y}): no se hace clic."
        )

    press = (_Input * 2)()
    for event, flags in zip(press, (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP), strict=True):
        event.type = _INPUT_MOUSE
        event.union.mi = _MouseInput(dx=0, dy=0, mouseData=0, dwFlags=flags, time=0, dwExtraInfo=0)
    if user32.SendInput(len(press), press, ctypes.sizeof(_Input)) < len(press):
        raise ActionError(f"Windows no aceptó el clic (error {ctypes.get_last_error()}).")


def close_window(handle: int) -> None:
    """Pide a una ventana que se cierre con WM_CLOSE.

    Nunca a la barra de tareas ni al escritorio (``SHELL_WINDOW_CLASSES``).
    """
    if type(handle) is not int or handle <= 0:
        raise ActionError(f"Handle de ventana inválido: {handle!r}")

    import win32gui

    try:
        if not win32gui.IsWindow(handle):
            raise ActionError(f"El handle {handle} no corresponde a una ventana.")
        class_name = win32gui.GetClassName(handle)
        if class_name in SHELL_WINDOW_CLASSES:
            raise ActionError(f"No se cierra la ventana del shell de Windows ({class_name}).")
        win32gui.PostMessage(handle, _WM_CLOSE, 0, 0)
    except win32gui.error as error:
        raise ActionError(f"Windows no pudo cerrar la ventana {handle}: {error}") from error
