"""Tests de los filtros de privacidad y de los atajos (casos 29 a 38 de la especificación C04).

Se usan las clases reales ``Key`` y ``KeyCode`` de pynput: importarlas no arranca ninguna
escucha. ``ShortcutListener`` nunca se arranca: se prueban sus avisos llamándolos directamente.
"""

from collections.abc import Iterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.errors import ConfigIncompleteError
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.passive import (
    ConfigPrivacyPolicy,
    PauseState,
    ShortcutListener,
    ShortcutPressed,
    ShortcutTracker,
    key_name,
)

keyboard = pytest.importorskip("pynput.keyboard")
Key, KeyCode = keyboard.Key, keyboard.KeyCode


def test_excluded_apps_are_compared_without_case_or_spaces(fakes: SimpleNamespace) -> None:
    policy = ConfigPrivacyPolicy(["KeePass.exe "])
    assert not policy.allows_window(replace(fakes.WINDOW, process_name="keepass.exe"))
    assert not policy.allows_window(replace(fakes.WINDOW, process_name="KEEPASS.EXE"))
    assert policy.allows_window(replace(fakes.WINDOW, process_name="notepad.exe"))


def test_unidentified_windows_are_rejected(fakes: SimpleNamespace) -> None:
    policy = ConfigPrivacyPolicy([])
    assert policy.allows_window(fakes.WINDOW)
    assert not policy.allows_window(replace(fakes.WINDOW, process_name=""))
    assert not policy.allows_window(replace(fakes.WINDOW, handle=0))


def test_password_fields_are_rejected(fakes: SimpleNamespace, views: Any) -> None:
    policy = ConfigPrivacyPolicy([])
    ok = fakes.find(views.a, "ok")
    assert not policy.allows_press_element(fakes.props_of(ok, is_password=True))
    assert policy.allows_press_element(fakes.props_of(ok, is_password=False))


def test_from_config_uses_the_exclusion_list(
    crawler_config: CrawlerConfig, fakes: SimpleNamespace
) -> None:
    policy = ConfigPrivacyPolicy.from_config(crawler_config)
    assert crawler_config.apps.excluded
    for name in crawler_config.apps.excluded:
        assert not policy.allows_window(replace(fakes.WINDOW, process_name=name.upper()))


def test_from_config_fails_if_the_exclusion_list_is_todo(crawler_config: CrawlerConfig) -> None:
    pending_apps = type(crawler_config.apps).model_validate(
        {"excluded": "TODO", "priority": "TODO"}
    )
    pending = crawler_config.model_copy(update={"apps": pending_apps})
    with pytest.raises(ConfigIncompleteError):
        ConfigPrivacyPolicy.from_config(pending)


def press_all(tracker: ShortcutTracker, names: list[str]) -> list[ShortcutPressed | None]:
    return [tracker.press(name, float(index)) for index, name in enumerate(names)]


def test_a_single_key_is_not_a_shortcut() -> None:
    assert ShortcutTracker().press("s", 0.0) is None


def test_shift_alone_is_not_a_shortcut() -> None:
    assert press_all(ShortcutTracker(), ["shift", "s"]) == [None, None]


def test_ctrl_and_a_key_is_a_shortcut() -> None:
    *_, shortcut = press_all(ShortcutTracker(), ["ctrl", "s"])
    assert shortcut == ShortcutPressed(combo="ctrl+s", at=1.0)


def test_combo_has_a_canonical_order() -> None:
    *_, shortcut = press_all(ShortcutTracker(), ["shift", "ctrl", "s"])
    assert shortcut is not None
    assert shortcut.combo == "ctrl+shift+s"
    *_, shortcut = press_all(ShortcutTracker(), ["win", "shift", "alt", "ctrl", "f5"])
    assert shortcut is not None
    assert shortcut.combo == "ctrl+alt+shift+win+f5"


def test_alt_gr_never_gives_a_shortcut() -> None:
    assert press_all(ShortcutTracker(), ["ctrl", "alt_gr", "q"]) == [None, None, None]


def test_autorepeat_gives_a_single_shortcut() -> None:
    tracker = ShortcutTracker()
    results = press_all(tracker, ["ctrl", "s", "s", "s"])
    assert [result.combo if result else None for result in results] == [
        None,
        "ctrl+s",
        None,
        None,
    ]
    tracker.release("s")
    again = tracker.press("s", 9.0)
    assert again == ShortcutPressed(combo="ctrl+s", at=9.0)


def _stored_strings(tracker: ShortcutTracker) -> Iterator[str]:
    for value in vars(tracker).values():
        if isinstance(value, str):
            yield value
        elif isinstance(value, set | frozenset | list | tuple):
            yield from (item for item in value if isinstance(item, str))


def test_single_keys_are_not_kept_anywhere() -> None:
    tracker = ShortcutTracker()
    assert press_all(tracker, ["a", "b"]) == [None, None]
    stored = list(_stored_strings(tracker))
    assert "a" not in stored
    assert "b" not in stored

    tracker.press("ctrl", 3.0)
    tracker.press("x", 4.0)
    tracker.reset()
    assert list(_stored_strings(tracker)) == []


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (KeyCode(vk=0x53, char="\x13"), "s"),
        (KeyCode(vk=0x35, char="5"), "5"),
        (KeyCode(vk=0xBF, char="#"), "vk191"),
        (KeyCode(char="s"), None),
        (Key.ctrl_l, "ctrl"),
        (Key.ctrl_r, "ctrl"),
        (Key.alt_l, "alt"),
        (Key.alt_gr, "alt_gr"),
        (Key.alt_r, "alt_gr"),
        (Key.shift_r, "shift"),
        (Key.cmd, "win"),
        (Key.f5, "f5"),
        (Key.enter, "enter"),
    ],
)
def test_key_name(key: Any, expected: str | None) -> None:
    assert key_name(key) == expected


def test_key_name_with_simple_doubles() -> None:
    assert key_name(SimpleNamespace(name="ctrl_l")) == "ctrl"
    assert key_name(SimpleNamespace(vk=0x41, char="\x01")) == "a"
    assert key_name(SimpleNamespace(vk=None)) is None
    assert key_name(object()) is None


def test_shortcut_listener_callbacks_deliver_only_shortcuts() -> None:
    delivered: list[ShortcutPressed] = []
    pause = PauseState()
    listener = ShortcutListener(delivered.append, pause=pause, clock=lambda: 7.0)

    listener._on_press(KeyCode(vk=0x53, char="s"))
    listener._on_press(Key.ctrl_l)
    listener._on_press(KeyCode(vk=0x53, char="\x13"))
    listener._on_release(KeyCode(vk=0x53, char="\x13"))
    listener._on_release(Key.ctrl_l)
    assert delivered == [ShortcutPressed(combo="ctrl+s", at=7.0)]

    pause.set_paused(True)
    listener._on_press(Key.ctrl_l)
    listener._on_press(KeyCode(vk=0x53, char="\x13"))
    assert len(delivered) == 1

    pause.set_paused(False)
    listener._on_press(KeyCode(vk=0x53, char="s"))
    assert len(delivered) == 1


def test_shortcut_listener_callbacks_do_not_raise() -> None:
    def broken(_: ShortcutPressed) -> None:
        raise RuntimeError("fallo del destino")

    listener = ShortcutListener(broken, pause=PauseState())
    listener._on_press(Key.ctrl_l)
    listener._on_press(KeyCode(vk=0x53, char="\x13"))
    listener._on_release(KeyCode(vk=0x53, char="\x13"))
    listener.stop()
