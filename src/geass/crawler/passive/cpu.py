"""Medición del uso de CPU del propio proceso, sin dependencias externas.

``time.process_time`` suma el tiempo de CPU de todos los hilos del proceso, así que cubre
el sondeo, la captura y las escuchas.
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CpuUsage:
    """Uso de CPU en un intervalo.

    ``process_fraction`` es la fracción de un núcleo; ``machine_fraction``, la de toda la
    máquina (lo que muestra el Administrador de tareas). El presupuesto
    ``passive.cpu_budget_fraction`` se compara con ``process_fraction``, la más estricta.
    """

    wall_s: float
    cpu_s: float
    logical_cpus: int

    @property
    def process_fraction(self) -> float:
        """Tiempo de CPU entre tiempo de pared. ``0.0`` si no pasó tiempo."""
        if self.wall_s <= 0:
            return 0.0
        return self.cpu_s / self.wall_s

    @property
    def machine_fraction(self) -> float:
        """``process_fraction`` repartida entre todos los procesadores lógicos."""
        return self.process_fraction / self.logical_cpus


class CpuMeter:
    """Mide el uso de CPU del proceso desde ``start``."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.perf_counter,
        cpu_clock: Callable[[], float] = time.process_time,
        logical_cpus: int | None = None,
    ) -> None:
        """
        Args:
            wall_clock: reloj de pared monotónico, en segundos.
            cpu_clock: tiempo de CPU del proceso, en segundos.
            logical_cpus: procesadores lógicos. Por defecto ``os.cpu_count()`` o 1.
        """
        self._wall_clock = wall_clock
        self._cpu_clock = cpu_clock
        self._logical_cpus = logical_cpus if logical_cpus is not None else os.cpu_count() or 1
        if self._logical_cpus < 1:
            raise ValueError("El número de procesadores lógicos debe ser al menos 1.")
        self._start: tuple[float, float] | None = None

    def start(self) -> None:
        """Empieza (o reinicia) la medición."""
        self._start = (self._wall_clock(), self._cpu_clock())

    def read(self) -> CpuUsage:
        """Uso desde ``start``. Lanza ``RuntimeError`` si no se llamó a ``start``."""
        if self._start is None:
            raise RuntimeError("Hay que llamar a CpuMeter.start antes de leer.")
        wall_start, cpu_start = self._start
        return CpuUsage(
            wall_s=self._wall_clock() - wall_start,
            cpu_s=self._cpu_clock() - cpu_start,
            logical_cpus=self._logical_cpus,
        )
