"""Tests de la medición de CPU (casos 43 y 44 de la especificación C04)."""

import pytest

from geass.crawler.passive import CpuMeter, CpuUsage


class SequenceClock:
    """Reloj falso que devuelve los valores dados, uno por llamada."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)

    def __call__(self) -> float:
        return self._values.pop(0)


def test_cpu_fractions_with_fake_clocks() -> None:
    meter = CpuMeter(
        wall_clock=SequenceClock(10.0, 20.0),
        cpu_clock=SequenceClock(1.0, 1.4),
        logical_cpus=8,
    )
    meter.start()
    usage = meter.read()
    assert usage.wall_s == pytest.approx(10.0)
    assert usage.cpu_s == pytest.approx(0.4)
    assert usage.process_fraction == pytest.approx(0.04)
    assert usage.machine_fraction == pytest.approx(0.005)


def test_read_before_start_fails() -> None:
    with pytest.raises(RuntimeError):
        CpuMeter().read()


def test_no_elapsed_time_gives_zero_fractions() -> None:
    usage = CpuUsage(wall_s=0.0, cpu_s=0.3, logical_cpus=4)
    assert usage.process_fraction == 0.0
    assert usage.machine_fraction == 0.0
    meter = CpuMeter(wall_clock=lambda: 5.0, cpu_clock=lambda: 1.0, logical_cpus=2)
    meter.start()
    assert meter.read().process_fraction == 0.0


def test_default_logical_cpus_is_at_least_one() -> None:
    meter = CpuMeter()
    meter.start()
    assert meter.read().logical_cpus >= 1
