"""Tests del bucle de observación (casos 14 a 28 de la especificación C04).

Salvo el último, todos llaman a ``tick`` con un reloj y un sensor falsos, sin hilos.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.passive import (
    MousePress,
    ObserverEvent,
    ObserverSettings,
    PassiveObserver,
    PauseState,
    PressCapture,
    PressCapturer,
    ShortcutPressed,
)
from geass.crawler.signature import SignatureRules
from geass.crawler.store import CrawlerStore

STEP_S = 0.1
MAX_TICKS = 500

ObserverFactory = Callable[..., PassiveObserver]


@pytest.fixture
def make_observer(
    sensor: Any,
    store: CrawlerStore,
    policy: Any,
    clock: Any,
    rules: SignatureRules,
    crawler_config: CrawlerConfig,
    fakes: SimpleNamespace,
) -> ObserverFactory:
    def build(
        *,
        settings: ObserverSettings = fakes.TICK_SETTINGS,
        privacy: Any = policy,
        pause: PauseState | None = None,
    ) -> PassiveObserver:
        return PassiveObserver(
            sensor,
            store,
            privacy,
            rules,
            settings,
            excluded_apps=crawler_config.apps.excluded,
            pause=pause,
            clock=clock,
        )

    return build


def step(observer: PassiveObserver, clock: Any, events: Sequence[ObserverEvent] = ()) -> None:
    observer.tick(events)
    clock.advance(STEP_S)


def ticks(observer: PassiveObserver, clock: Any, count: int) -> None:
    for _ in range(count):
        step(observer, clock)


def settle_known_state(observer: PassiveObserver, clock: Any) -> int:
    """Vueltas hasta que hay un estado estable registrado. Devuelve su id."""
    for _ in range(MAX_TICKS):
        step(observer, clock)
        if observer.current_state_id is not None and not observer.settling:
            return observer.current_state_id
    raise AssertionError("El observador no llegó a un estado estable.")


def until_settled(observer: PassiveObserver, clock: Any) -> None:
    for _ in range(MAX_TICKS):
        if not observer.settling:
            return
        step(observer, clock)
    raise AssertionError("La espera de estabilidad no terminó.")


def click(
    fakes: SimpleNamespace, view: Any, name: str, at: float, window: Any = None
) -> PressCapture:
    element = fakes.find(view, name)
    x, y = fakes.center(element)
    return PressCapture(
        press=MousePress(x=x, y=y, button="left", at=at),
        window=window if window is not None else fakes.WINDOW,
        element=fakes.props_of(element),
        delay_s=0.0,
    )


def test_first_state_is_recorded_without_transitions(
    make_observer: ObserverFactory, clock: Any, store: CrawlerStore
) -> None:
    observer = make_observer()
    ticks(observer, clock, 30)
    assert store.counts()["states"] == 1
    assert store.counts()["transitions"] == 0
    assert observer.current_state_id is not None
    assert observer.stats.get("states_new") == 1


def test_click_with_change_records_a_transition(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    views: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    observer = make_observer()
    source_id = settle_known_state(observer, clock)

    step(observer, clock, [click(fakes, views.a, "ok", clock())])
    sensor.show(fakes.WINDOW, views.b)
    until_settled(observer, clock)

    (transition,) = store.list_transitions()
    assert transition.source_state_id == source_id
    assert transition.target_state_id == observer.current_state_id != source_id
    assert transition.action_type == "invoke"
    assert transition.source == "passive"
    assert transition.element_id is not None
    assert store.get_element(transition.element_id).name == "ok"
    assert transition.mean_latency_s is not None
    assert 0.0 <= transition.mean_latency_s <= fakes.TICK_SETTINGS.stability_timeout_s
    assert observer.stats.get("transitions_with_element") == 1


def test_click_without_change_records_no_transition(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    views: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    observer = make_observer()
    settle_known_state(observer, clock)

    step(observer, clock, [click(fakes, views.a, "ok", clock())])
    until_settled(observer, clock)

    assert store.list_transitions() == []
    assert observer.stats.get("triggers_without_change") == 1


def test_two_clicks_in_the_same_wait_record_no_transition(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    views: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    observer = make_observer()
    settle_known_state(observer, clock)

    step(observer, clock, [click(fakes, views.a, "ok", clock())])
    sensor.show(fakes.WINDOW, views.b)
    step(observer, clock)
    step(observer, clock, [click(fakes, views.b, "back", clock())])
    sensor.show(fakes.WINDOW, views.c)
    until_settled(observer, clock)

    assert store.list_transitions() == []
    assert observer.stats.get("triggers_interrupted") == 1
    assert observer.current_state_id is not None
    assert store.counts()["states"] == 2


def test_click_in_another_window_is_not_attributed(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    observer = make_observer()
    source_id = settle_known_state(observer, clock)
    other_view = fakes.make_dump(fakes.VIEW_C_BUTTONS, window=fakes.OTHER_WINDOW)

    capture = click(fakes, other_view, "next", clock(), window=fakes.OTHER_WINDOW)
    sensor.show(fakes.OTHER_WINDOW, other_view)
    step(observer, clock, [capture])
    until_settled(observer, clock)

    assert store.list_transitions() == []
    assert observer.stats.get("triggers_unattributed") == 1
    assert observer.current_state_id not in (None, source_id)
    assert store.counts()["states"] == 2
    assert store.find_app(fakes.OTHER_WINDOW.process_name) is not None


def test_shortcut_with_change_records_a_transition_without_element(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    views: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    observer = make_observer()
    settle_known_state(observer, clock)

    step(observer, clock, [ShortcutPressed(combo="ctrl+s", at=clock())])
    sensor.show(fakes.WINDOW, views.b)
    until_settled(observer, clock)

    (transition,) = store.list_transitions()
    assert transition.element_id is None
    assert transition.action_type == "unknown"
    assert observer.stats.get("shortcuts_seen") == 1


def test_excluded_window_in_foreground_is_never_read(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    secret = replace(fakes.OTHER_WINDOW, process_name="secreto.exe")
    observer = make_observer(privacy=fakes.FakePolicy(excluded=["secreto.exe"]))
    settle_known_state(observer, clock)
    before, dumps = store.counts(), sensor.dump_calls

    sensor.show(secret, fakes.make_dump(fakes.VIEW_C_BUTTONS, window=secret))
    ticks(observer, clock, 200)

    assert sensor.dump_calls == dumps
    assert store.counts() == before
    assert observer.current_state_id is None
    assert observer.stats.get("skipped_excluded_window") == 1


def test_unidentified_window_in_foreground_is_never_read(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    observer = make_observer()
    settle_known_state(observer, clock)
    before, dumps = store.counts(), sensor.dump_calls

    sensor.show(replace(fakes.WINDOW, process_name=""), None)
    ticks(observer, clock, 200)

    assert sensor.dump_calls == dumps
    assert store.counts() == before
    assert observer.current_state_id is None
    assert observer.stats.get("skipped_uncertain_window") == 1


def test_foreground_change_during_the_dump_records_nothing(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    observer = make_observer()
    sensor.foreground_queue = [fakes.WINDOW, fakes.OTHER_WINDOW]

    observer.tick()

    assert sensor.dump_calls == 1
    assert store.counts()["states"] == 0
    assert observer.current_state_id is None
    assert not observer.settling


def test_partial_dump_is_not_recorded(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    sensor.show(fakes.WINDOW, fakes.make_dump(fakes.VIEW_A_BUTTONS, partial=True))
    observer = make_observer()

    step(observer, clock)
    assert observer.settling
    until_settled(observer, clock)

    assert store.counts()["states"] == 0
    assert observer.stats.get("skipped_partial_dump") == 1
    assert observer.current_state_id is None


def test_pause_stops_reading_and_drops_events(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    views: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    pause = PauseState()
    observer = make_observer(pause=pause)
    settle_known_state(observer, clock)

    pause.set_paused(True)
    dumps = sensor.dump_calls
    step(observer, clock, [click(fakes, views.a, "ok", clock())])
    sensor.show(fakes.WINDOW, views.b)
    for _ in range(50):
        step(observer, clock, [ShortcutPressed(combo="ctrl+s", at=clock())])
    assert sensor.dump_calls == dumps
    assert observer.current_state_id is None

    pause.set_paused(False)
    settle_known_state(observer, clock)

    assert store.list_transitions() == []
    assert observer.stats.get("states_recorded") == 2
    assert observer.stats.get("shortcuts_seen") == 0
    assert store.counts()["states"] == 2


def test_a_tree_that_never_settles_times_out(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    clock: Any,
) -> None:
    counter = iter(range(10_000))
    sensor.dump_factory = lambda window: fakes.make_dump(
        [(f"boton{next(counter)}", (10, 10, 110, 50))], window=window
    )
    observer = make_observer()
    started_at = clock()

    closed_at = None
    for _ in range(MAX_TICKS):
        now = clock()
        step(observer, clock)
        if observer.stats.get("settles_timed_out") == 1:
            closed_at = now
            break

    assert closed_at is not None
    timeout = fakes.TICK_SETTINGS.stability_timeout_s
    assert closed_at - started_at <= timeout + STEP_S + 1e-6
    assert observer.stats.get("settles_stable") == 0


@pytest.mark.parametrize(
    ("input_changes", "duration_s", "low", "high"),
    [(False, 300.0, 0, 19), (True, 30.0, 10, 20)],
    ids=["sin_entrada", "con_entrada"],
)
def test_resync_backs_off_without_input(
    make_observer: ObserverFactory,
    sensor: Any,
    clock: Any,
    input_changes: bool,
    duration_s: float,
    low: int,
    high: int,
) -> None:
    observer = make_observer()
    for _ in range(round(duration_s / 0.5)):
        if input_changes:
            sensor.tick += 1
        observer.tick()
        clock.advance(0.5)
    assert low <= sensor.dump_calls <= high


def test_click_on_an_element_that_is_not_stored(
    make_observer: ObserverFactory,
    fakes: SimpleNamespace,
    sensor: Any,
    views: Any,
    clock: Any,
    store: CrawlerStore,
) -> None:
    with_list = fakes.make_dump(
        fakes.VIEW_A_BUTTONS, list_items=[("informe.txt", (10, 310, 400, 340))]
    )
    sensor.show(fakes.WINDOW, with_list)
    observer = make_observer()
    settle_known_state(observer, clock)

    step(observer, clock, [click(fakes, with_list, "informe.txt", clock())])
    sensor.show(fakes.WINDOW, views.b)
    until_settled(observer, clock)

    (transition,) = store.list_transitions()
    assert transition.element_id is None
    assert transition.action_type == "unknown"


def wait_for(condition: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_threads_record_a_click_transition(
    fakes: SimpleNamespace,
    sensor: Any,
    views: Any,
    policy: Any,
    store: CrawlerStore,
    rules: SignatureRules,
) -> None:
    settings = ObserverSettings(
        poll_interval_s=0.02,
        stability_window_s=0.1,
        stability_timeout_s=1.0,
        stability_poll_s=0.02,
        resync_interval_s=0.5,
        resync_max_interval_s=1.0,
    )
    observer = PassiveObserver(sensor, store, policy, rules, settings, excluded_apps=[])
    capturer = PressCapturer(
        sensor, policy, observer.pause, observer.post, max_delay_s=1.0, stats=observer.stats
    )
    ok = fakes.find(views.a, "ok")
    sensor.element = fakes.props_of(ok)
    # La interfaz reacciona al clic justo después de que se lee el elemento.
    sensor.on_element_at = lambda: sensor.show(fakes.WINDOW, views.b)

    observer.start()
    capturer.start()
    try:
        assert wait_for(lambda: observer.current_state_id is not None, 3.0)
        x, y = fakes.center(ok)
        started = time.monotonic()
        capturer.submit(MousePress(x=x, y=y, button="left", at=time.perf_counter()))
        assert wait_for(lambda: len(store.list_transitions()) == 1, 3.0)
        assert time.monotonic() - started < 3.0
    finally:
        capturer.stop()
        observer.stop()

    assert not observer.is_alive()
    (transition,) = store.list_transitions()
    assert transition.action_type == "invoke"
    assert transition.element_id is not None
