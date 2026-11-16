"""Atajo global de pausa y reanudación del crawler pasivo.

Usa ``RegisterHotKey`` de Win32 y no un gancho de teclado. Así, con
``capture_shortcuts: false``, el proceso no observa ninguna tecla: Windows solo le avisa
cuando se pulsa exactamente esa combinación.

El atajo se registra en un hilo propio, que bombea sus mensajes con ``GetMessageW``. Solo
escucha: nunca genera pulsaciones.
"""

import ctypes
import functools
import re
import string
import threading
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from geass.core.errors import GeassError
from geass.core.log import get_logger

logger = get_logger(__name__)

# Constantes de Win32 (winuser.h).
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
_PM_NOREMOVE = 0x0000
_VK_F1 = 0x70
_MAX_FUNCTION_KEY = 24

# Identificador del atajo dentro del hilo que lo registra.
_HOTKEY_ID = 1
_START_TIMEOUT_S = 5.0
_JOIN_TIMEOUT_S = 5.0

_MODIFIERS: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}
_REQUIRED_MODIFIERS = MOD_CONTROL | MOD_ALT | MOD_WIN
_FUNCTION_KEY = re.compile(r"f([1-9]\d?)")


class HotkeyError(GeassError):
    """El atajo global no es válido o no se pudo registrar."""


def parse_hotkey(text: str) -> tuple[int, int]:
    """Convierte "ctrl+alt+p" en (modificadores MOD_*, código de tecla virtual).

    Partes separadas por ``+``, sin distinguir mayúsculas y con espacios tolerados.
    Modificadores: ``ctrl``, ``alt``, ``shift`` y ``win``; al menos uno de ``ctrl``, ``alt``
    o ``win``. Exactamente una tecla: letra de la ``a`` a la ``z``, dígito o ``f1`` a ``f24``.

    Raises:
        HotkeyError: el texto no cumple esas reglas.
    """
    modifiers = 0
    keys: list[str] = []
    for part in (piece.strip().lower() for piece in text.split("+")):
        flag = _MODIFIERS.get(part)
        if flag is None:
            keys.append(part)
        elif modifiers & flag:
            raise _invalid(text, f"el modificador '{part}' está repetido")
        else:
            modifiers |= flag
    if len(keys) != 1:
        raise _invalid(text, "debe haber exactamente una tecla además de los modificadores")
    vk = _virtual_key(keys[0])
    if vk is None:
        raise _invalid(text, "la tecla debe ser una letra de la a a la z, un dígito o de f1 a f24")
    if not modifiers & _REQUIRED_MODIFIERS:
        raise _invalid(text, "debe incluir ctrl, alt o win")
    return modifiers, vk


def _invalid(text: str, reason: str) -> HotkeyError:
    return HotkeyError(f"Atajo de pausa no válido: '{text}': {reason}.")


def _virtual_key(key: str) -> int | None:
    if len(key) == 1 and (key in string.ascii_lowercase or key in string.digits):
        return ord(key.upper())
    match = _FUNCTION_KEY.fullmatch(key)
    if match is not None:
        number = int(match.group(1))
        if 1 <= number <= _MAX_FUNCTION_KEY:
            return _VK_F1 + number - 1
    return None


class PauseHotkey:
    """Atajo global mediante RegisterHotKey de Win32, en un hilo propio."""

    def __init__(self, hotkey: str, on_trigger: Callable[[], None]) -> None:
        """
        Args:
            hotkey: combinación, por ejemplo ``ctrl+alt+p`` (``passive.pause_hotkey``).
            on_trigger: se llama desde el hilo del atajo cada vez que se pulsa.

        Raises:
            HotkeyError: la combinación no es válida.
        """
        self._text = hotkey
        self._modifiers, self._vk = parse_hotkey(hotkey)
        self._on_trigger = on_trigger
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._registered = False

    def start(self) -> None:
        """Registra el atajo. Vuelve cuando se sabe si el registro funcionó.

        Raises:
            HotkeyError: el atajo ya está en uso o Windows no lo aceptó.
        """
        if self._thread is not None:
            raise RuntimeError("El atajo de pausa ya está en marcha.")
        self._ready.clear()
        self._registered = False
        self._thread_id = 0
        self._thread = threading.Thread(target=self._run, name="geass-passive-hotkey", daemon=True)
        self._thread.start()
        if not self._ready.wait(_START_TIMEOUT_S) or not self._registered:
            self.stop()
            raise HotkeyError(
                f"No se pudo registrar el atajo de pausa '{self._text}': el atajo ya está en "
                "uso por otra aplicación o no se pudo registrar."
            )

    def stop(self) -> None:
        """Pide al hilo que termine y espera a que lo haga. Sin ``start`` no hace nada."""
        thread = self._thread
        if thread is None:
            return
        if thread.is_alive() and self._thread_id:
            posted = _user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            if not posted:
                logger.warning(
                    "No se pudo avisar al hilo del atajo de pausa (error %d).",
                    ctypes.get_last_error(),
                )
        thread.join(_JOIN_TIMEOUT_S)
        self._thread = None

    def _run(self) -> None:
        user32 = _user32()
        message = wintypes.MSG()
        try:
            # Crea la cola de mensajes del hilo antes de que nadie le envíe WM_QUIT.
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, _PM_NOREMOVE)
            self._thread_id = int(_kernel32().GetCurrentThreadId())
            flags = self._modifiers | MOD_NOREPEAT
            self._registered = bool(user32.RegisterHotKey(None, _HOTKEY_ID, flags, self._vk))
            if not self._registered:
                logger.warning(
                    "RegisterHotKey rechazó el atajo de pausa (error %d).", ctypes.get_last_error()
                )
        finally:
            self._ready.set()
        if not self._registered:
            return
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    return
                if result == -1:
                    logger.error(
                        "Falló la espera de mensajes del atajo de pausa (error %d).",
                        ctypes.get_last_error(),
                    )
                    return
                if message.message == WM_HOTKEY and message.wParam == _HOTKEY_ID:
                    self._fire()
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)

    def _fire(self) -> None:
        try:
            self._on_trigger()
        except Exception:
            logger.exception("Falló la acción del atajo de pausa.")


@functools.cache
def _user32() -> Any:
    """``user32.dll`` con los prototipos que usa este módulo. Solo existe en Windows."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    message_pointer = ctypes.POINTER(wintypes.MSG)
    user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = (message_pointer, wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.GetMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = (
        message_pointer,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    )
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = (
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.PostThreadMessageW.restype = wintypes.BOOL
    return user32


@functools.cache
def _kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThreadId.argtypes = ()
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    return kernel32
