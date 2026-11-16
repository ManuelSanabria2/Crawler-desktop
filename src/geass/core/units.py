"""Únicas conversiones de unidades permitidas en GEASS.

Las unidades internas son SI: metros, radianes y segundos. Grados y milímetros solo
aparecen en los bordes (protocolo con el ESP32, reportes legibles y CLI), y siempre
se convierten con las funciones de este módulo.
"""

import math
from typing import TypeVar

import numpy as np
from numpy.typing import NDArray

Value = TypeVar("Value", float, NDArray[np.float64])

RAD_PER_DEG = math.pi / 180.0
MM_PER_M = 1000.0


def deg_to_rad(value: Value) -> Value:
    """Convierte grados a radianes. Acepta un número o un arreglo de NumPy."""
    return value * RAD_PER_DEG


def rad_to_deg(value: Value) -> Value:
    """Convierte radianes a grados. Acepta un número o un arreglo de NumPy."""
    return value / RAD_PER_DEG


def mm_to_m(value: Value) -> Value:
    """Convierte milímetros a metros. Acepta un número o un arreglo de NumPy."""
    return value / MM_PER_M


def m_to_mm(value: Value) -> Value:
    """Convierte metros a milímetros. Acepta un número o un arreglo de NumPy."""
    return value * MM_PER_M
