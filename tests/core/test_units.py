"""Tests de las conversiones de unidades."""

import math

import numpy as np
import pytest

from geass.core.units import deg_to_rad, m_to_mm, mm_to_m, rad_to_deg


@pytest.mark.parametrize(
    ("degrees", "radians"),
    [(0.0, 0.0), (180.0, math.pi), (90.0, math.pi / 2), (-45.0, -math.pi / 4), (3.0, 0.05235988)],
)
def test_degrees_and_radians(degrees: float, radians: float) -> None:
    assert deg_to_rad(degrees) == pytest.approx(radians, abs=1e-8)
    assert rad_to_deg(radians) == pytest.approx(degrees, abs=1e-6)


@pytest.mark.parametrize(("mm", "m"), [(0.0, 0.0), (1000.0, 1.0), (2.0, 0.002), (-35.5, -0.0355)])
def test_millimeters_and_meters(mm: float, m: float) -> None:
    assert mm_to_m(mm) == pytest.approx(m)
    assert m_to_mm(m) == pytest.approx(mm)


def test_round_trips() -> None:
    for value in (-170.0, -1.5, 0.0, 0.25, 33.3, 179.99):
        assert rad_to_deg(deg_to_rad(value)) == pytest.approx(value, abs=1e-12)
        assert m_to_mm(mm_to_m(value)) == pytest.approx(value, abs=1e-12)


def test_numpy_arrays_keep_shape() -> None:
    degrees = np.array([[0.0, 90.0, 180.0], [-90.0, 45.0, 360.0]])
    radians = deg_to_rad(degrees)
    assert isinstance(radians, np.ndarray)
    assert radians.shape == degrees.shape
    np.testing.assert_allclose(radians, np.deg2rad(degrees), atol=1e-15)
    np.testing.assert_allclose(rad_to_deg(radians), degrees, atol=1e-12)

    millimeters = np.array([1.0, 250.0, 1000.0])
    np.testing.assert_allclose(mm_to_m(millimeters), [0.001, 0.25, 1.0])
    np.testing.assert_allclose(m_to_mm(mm_to_m(millimeters)), millimeters)


def test_input_array_is_not_modified() -> None:
    degrees = np.array([10.0, 20.0])
    deg_to_rad(degrees)
    np.testing.assert_array_equal(degrees, [10.0, 20.0])
