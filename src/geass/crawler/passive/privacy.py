"""Filtros de privacidad y captura de atajos de teclado del crawler pasivo.

- ``ConfigPrivacyPolicy`` aplica la lista de apps excluidas de ``crawler.yaml`` y descarta
  los campos de contraseña. No hay nada que filtrar sobre valores de texto: el lector UIA
  no los lee (``NodeProps`` no tiene ningún campo para ellos).
- ``ShortcutTracker`` decide qué pulsaciones son un atajo. Una tecla suelta nunca lo es y
  nunca se guarda.
- ``ShortcutListener`` escucha el teclado con pynput. Solo se crea si
  ``passive.capture_shortcuts`` es verdadero; si no, el proceso no observa ninguna tecla.

Este módulo solo escucha: nunca genera pulsaciones.
"""

import string
import time
from collections.abc import Callable, Sequence
from typing import Any

from geass.core.log import get_logger
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.passive.control import PauseState
from geass.crawler.passive.events import ShortcutPressed, WindowRef
from geass.crawler.uia.reader import NodeProps

logger = get_logger(__name__)

MODIFIER_NAMES: tuple[str, ...] = ("ctrl", "alt", "shift", "win")
ALT_GR = "alt_gr"

# Modificadores que convierten una tecla en atajo. Shift solo no cuenta: es escritura.
_SHORTCUT_MODIFIERS = frozenset({"ctrl", "alt", "win"})
_HELD_NAMES = frozenset({*MODIFIER_NAMES, ALT_GR})

# Códigos de tecla virtual de Windows de los dígitos y las letras: coinciden con su ASCII.
_VK_DIGITS = frozenset(ord(char) for char in string.digits)
_VK_LETTERS = frozenset(ord(char) for char in string.ascii_uppercase)

_KEY_ALIASES: dict[str, str] = {
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "alt": "alt",
    "alt_l": "alt",
    "alt_gr": ALT_GR,
    "alt_r": ALT_GR,
}


class ConfigPrivacyPolicy:
    """Implementa el protocolo PrivacyPolicy con las apps excluidas de crawler.yaml."""

    def __init__(self, excluded_apps: Sequence[str]) -> None:
        """
        Args:
            excluded_apps: ejecutables de los que no se lee ni registra nada. Se comparan
                sin distinguir mayúsculas y sin espacios sobrantes.
        """
        self._excluded = frozenset(name.strip().lower() for name in excluded_apps)

    @classmethod
    def from_config(cls, config: CrawlerConfig) -> "ConfigPrivacyPolicy":
        """Política con ``apps.excluded``. Si sigue como TODO, lanza ``ConfigIncompleteError``."""
        return cls(config.apps.excluded)

    def allows_window(self, window: WindowRef) -> bool:
        """Falso si no se sabe con certeza qué ventana es o si su app está excluida."""
        if not window.identified:
            return False
        return window.process_name.strip().lower() not in self._excluded

    def allows_press_element(self, element: NodeProps) -> bool:
        """Falso si el elemento es un campo de contraseña."""
        return not element.is_password


class ShortcutTracker:
    """Decide qué pulsaciones son un atajo. Sin hilos ni pynput.

    Solo guarda el conjunto de modificadores presionados y el nombre de la tecla del último
    atajo emitido (para no repetirlo con la autorrepetición). Una tecla sin modificador
    válido no se guarda en ningún sitio.
    """

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._repeating: str | None = None

    def press(self, name: str, at: float) -> ShortcutPressed | None:
        """Anota una tecla presionada. Devuelve el atajo si lo es, o ``None``.

        Args:
            name: nombre canónico de la tecla (ver ``key_name``).
            at: hora de la pulsación en el reloj del observador.
        """
        if name in _HELD_NAMES:
            self._held.add(name)
            return None
        if ALT_GR in self._held or not (self._held & _SHORTCUT_MODIFIERS):
            return None
        if name == self._repeating:
            return None
        self._repeating = name
        parts = [modifier for modifier in MODIFIER_NAMES if modifier in self._held]
        parts.append(name)
        return ShortcutPressed(combo="+".join(parts), at=at)

    def release(self, name: str) -> None:
        """Anota una tecla soltada."""
        if name in _HELD_NAMES:
            self._held.discard(name)
        elif name == self._repeating:
            self._repeating = None

    def reset(self) -> None:
        """Olvida todo lo anotado."""
        self._held.clear()
        self._repeating = None


def key_name(key: Any) -> str | None:
    """Nombre canónico de una tecla de pynput, o None si no se reconoce.

    Las teclas especiales (``Key``) se identifican por su nombre. Las normales (``KeyCode``)
    por su código de tecla virtual, nunca por su carácter: con Ctrl presionado el carácter
    es un carácter de control.
    """
    name = getattr(key, "name", None)
    if isinstance(name, str):
        if not name:
            return None
        if name in _KEY_ALIASES:
            return _KEY_ALIASES[name]
        if name.startswith("shift"):
            return "shift"
        if name.startswith("cmd"):
            return "win"
        return name
    vk = getattr(key, "vk", None)
    if vk is None:
        return None
    code = int(vk)
    if code in _VK_DIGITS or code in _VK_LETTERS:
        return chr(code).lower()
    return f"vk{code}"


class ShortcutListener:
    """Escucha del teclado con pynput. Solo existe si capture_shortcuts es verdadero."""

    def __init__(
        self,
        sink: Callable[[ShortcutPressed], None],
        *,
        pause: PauseState,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """
        Args:
            sink: destino de los atajos; normalmente ``PassiveObserver.post``.
            pause: interruptor de pausa. En pausa no se entrega nada.
            clock: el mismo reloj que usa el observador.
        """
        self._sink = sink
        self._pause = pause
        self._clock = clock
        self._tracker = ShortcutTracker()
        self._listener: Any = None

    def start(self) -> None:
        if self._listener is not None:
            raise RuntimeError("La escucha de atajos ya está en marcha.")
        from pynput import keyboard

        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        listener.stop()
        self._listener = None
        self._tracker.reset()

    def _on_press(self, key: Any) -> None:
        # Corre dentro del gancho de teclado de Windows: nada lento y ninguna excepción fuera.
        try:
            if self._pause.is_paused():
                self._tracker.reset()
                return
            name = key_name(key)
            if name is None:
                return
            shortcut = self._tracker.press(name, self._clock())
            if shortcut is not None:
                self._sink(shortcut)
        except Exception:
            logger.exception("Falló el aviso de una pulsación; se descarta.")

    def _on_release(self, key: Any) -> None:
        try:
            if self._pause.is_paused():
                self._tracker.reset()
                return
            name = key_name(key)
            if name is not None:
                self._tracker.release(name)
        except Exception:
            logger.exception("Falló el aviso de una tecla soltada; se descarta.")
