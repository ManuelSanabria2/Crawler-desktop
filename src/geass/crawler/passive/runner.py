"""Arranque, parada y estado del crawler pasivo como proceso.

``PassiveRunner`` monta las piezas del observador (sondeo, captura de clics, escuchas y
atajo de pausa), las arranca, espera la orden de parada y las detiene en orden. Mientras
corre escribe su estado en ``passive.status_path``: solo números, fechas y el pid, ningún
nombre de app, ventana ni elemento.

La parada desde otra terminal usa un evento con nombre de Win32 (``STOP_EVENT_NAME``), que
además impide tener dos crawlers pasivos a la vez en la misma sesión de Windows.
"""

import contextlib
import ctypes
import functools
import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from geass.core.errors import GeassError
from geass.core.log import get_logger
from geass.core.paths import ensure_dir, resolve_project_path
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.passive.control import PassiveStats, PauseState
from geass.crawler.passive.cpu import CpuMeter, CpuUsage
from geass.crawler.passive.events import Sensor
from geass.crawler.passive.hotkey import HotkeyError, PauseHotkey
from geass.crawler.passive.observer import ObserverSettings, PassiveObserver
from geass.crawler.passive.pointer import MouseListener, PressCapturer
from geass.crawler.passive.privacy import ConfigPrivacyPolicy, ShortcutListener
from geass.crawler.signature.rules import SignatureRules
from geass.crawler.store.db import CrawlerStore

logger = get_logger(__name__)

STOP_EVENT_NAME = "Local\\GeassCrawlerPassiveStop"

# Tramo máximo de cada espera del bucle principal, para que Ctrl+C responda.
_MAX_WAIT_S = 0.5

# Constantes de Win32 (winbase.h, winerror.h).
_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_INFINITE = 0xFFFFFFFF


class PassiveError(GeassError):
    """Problema al arrancar o parar el crawler pasivo."""


class AlreadyRunningError(PassiveError):
    """Ya hay un crawler pasivo en marcha en esta sesión de Windows."""


class StopSignal(Protocol):
    """Señal de parada del bucle principal. ``threading.Event`` la cumple."""

    def wait(self, timeout: float | None = None) -> bool: ...

    def set(self) -> None: ...


class NamedStopEvent:
    """Evento con nombre de Win32. Cumple StopSignal."""

    def __init__(self, name: str = STOP_EVENT_NAME) -> None:
        """Crea el evento.

        Raises:
            AlreadyRunningError: ya existe un evento con ese nombre, es decir, ya hay un
                crawler pasivo en marcha.
            PassiveError: Windows no pudo crear el evento.
        """
        self.name = name
        kernel32 = _kernel32()
        ctypes.set_last_error(0)
        handle = kernel32.CreateEventW(None, True, False, name)
        error = ctypes.get_last_error()
        if not handle:
            raise PassiveError(f"No se pudo crear el evento de parada {name} (error {error}).")
        if error == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise AlreadyRunningError(
                "Ya hay un crawler pasivo en marcha en esta sesión de Windows. "
                "Páralo con 'python -m geass.crawler passive stop'."
            )
        self._handle: int | None = handle

    def wait(self, timeout: float | None = None) -> bool:
        """Espera a que se active. Devuelve verdadero si se activó antes de ``timeout``."""
        handle = self._require()
        if timeout is None:
            milliseconds = _INFINITE
        else:
            milliseconds = min(max(0, math.ceil(timeout * 1000)), _INFINITE - 1)
        result = _kernel32().WaitForSingleObject(handle, milliseconds)
        if result == _WAIT_OBJECT_0:
            return True
        if result == _WAIT_TIMEOUT:
            return False
        raise PassiveError(
            f"Falló la espera del evento de parada (error {ctypes.get_last_error()})."
        )

    def set(self) -> None:
        if not _kernel32().SetEvent(self._require()):
            raise PassiveError(
                f"No se pudo activar el evento de parada (error {ctypes.get_last_error()})."
            )

    def close(self) -> None:
        """Libera el evento. Se puede llamar varias veces."""
        handle, self._handle = self._handle, None
        if handle:
            _kernel32().CloseHandle(handle)

    def _require(self) -> int:
        if not self._handle:
            raise PassiveError("El evento de parada ya está cerrado.")
        return self._handle


class PassiveStatus(BaseModel):
    """Estado del crawler pasivo que se escribe en ``passive.status_path``.

    Solo números, fechas y el pid: ningún nombre de app, ventana ni elemento.
    """

    model_config = ConfigDict(frozen=True)

    pid: int
    session_id: int
    started_at: datetime
    updated_at: datetime
    running: bool
    paused: bool
    wall_s: float
    cpu_s: float
    cpu_fraction: float
    cpu_fraction_machine: float
    cpu_budget_fraction: float
    within_cpu_budget: bool
    counters: dict[str, float]


