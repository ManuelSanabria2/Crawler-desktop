"""Tests del atajo global de pausa (casos 39 a 42 de la especificación C04).

El caso 41 registra de verdad una combinación poco usada y la retira enseguida. No se
simula ninguna pulsación.
"""

import sys

import pytest

from geass.crawler.passive import HotkeyError, PauseHotkey, parse_hotkey


def test_parse_valid_hotkeys() -> None:
    assert parse_hotkey("ctrl+alt+p") == (0x0003, 0x50)
    assert parse_hotkey(" Ctrl + Shift + F5 ") == (0x0006, 0x74)
    assert parse_hotkey("win+1") == (0x0008, 0x31)
    assert parse_hotkey("alt+f24") == (0x0001, 0x87)


@pytest.mark.parametrize(
    "text", ["p", "shift+p", "ctrl+alt", "ctrl+p+q", "ctrl+ñ", "", "ctrl+ctrl+p", "ctrl+f25"]
)
def test_parse_invalid_hotkeys(text: str) -> None:
    with pytest.raises(HotkeyError) as caught:
        parse_hotkey(text)
    assert f"'{text}'" in str(caught.value)


def test_invalid_hotkey_fails_on_creation() -> None:
    with pytest.raises(HotkeyError):
        PauseHotkey("shift+p", lambda: None)


@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey solo existe en Windows")
def test_register_and_unregister_a_real_hotkey() -> None:
    hotkey = PauseHotkey("ctrl+alt+shift+f24", lambda: None)
    try:
        hotkey.start()
    except HotkeyError:
        pytest.skip("La combinación ctrl+alt+shift+f24 ya está en uso por otra aplicación.")
    thread = hotkey._thread
    assert thread is not None and thread.is_alive()
    hotkey.stop()
    assert not thread.is_alive()
    assert hotkey._thread is None


def test_stop_without_start_does_nothing() -> None:
    PauseHotkey("ctrl+alt+p", lambda: None).stop()
