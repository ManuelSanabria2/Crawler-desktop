"""Tests de la CLI del crawler pasivo (casos 51 a 53 de la especificación C04).

Ninguno arranca el crawler: ``start`` se prueba solo con un valor inválido.
"""

import ctypes
import sys

import pytest
from typer.testing import CliRunner

from geass.crawler.__main__ import app
from geass.crawler.passive import STOP_EVENT_NAME
from geass.crawler.passive import runner as runner_module

_SYNCHRONIZE = 0x00100000


def passive_running() -> bool:
    """Mira si existe el evento de parada sin activarlo: lo abre y lo cierra."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.OpenEventW.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenEventW(_SYNCHRONIZE, False, STOP_EVENT_NAME)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def test_passive_help_lists_the_commands() -> None:
    result = CliRunner().invoke(app, ["passive", "--help"])
    assert result.exit_code == 0
    for command in ("start", "stop", "stats"):
        assert command in result.output


@pytest.mark.skipif(sys.platform != "win32", reason="eventos con nombre de Win32")
def test_passive_stop_without_a_running_crawler() -> None:
    if passive_running():
        pytest.skip("Hay un crawler pasivo real en marcha; no se toca.")
    result = CliRunner().invoke(app, ["passive", "stop"])
    assert result.exit_code == 1
    assert "No hay ningún crawler pasivo en marcha." in result.output


@pytest.mark.parametrize("duration", ["0", "-5"])
def test_passive_start_rejects_a_non_positive_duration(
    duration: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("No debía crearse ningún PassiveRunner.")

    monkeypatch.setattr(runner_module, "PassiveRunner", forbidden)
    result = CliRunner().invoke(app, ["passive", "start", "--duration", duration])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
