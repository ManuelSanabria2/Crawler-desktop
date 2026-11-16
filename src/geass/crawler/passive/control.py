"""Estado compartido entre los hilos del observador pasivo: pausa y contadores."""

import threading

COUNTER_NAMES: tuple[str, ...] = (
    # Hilo de captura
    "presses_seen",
    "presses_dropped_paused",
    "presses_blocked_privacy",
    "presses_unidentified_window",
    "press_elements_read",
    "press_elements_late",
    "press_elements_blocked_privacy",
    "capture_errors",
    # Hilo de sondeo
    "samples",
    "sample_errors",
    "skipped_uncertain_window",
    "skipped_excluded_window",
    "skipped_partial_dump",
    "settles_started",
    "settles_stable",
    "settles_timed_out",
    "triggers_interrupted",
    "triggers_unattributed",
    "triggers_without_change",
    "shortcuts_seen",
    "hints_seen",
    "states_recorded",
    "states_new",
    "transitions_recorded",
    "transitions_with_element",
    "store_errors",
    "loop_errors",
)


class PauseState:
    """Interruptor de pausa. Se puede leer y cambiar desde cualquier hilo."""

    def __init__(self, paused: bool = False) -> None:
        self._lock = threading.Lock()
        self._paused = paused
        self._generation = 0

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            if paused != self._paused:
                self._paused = paused
                self._generation += 1

    def toggle(self) -> bool:
        """Cambia entre pausa y marcha. Devuelve el estado nuevo: verdadero si quedó en pausa."""
        with self._lock:
            self._paused = not self._paused
            self._generation += 1
            return self._paused

    @property
    def generation(self) -> int:
        """Número de cambios de estado hasta ahora. Sirve para notar una pausa ya terminada."""
        with self._lock:
            return self._generation


class PassiveStats:
    """Contadores del observador. Solo números: ningún nombre de app ni de elemento."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, int] = dict.fromkeys(COUNTER_NAMES, 0)
        self._dump_time_s = 0.0

    def increment(self, name: str, amount: int = 1) -> None:
        """Suma a un contador. Lanza ``KeyError`` si el nombre no está en ``COUNTER_NAMES``."""
        with self._lock:
            self._counts[name] = self._counts[name] + amount

    def add_dump_time(self, seconds: float) -> None:
        with self._lock:
            self._dump_time_s += seconds

    def get(self, name: str) -> int:
        with self._lock:
            return self._counts[name]

    def snapshot(self) -> dict[str, float]:
        """Copia de todos los contadores, más ``dump_time_s`` (segundos acumulados volcando)."""
        with self._lock:
            result: dict[str, float] = dict(self._counts)
            result["dump_time_s"] = self._dump_time_s
            return result