class PassiveRunner:
    """Arranca el crawler pasivo, espera la orden de parada y lo detiene en orden."""

    def __init__(
        self,
        config: CrawlerConfig,
        *,
        store: CrawlerStore | None = None,
        sensor: Sensor | None = None,
        stop_signal: StopSignal | None = None,
        status_path: Path | None = None,
        listeners: bool = True,
        cpu_meter: CpuMeter | None = None,
    ) -> None:
        """
        Args:
            config: configuración del crawler.
            store: base ya abierta. Por defecto se abre la de ``store.db_path`` y se cierra
                al terminar.
            sensor: lecturas del escritorio. Por defecto ``WinSensor``.
            stop_signal: señal de parada. Por defecto ``NamedStopEvent()``.
            status_path: archivo de estado. Por defecto ``passive.status_path``.
            listeners: si se arrancan el atajo de pausa y las escuchas de mouse y teclado.
                Los tests lo ponen en falso.
            cpu_meter: medidor de CPU. Por defecto ``CpuMeter()``.
        """
        self._config = config
        self._store = store
        self._sensor = sensor
        self._stop_signal = stop_signal
        self._status_path = status_path
        self._listeners = listeners
        self._cpu_meter = cpu_meter
        self._pause = PauseState()
        self._observer: PassiveObserver | None = None

    @property
    def observer(self) -> PassiveObserver | None:
        """Observador de la última ejecución. ``None`` antes de ``run``."""
        return self._observer

    @property
    def pause(self) -> PauseState:
        """Interruptor de pausa compartido por todas las piezas."""
        return self._pause

    def run(self, duration_s: float | None = None) -> PassiveStatus:
        """Corre hasta que llega la señal de parada, pasa ``duration_s`` o se pulsa Ctrl+C.

        Returns:
            El estado final, con ``running`` falso.

        Raises:
            AlreadyRunningError: ya hay otro crawler pasivo en marcha.
            HotkeyError: no se pudo registrar el atajo de pausa; no se arrancó nada.
        """
        config = self._config
        owned_signal: NamedStopEvent | None = None
        stop_signal = self._stop_signal
        if stop_signal is None:
            owned_signal = NamedStopEvent()
            stop_signal = owned_signal
        owned_store: CrawlerStore | None = None
        try:
            store = self._store
            if store is None:
                owned_store = CrawlerStore.from_config(config)
                store = owned_store
            sensor = self._sensor
            if sensor is None:
                from geass.crawler.passive.sensor_win import WinSensor

                sensor = WinSensor.from_config(config)
            status_path = self._status_path
            if status_path is None:
                status_path = resolve_project_path(config.passive.status_path)
            cpu_meter = self._cpu_meter if self._cpu_meter is not None else CpuMeter()
            return self._run_session(store, sensor, stop_signal, status_path, cpu_meter, duration_s)
        finally:
            if owned_signal is not None:
                owned_signal.close()
            if owned_store is not None:
                owned_store.close()

    def _run_session(
        self,
        store: CrawlerStore,
        sensor: Sensor,
        stop_signal: StopSignal,
        status_path: Path,
        cpu_meter: CpuMeter,
        duration_s: float | None,
    ) -> PassiveStatus:
        config = self._config
        passive = config.passive
        pause = self._pause
        stats = PassiveStats()
        clock = time.perf_counter
        started_at = datetime.now(UTC)
        session_id = store.start_session("passive")

        observer: PassiveObserver | None = None
        capturer: PressCapturer | None = None
        hotkey: PauseHotkey | None = None
        mouse: MouseListener | None = None
        shortcuts: ShortcutListener | None = None
        meter_started = False
        error_note: str | None = None

        def usage() -> CpuUsage:
            if meter_started:
                return cpu_meter.read()
            return CpuUsage(wall_s=0.0, cpu_s=0.0, logical_cpus=os.cpu_count() or 1)

        def status(running: bool) -> PassiveStatus:
            return _build_status(
                session_id=session_id,
                started_at=started_at,
                running=running,
                paused=pause.is_paused(),
                usage=usage(),
                budget=passive.cpu_budget_fraction,
                counters=stats.snapshot(),
            )

        try:
            privacy = ConfigPrivacyPolicy.from_config(config)
            rules = SignatureRules.from_config(config.signature)
            settings = ObserverSettings.from_config(passive)
            thread_setup = getattr(sensor, "thread_setup", None)
            thread_teardown = getattr(sensor, "thread_teardown", None)
            observer = PassiveObserver(
                sensor,
                store,
                privacy,
                rules,
                settings,
                excluded_apps=config.apps.excluded,
                pause=pause,
                stats=stats,
                clock=clock,
                thread_setup=thread_setup,
                thread_teardown=thread_teardown,
            )
            self._observer = observer
            capturer = PressCapturer(
                sensor,
                privacy,
                pause,
                observer.post,
                max_delay_s=passive.max_capture_delay_s,
                stats=stats,
                clock=clock,
                thread_setup=thread_setup,
                thread_teardown=thread_teardown,
            )

            if self._listeners:
                # Primero el atajo de pausa: sin él no se arranca nada.
                try:
                    hotkey = PauseHotkey(passive.pause_hotkey, pause.toggle)
                    hotkey.start()
                except HotkeyError as error:
                    hotkey = None
                    error_note = f"No arrancó: no se pudo registrar el atajo de pausa. {error}"
                    raise

            observer.start()
            capturer.start()
            if self._listeners:
                if passive.listen_mouse:
                    mouse = MouseListener(capturer.submit, clock=clock)
                    mouse.start()
                if passive.capture_shortcuts:
                    shortcuts = ShortcutListener(observer.post, pause=pause, clock=clock)
                    shortcuts.start()
            if passive.use_uia_events:
                logger.info(
                    "Los eventos de UI Automation todavía no están implementados; "
                    "se usa solo el sondeo."
                )

            cpu_meter.start()
            meter_started = True
            _write_status_safely(status_path, status(running=True))
            self._wait(stop_signal, duration_s, lambda: status(running=True), status_path)
        except Exception as error:
            if error_note is None:
                error_note = f"Terminó con error: {type(error).__name__}"
            raise
        finally:
            for component in (mouse, shortcuts, hotkey, capturer, observer):
                if component is None:
                    continue
                try:
                    component.stop()
                except Exception:
                    logger.exception("No se pudo detener %s.", type(component).__name__)
            final = status(running=False)
            notes: dict[str, Any] = {
                "cpu_fraction": final.cpu_fraction,
                "wall_s": final.wall_s,
                "counters": final.counters,
            }
            if error_note is not None:
                notes["error"] = error_note
            try:
                store.end_session(session_id, notes=json.dumps(notes, ensure_ascii=False))
            except Exception:
                logger.exception("No se pudo cerrar la sesión %d en la base.", session_id)
            _write_status_safely(status_path, final)
        return final

    def _wait(
        self,
        stop_signal: StopSignal,
        duration_s: float | None,
        current_status: Callable[[], PassiveStatus],
        status_path: Path,
    ) -> None:
        """Bucle principal: espera la parada y reescribe el estado cada cierto tiempo."""
        interval = self._config.passive.status_interval_s
        start = time.perf_counter()
        deadline = None if duration_s is None else start + duration_s
        next_status = start + interval
        try:
            while True:
                now = time.perf_counter()
                if deadline is not None and now >= deadline:
                    return
                timeout = min(_MAX_WAIT_S, max(0.0, next_status - now))
                if deadline is not None:
                    timeout = min(timeout, deadline - now)
                if stop_signal.wait(timeout):
                    return
                now = time.perf_counter()
                if now >= next_status:
                    _write_status_safely(status_path, current_status())
                    next_status = now + interval
        except KeyboardInterrupt:
            logger.info("Parada pedida con Ctrl+C.")


