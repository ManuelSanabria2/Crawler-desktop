"""Tests de la verificación (casos 27 a 32 de la especificación C08a)."""

from types import SimpleNamespace

import pytest

from geass.core.config import load_crawler_config
from geass.crawler.actions import (
    Baseline,
    ElementExpectation,
    Expectation,
    capture_baseline,
    expectation_met,
    ui_fingerprint,
    wait_until,
)
from geass.crawler.uia import UiaError

EXCLUDED = load_crawler_config().apps.excluded


def empty_baseline(handles: frozenset[int] = frozenset()) -> Baseline:
    return Baseline(
        foreground_handle=None,
        window_handles=handles,
        process_handles=frozenset(),
        fingerprint=None,
        state=None,
    )


def test_fingerprint(fakes: SimpleNamespace) -> None:
    desktop = fakes.calc_world()
    window = desktop.foreground()
    first = ui_fingerprint(desktop.snapshot(window))
    desktop.window(fakes.CALC).elements.reverse()
    assert ui_fingerprint(desktop.snapshot(window)) == first
    desktop.get("Se muestra 0").rename("Se muestra 2")
    renamed = ui_fingerprint(desktop.snapshot(window))
    assert renamed != first
    desktop.get("Dos").change(enabled=False)
    assert ui_fingerprint(desktop.snapshot(window)) != renamed


def test_wait_until_polls(fakes: SimpleNamespace) -> None:
    clock = fakes.FakeClock()
    calls = iter([False, False, True])
    assert wait_until(
        lambda: next(calls), timeout_s=3.0, poll_s=0.2, clock=clock.clock, sleep=clock.sleep
    )
    assert len(clock.sleeps) == 2

    clock = fakes.FakeClock()
    assert not wait_until(
        lambda: False, timeout_s=3.0, poll_s=0.2, clock=clock.clock, sleep=clock.sleep
    )
    assert clock.now >= 3.0


def test_wait_until_read_errors_count_as_false(fakes: SimpleNamespace) -> None:
    clock = fakes.FakeClock()
    count = {"n": 0}

    def flaky() -> bool:
        count["n"] += 1
        if count["n"] <= 2:
            raise UiaError("todavía no")
        return True

    assert wait_until(flaky, timeout_s=3.0, poll_s=0.2, clock=clock.clock, sleep=clock.sleep)

    def broken() -> bool:
        raise RuntimeError("fallo de programación")

    with pytest.raises(RuntimeError):
        wait_until(broken, timeout_s=3.0, poll_s=0.2, clock=clock.clock, sleep=clock.sleep)


def test_expectation_process(fakes: SimpleNamespace) -> None:
    desktop = fakes.calc_world()
    baseline = empty_baseline()
    assert expectation_met(
        desktop, Expectation(process="calculatorapp.exe"), baseline, excluded=EXCLUDED
    )
    assert not expectation_met(
        desktop, Expectation(process="notepad.exe"), baseline, excluded=EXCLUDED
    )


def test_expectation_new_window(fakes: SimpleNamespace) -> None:
    desktop = fakes.notepad_world()
    expectation = Expectation(new_window=True, title="bloc de notas")
    assert not expectation_met(
        desktop, expectation, empty_baseline(frozenset({fakes.NOTEPAD})), excluded=EXCLUDED
    )
    assert expectation_met(desktop, expectation, empty_baseline(), excluded=EXCLUDED)

    chrome = fakes.make_window(900, "chrome.exe", "")
    desktop.add_window(chrome)
    desktop.reads.clear()
    chrome_expectation = Expectation(new_window=True, title="google")
    assert not expectation_met(
        desktop,
        chrome_expectation,
        empty_baseline(frozenset({fakes.NOTEPAD})),
        excluded=EXCLUDED,
    )
    assert desktop.reads == [("windows",)]


def test_expectation_element(fakes: SimpleNamespace) -> None:
    desktop = fakes.calc_world()
    expectation = Expectation(
        element=ElementExpectation(automation_id="CalculatorResults", name="Se muestra 5")
    )
    assert not expectation_met(desktop, expectation, empty_baseline(), excluded=EXCLUDED)
    desktop.get("Se muestra 0").rename("Se muestra 5")
    assert expectation_met(desktop, expectation, empty_baseline(), excluded=EXCLUDED)


def test_capture_baseline(fakes: SimpleNamespace) -> None:
    desktop = fakes.settings_world()
    window = desktop.foreground()
    element = desktop.live(desktop.get("Inicio"))
    baseline = capture_baseline(desktop, window, element)
    assert baseline.foreground_handle == fakes.SETTINGS
    assert baseline.window_handles == frozenset({fakes.SETTINGS})
    assert baseline.process_handles == frozenset({fakes.SETTINGS})
    assert baseline.fingerprint == ui_fingerprint(desktop.snapshot(window))
    assert baseline.state is not None and baseline.state.selected is True

    chrome = fakes.make_window(900, "chrome.exe", "")
    desktop.add_window(chrome)
    baseline = capture_baseline(desktop, chrome.ref, None)
    assert baseline.fingerprint is None
    assert baseline.state is None
