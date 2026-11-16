"""Tests de la detección de estabilidad (casos 1 a 5 de la especificación C04)."""

import pytest

from geass.crawler.passive import StabilityTracker


def test_same_key_becomes_stable_after_the_window() -> None:
    tracker = StabilityTracker(0.4, 5.0, 0.0)
    assert [tracker.observe("A", now) for now in (0.0, 0.2, 0.4)] == [
        "settling",
        "settling",
        "stable",
    ]


def test_a_change_restarts_the_window() -> None:
    tracker = StabilityTracker(0.4, 5.0, 0.0)
    observations = [("A", 0.0), ("A", 0.2), ("B", 0.3), ("B", 0.5), ("B", 0.8)]
    statuses = [tracker.observe(key, now) for key, now in observations]
    assert statuses == ["settling"] * 4 + ["stable"]
    assert tracker.unchanged_since == 0.3
    assert tracker.changes == 1


def test_a_tree_that_keeps_changing_times_out() -> None:
    tracker = StabilityTracker(0.4, 5.0, 0.0)
    statuses = []
    for index in range(12):
        now = index * 0.5
        status = tracker.observe(f"huella-{index}", now)
        statuses.append((now, status))
        if status != "settling":
            break
    first_due = next(now for now, _ in statuses if now - tracker.started_at >= 5.0)
    assert statuses[-1] == (first_due, "timed_out")
    assert all(status == "settling" for _, status in statuses[:-1])


def test_none_never_gives_stability_and_restarts_the_count() -> None:
    tracker = StabilityTracker(0.4, 5.0, 0.0)
    observations = [("A", 0.0), (None, 0.2), ("A", 0.25), ("A", 0.5)]
    assert [tracker.observe(key, now) for key, now in observations] == ["settling"] * 4
    assert tracker.unchanged_since == 0.25
    assert tracker.observe("A", 0.75) == "stable"


@pytest.mark.parametrize(("window_s", "timeout_s"), [(0, 5), (0.4, 0)])
def test_window_and_timeout_must_be_positive(window_s: float, timeout_s: float) -> None:
    with pytest.raises(ValueError):
        StabilityTracker(window_s, timeout_s, 0)