def request_stop(name: str = STOP_EVENT_NAME) -> bool:
    """Pide la parada al crawler pasivo en marcha.

    Returns:
        Verdadero si había uno y se le avisó; falso si no hay ninguno (no crea el evento).
    """
    kernel32 = _kernel32()
    handle = kernel32.OpenEventW(_EVENT_MODIFY_STATE, False, name)
    if not handle:
        return False
    try:
        if not kernel32.SetEvent(handle):
            raise PassiveError(
                f"No se pudo activar el evento de parada (error {ctypes.get_last_error()})."
            )
    finally:
        kernel32.CloseHandle(handle)
    return True


def read_status(config: CrawlerConfig, *, status_path: Path | None = None) -> PassiveStatus | None:
    """Último estado escrito, o ``None`` si no hay archivo de estado.

    Raises:
        PassiveError: el archivo existe pero no se puede leer o no es un estado válido.
    """
    path = (
        status_path if status_path is not None else resolve_project_path(config.passive.status_path)
    )
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PassiveError(f"No se pudo leer el archivo de estado {path}: {error}") from error
    try:
        return PassiveStatus.model_validate_json(text)
    except ValidationError as error:
        raise PassiveError(f"El archivo de estado {path} no es válido: {error}") from error


def _build_status(
    *,
    session_id: int,
    started_at: datetime,
    running: bool,
    paused: bool,
    usage: CpuUsage,
    budget: float,
    counters: dict[str, float],
) -> PassiveStatus:
    fraction = usage.process_fraction
    return PassiveStatus(
        pid=os.getpid(),
        session_id=session_id,
        started_at=started_at,
        updated_at=datetime.now(UTC),
        running=running,
        paused=paused,
        wall_s=usage.wall_s,
        cpu_s=usage.cpu_s,
        cpu_fraction=fraction,
        cpu_fraction_machine=usage.machine_fraction,
        cpu_budget_fraction=budget,
        within_cpu_budget=fraction <= budget,
        counters=counters,
    )


def _write_status(path: Path, status: PassiveStatus) -> None:
    """Escribe el estado de forma atómica: archivo temporal en la misma carpeta y reemplazo."""
    ensure_dir(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(status.model_dump_json(indent=2))
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _write_status_safely(path: Path, status: PassiveStatus) -> None:
    # Un fallo al escribir el estado no debe parar el crawler.
    try:
        _write_status(path, status)
    except OSError as error:
        logger.warning("No se pudo escribir el archivo de estado %s: %s", path, error)


@functools.cache
def _kernel32() -> Any:
    """``kernel32.dll`` con los prototipos de eventos. Solo existe en Windows."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = (
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32
