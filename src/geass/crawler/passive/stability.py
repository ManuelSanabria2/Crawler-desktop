"""Detección de estabilidad del árbol de la interfaz.

El árbol se da por estable cuando la misma huella se observa sin cambios durante una
ventana de tiempo. Si eso no ocurre antes del tiempo límite, la espera termina igual.
"""

from collections.abc import Hashable
from typing import Literal

StabilityStatus = Literal["settling", "stable", "timed_out"]


class StabilityTracker:
    """Sigue una secuencia de huellas y decide cuándo dejó de cambiar.

    No tiene reloj propio ni hilos: quien lo usa le pasa la hora de cada observación.
    Los tiempos van en segundos de un reloj monotónico.
    """

    def __init__(self, window_s: float, timeout_s: float, started_at: float) -> None:
        if window_s <= 0 or timeout_s <= 0:
            raise ValueError("La ventana de estabilidad y el tiempo límite deben ser positivos.")
        self.window_s = window_s
        self.timeout_s = timeout_s
        self.started_at = started_at
        self._key: Hashable | None = None
        self._unchanged_since: float | None = None
        self._observations = 0
        self._changes = 0

    @property
    def unchanged_since(self) -> float | None:
        """Hora de la primera observación de la huella actual. ``None`` si no hay huella."""
        return self._unchanged_since

    @property
    def observations(self) -> int:
        return self._observations

    @property
    def changes(self) -> int:
        """Veces que la huella fue distinta de la anterior, sin contar la primera."""
        return self._changes

    def observe(self, key: Hashable | None, now: float) -> StabilityStatus:
        """Registra una observación y devuelve el estado de la espera.

        Args:
            key: huella del árbol. ``None`` significa que no se pudo leer: cuenta como un
                cambio y nunca da estabilidad.
            now: hora de la observación.

        Returns:
            ``stable`` si la huella lleva ``window_s`` sin cambiar; ``timed_out`` si pasó
            ``timeout_s`` desde ``started_at`` sin lograrlo; ``settling`` en otro caso.
            La estabilidad tiene prioridad sobre el tiempo límite.
        """
        self._observations += 1
        if key is None:
            if self._key is not None:
                self._changes += 1
            self._key = None
            self._unchanged_since = None
        elif self._unchanged_since is None or key != self._key:
            if self._unchanged_since is not None:
                self._changes += 1
            self._key = key
            self._unchanged_since = now
        if self._unchanged_since is not None and now - self._unchanged_since >= self.window_s:
            return "stable"
        if now - self.started_at >= self.timeout_s:
            return "timed_out"
        return "settling"
