"""Tests del arranque y la parada (casos 45 a 50 de la especificación C04).

Nunca se arrancan las escuchas reales ni el sensor real: ``listeners=False``, o dobles en su
lugar. La base y el archivo de estado van en ``tmp_path``.
"""

import json
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.passive import (
    AlreadyRunningError,
    HotkeyError,
    NamedStopEvent,
    PassiveError,
    PassiveRunner,
    PassiveStatus,
    read_status,
    request_stop,
)
from geass.crawler.passive import runner as runner_module
from geass.crawler.store import CrawlerStore

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="eventos con nombre de Win32")

SECRET_PROCESS = "ProcesoSecreto.exe"
SECRET_TITLE = "Titulo Confidencial - Ventana Privada"
SECRET_BUTTONS = [("BotonSecretoAceptar", (10, 10, 110, 50)), ("BotonPrivado", (120, 10, 220, 50))]


@pytest.fixture
def fast_config(crawler_config: CrawlerConfig) -> CrawlerConfig:
    passive = crawler_config.passive.model_copy(
        update={
            "poll_interval_s": 0.02,
            "stability_window_s": 0.05,
            "stability_timeout_s": 0.5,
            "stability_poll_s": 0.02,
            "resync_interval_s": 0.2,
            "resync_max_interval_s": 0.5,
            "status_interval_s": 0.1,
        }
    )
    return crawler_config.model_copy(update={"passive": passive})


@pytest.fixture
def secret_sensor(fakes: SimpleNamespace) -> Any:
    window = fakes.WINDOW.__class__(
        handle=3003,
        process_id=6464,
        process_name=SECRET_PROCESS,
        class_name="VentanaSecreta",
        title=SECRET_TITLE,
    )
    return fakes.FakeSensor(window, fakes.make_dump(SECRET_BUTTONS, window=window))


@pytest.fixture
def status_path(tmp_path: Path) -> Path:
    return tmp_path / "estado" / "passive_status.json"


def make_runner(
    config: CrawlerConfig, store: CrawlerStore, sensor: Any, status_path: Path, **kwargs: Any
) -> PassiveRunner:
    options: dict[str, Any] = {"stop_signal": threading.Event(), "listeners": False}
    options.update(kwargs)
    return PassiveRunner(config, store=store, sensor=sensor, status_path=status_path, **options)


def test_run_with_duration_stops_by_itself(
    fast_config: CrawlerConfig, store: CrawlerStore, secret_sensor: Any, status_path: Path
) -> None:
    runner = make_runner(fast_config, store, secret_sensor, status_path)
    assert runner.observer is None

    started = time.monotonic()
    final = runner.run(duration_s=1.0)
    elapsed = time.monotonic() - started

    assert 1.0 <= elapsed < 5.0
    assert final.running is False
    assert final.within_cpu_budget == (final.cpu_fraction <= final.cpu_budget_fraction)
    (session,) = store.list_sessions()
    assert session.id == final.session_id
    assert session.mode == "passive"
    assert session.ended_at is not None
    assert status_path.is_file()
    on_disk = PassiveStatus.model_validate_json(status_path.read_text(encoding="utf-8"))
    assert on_disk.running is False
    assert on_disk.session_id == final.session_id
    assert runner.observer is not None
    assert not runner.observer.is_alive()
    assert store.counts()["states"] == 1
    assert final.counters["states_recorded"] >= 1


def test_run_stops_when_the_signal_is_set(
    fast_config: CrawlerConfig, store: CrawlerStore, secret_sensor: Any, status_path: Path
) -> None:
    signal = threading.Event()
    runner = make_runner(fast_config, store, secret_sensor, status_path, stop_signal=signal)
    timer = threading.Timer(0.3, signal.set)

    started = time.monotonic()
    timer.start()
    try:
        final = runner.run()
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3.0
    assert final.running is False


def test_status_file_has_no_names(
    fast_config: CrawlerConfig, store: CrawlerStore, secret_sensor: Any, status_path: Path
) -> None:
    make_runner(fast_config, store, secret_sensor, status_path).run(duration_s=0.5)
    text = status_path.read_text(encoding="utf-8").lower()
    secrets = [
        SECRET_PROCESS,
        SECRET_TITLE,
        "ventanasecreta",
        *(name for name, _ in SECRET_BUTTONS),
    ]
    for secret in secrets:
        assert secret.lower() not in text


@windows_only
def test_named_stop_event_and_request_stop() -> None:
    name = f"Local\\GeassTest{uuid.uuid4().hex}"
    assert request_stop(name) is False

    event = NamedStopEvent(name)
    try:
        assert event.wait(0) is False
        assert request_stop(name) is True
        assert event.wait(0) is True
        with pytest.raises(AlreadyRunningError):
            NamedStopEvent(name)
    finally:
        event.close()
    event.close()
    assert request_stop(name) is False


