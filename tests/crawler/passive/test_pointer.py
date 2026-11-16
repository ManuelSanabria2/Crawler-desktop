"""Tests de la captura del clic (casos 6 a 13 de la especificación C04)."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from geass.crawler.passive import (
    MousePress,
    PassiveStats,
    PauseState,
    PressCapture,
    PressCapturer,
    infer_action,
    match_element,
)

MAX_DELAY_S = 0.15


def capturer_for(
    sensor: Any,
    policy: Any,
    clock: Any,
    *,
    pause: PauseState | None = None,
    stats: PassiveStats | None = None,
) -> tuple[PressCapturer, PassiveStats, list[PressCapture]]:
    sink: list[PressCapture] = []
    stats = stats if stats is not None else PassiveStats()
    capturer = PressCapturer(
        sensor,
        policy,
        pause if pause is not None else PauseState(),
        sink.append,
        max_delay_s=MAX_DELAY_S,
        stats=stats,
        clock=clock,
    )
    return capturer, stats, sink


def press_on(fakes: SimpleNamespace, view: Any, name: str, at: float) -> MousePress:
    x, y = fakes.center(fakes.find(view, name))
    return MousePress(x=x, y=y, button="left", at=at)


def test_capture_reads_window_and_element(
    fakes: SimpleNamespace, sensor: Any, policy: Any, clock: Any, views: Any
) -> None:
    sensor.element = fakes.props_of(fakes.find(views.a, "ok"))
    capturer, stats, _ = capturer_for(sensor, policy, clock)
    press = press_on(fakes, views.a, "ok", clock())

    captured = capturer.capture(press)

    assert captured is not None
    assert captured.press == press
    assert captured.window == fakes.WINDOW
    assert captured.element == sensor.element
    assert stats.get("presses_seen") == 1
    assert stats.get("press_elements_read") == 1


def test_capture_in_pause_reads_nothing(
    fakes: SimpleNamespace, sensor: Any, policy: Any, clock: Any, views: Any
) -> None:
    capturer, stats, _ = capturer_for(sensor, policy, clock, pause=PauseState(paused=True))
    assert capturer.capture(press_on(fakes, views.a, "ok", clock())) is None
    assert sensor.window_at_calls == 0
    assert sensor.element_at_calls == 0
    assert stats.get("presses_dropped_paused") == 1


def test_capture_on_an_excluded_window_reads_no_element(
    fakes: SimpleNamespace, sensor: Any, clock: Any, views: Any
) -> None:
    sensor.element = fakes.props_of(fakes.find(views.a, "ok"))
    policy = fakes.FakePolicy(excluded=[fakes.WINDOW.process_name])
    capturer, stats, _ = capturer_for(sensor, policy, clock)

    captured = capturer.capture(press_on(fakes, views.a, "ok", clock()))

    assert captured is not None
    assert captured.window is None
    assert captured.element is None
    assert sensor.element_at_calls == 0
    assert stats.get("presses_blocked_privacy") == 1


def test_capture_on_an_unidentified_window_reads_no_element(
    fakes: SimpleNamespace, sensor: Any, policy: Any, clock: Any, views: Any
) -> None:
    sensor.point_window = replace(fakes.WINDOW, process_name="")
    sensor.element = fakes.props_of(fakes.find(views.a, "ok"))
    capturer, stats, _ = capturer_for(sensor, policy, clock)

    captured = capturer.capture(press_on(fakes, views.a, "ok", clock()))

    assert captured is not None
    assert captured.window is None
    assert captured.element is None
    assert sensor.element_at_calls == 0
    assert stats.get("presses_unidentified_window") == 1
    assert stats.get("presses_blocked_privacy") == 0


def test_capture_drops_password_fields_but_keeps_the_window(
    fakes: SimpleNamespace, sensor: Any, clock: Any, views: Any
) -> None:
    sensor.element = fakes.props_of(fakes.find(views.a, "ok"), is_password=True)
    policy = fakes.FakePolicy(allow_passwords=False)
    capturer, stats, _ = capturer_for(sensor, policy, clock)

    captured = capturer.capture(press_on(fakes, views.a, "ok", clock()))

    assert captured is not None
    assert captured.window == fakes.WINDOW
    assert captured.element is None
    assert stats.get("press_elements_blocked_privacy") == 1
    assert stats.get("press_elements_read") == 0


def test_capture_drops_a_late_element(
    fakes: SimpleNamespace, sensor: Any, policy: Any, clock: Any, views: Any
) -> None:
    sensor.element = fakes.props_of(fakes.find(views.a, "ok"))
    sensor.on_element_at = lambda: clock.advance(0.2)
    capturer, stats, _ = capturer_for(sensor, policy, clock)

    captured = capturer.capture(press_on(fakes, views.a, "ok", clock()))

    assert captured is not None
    assert captured.window == fakes.WINDOW
    assert captured.element is None
    assert captured.delay_s == pytest.approx(0.2)
    assert stats.get("press_elements_late") == 1
    assert stats.get("press_elements_read") == 0


def test_match_element_prefers_the_deepest_and_needs_point_and_name(
    fakes: SimpleNamespace,
) -> None:
    make = fakes.make_element
    inner = make("Button", "x", (10, 10, 50, 50), "Window[0]/Pane[0]/Button[0]")
    pane = make("Pane", "", (0, 0, 300, 300), "Window[0]/Pane[0]", children=[inner])
    outer = make("Button", "x", (0, 0, 200, 200), "Window[0]/Button[0]")
    view = fakes.make_dump([])
    view = view.model_copy(
        update={"root": view.root.model_copy(update={"children": [outer, pane]})}
    )
    props = fakes.props_of(inner)

    assert match_element(view, props, 20, 20) == inner
    assert match_element(view, props, 150, 150) == outer
    assert match_element(view, props, 500, 500) is None
    assert match_element(view, replace(props, name="y"), 20, 20) is None


@pytest.mark.parametrize(
    ("button", "patterns", "expected"),
    [
        ("left", ("Invoke",), "invoke"),
        ("left", ("Toggle", "Invoke"), "invoke"),
        ("left", ("ExpandCollapse",), "expand_collapse"),
        ("left", ("SelectionItem",), "select"),
        ("left", (), "coordinate_click"),
        ("left", None, "unknown"),
        ("right", ("Invoke",), "unknown"),
    ],
)
def test_infer_action(button: str, patterns: tuple[str, ...] | None, expected: str) -> None:
    assert infer_action(button, patterns) == expected