def test_read_status_without_file_or_with_a_broken_file(
    crawler_config: CrawlerConfig, tmp_path: Path
) -> None:
    path = tmp_path / "passive_status.json"
    assert read_status(crawler_config, status_path=path) is None
    path.write_text("{", encoding="utf-8")
    with pytest.raises(PassiveError):
        read_status(crawler_config, status_path=path)


def test_read_status_reads_what_run_wrote(
    fast_config: CrawlerConfig, store: CrawlerStore, secret_sensor: Any, status_path: Path
) -> None:
    final = make_runner(fast_config, store, secret_sensor, status_path).run(duration_s=0.3)
    assert read_status(fast_config, status_path=status_path) == final


def test_session_notes_are_json_with_cpu_and_counters(
    fast_config: CrawlerConfig, store: CrawlerStore, secret_sensor: Any, status_path: Path
) -> None:
    final = make_runner(fast_config, store, secret_sensor, status_path).run(duration_s=0.3)
    notes = json.loads(store.get_session(final.session_id).notes)
    assert {"cpu_fraction", "wall_s", "counters"} <= set(notes)
    assert "error" not in notes
    assert notes["counters"]["states_recorded"] >= 1


class _Recorder:
    """Registra en una lista común cuándo se arranca y se detiene cada pieza."""

    log: list[str] = []
    fail_start = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.log.append(f"create:{self.label}")

    label = "pieza"

    def start(self) -> None:
        if self.fail_start:
            raise HotkeyError("atajo ocupado (doble del test)")
        self.log.append(f"start:{self.label}")

    def stop(self) -> None:
        self.log.append(f"stop:{self.label}")


@pytest.fixture
def fake_listeners(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Sustituye el atajo y las escuchas del runner por dobles que no tocan el sistema."""
    log: list[str] = []

    def double(label: str, *, fail: bool = False) -> type:
        return type(label, (_Recorder,), {"log": log, "label": label, "fail_start": fail})

    monkeypatch.setattr(runner_module, "PauseHotkey", double("hotkey"))
    monkeypatch.setattr(runner_module, "MouseListener", double("mouse"))
    monkeypatch.setattr(runner_module, "ShortcutListener", double("shortcuts"))
    return log


def test_listeners_start_and_stop_in_order(
    fast_config: CrawlerConfig,
    store: CrawlerStore,
    secret_sensor: Any,
    status_path: Path,
    fake_listeners: list[str],
) -> None:
    passive = fast_config.passive.model_copy(
        update={"capture_shortcuts": True, "listen_mouse": True}
    )
    config = fast_config.model_copy(update={"passive": passive})
    make_runner(config, store, secret_sensor, status_path, listeners=True).run(duration_s=0.2)
    starts = [entry for entry in fake_listeners if entry.startswith("start:")]
    stops = [entry for entry in fake_listeners if entry.startswith("stop:")]
    assert starts == ["start:hotkey", "start:mouse", "start:shortcuts"]
    assert stops == ["stop:mouse", "stop:shortcuts", "stop:hotkey"]


def test_no_keyboard_listener_without_capture_shortcuts(
    fast_config: CrawlerConfig,
    store: CrawlerStore,
    secret_sensor: Any,
    status_path: Path,
    fake_listeners: list[str],
) -> None:
    assert fast_config.passive.capture_shortcuts is False
    make_runner(fast_config, store, secret_sensor, status_path, listeners=True).run(duration_s=0.2)
    assert not [entry for entry in fake_listeners if "shortcuts" in entry]
    assert "start:hotkey" in fake_listeners


def test_hotkey_failure_starts_nothing_and_closes_the_session(
    fast_config: CrawlerConfig,
    store: CrawlerStore,
    secret_sensor: Any,
    status_path: Path,
    fake_listeners: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = type(
        "hotkey", (_Recorder,), {"log": fake_listeners, "label": "hotkey", "fail_start": True}
    )
    monkeypatch.setattr(runner_module, "PauseHotkey", failing)
    runner = make_runner(fast_config, store, secret_sensor, status_path, listeners=True)

    with pytest.raises(HotkeyError):
        runner.run(duration_s=5.0)

    assert not [entry for entry in fake_listeners if entry.startswith("start:")]
    assert not [entry for entry in fake_listeners if "mouse" in entry]
    assert runner.observer is not None
    assert not runner.observer.is_alive()
    assert secret_sensor.dump_calls == 0
    (session,) = store.list_sessions()
    assert session.ended_at is not None
    assert "atajo de pausa" in json.loads(session.notes)["error"]
    assert (
        PassiveStatus.model_validate_json(status_path.read_text(encoding="utf-8")).running is False
    )
