"""Bucle de observación del crawler pasivo.

Un único hilo, el de sondeo, vuelca ventanas y escribe en la base. Los demás hilos (captura
de clics, atajos, y más adelante eventos de UI Automation) solo le publican eventos con
``PassiveObserver.post``. Así el orden de los hechos se decide en un solo lugar.

Ciclo:

1. En reposo se consulta la ventana en primer plano cada ``poll_interval_s``. Eso no usa
   UI Automation y casi no cuesta. El árbol solo se vuelca si cambió la ventana o su título,
   si llegó un aviso, o si toca resincronizar (``resync_interval_s``).
2. Un clic, un atajo o un volcado distinto del estado conocido abren una espera de
   estabilidad: se vuelca cada ``stability_poll_s`` hasta que la firma deja de cambiar
   durante ``stability_window_s`` o se agota ``stability_timeout_s``.
3. Al cerrar la espera se registra el estado y, si se sabe con certeza qué acción lo
   provocó y desde qué estado, la transición.

Regla de certeza: si no se puede confirmar qué ventana es, o el volcado es parcial, no se
registra nada y se olvida el estado conocido. Solo lee: nunca actúa sobre la interfaz.
"""

import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from geass.core.errors import GeassError
from geass.core.log import get_logger
from geass.core.schemas.crawler import Passive
from geass.crawler.passive.control import PassiveStats, PauseState
from geass.crawler.passive.events import (
    ChangeHint,
    ObserverEvent,
    PressCapture,
    PrivacyPolicy,
    Sensor,
    ShortcutPressed,
    WindowRef,
)
from geass.crawler.passive.pointer import infer_action, match_element
from geass.crawler.passive.stability import StabilityTracker
from geass.crawler.signature.compute import StateSignature, compute_signature
from geass.crawler.signature.rules import SignatureRules
from geass.crawler.store.db import CrawlerStore, StoreError
from geass.crawler.store.recording import RecordedState, record_dump
from geass.crawler.store.records import ActionType
from geass.crawler.uia.schema import WindowDump

logger = get_logger(__name__)

Clock = Callable[[], float]
StateKey = tuple[int, str, str]

_STOP = object()
_JOIN_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class ObserverSettings:
    """Tiempos del observador, en segundos. En uso normal salen de ``crawler.yaml``."""

    poll_interval_s: float
    stability_window_s: float
    stability_timeout_s: float
    stability_poll_s: float
    resync_interval_s: float
    resync_max_interval_s: float

    @classmethod
    def from_config(cls, passive: Passive) -> "ObserverSettings":
        return cls(
            poll_interval_s=passive.poll_interval_s,
            stability_window_s=passive.stability_window_s,
            stability_timeout_s=passive.stability_timeout_s,
            stability_poll_s=passive.stability_poll_s,
            resync_interval_s=passive.resync_interval_s,
            resync_max_interval_s=passive.resync_max_interval_s,
        )


@dataclass(frozen=True)
class _Sample:
    """Un volcado de la ventana en primer plano, con su firma."""

    window: WindowRef
    dump: WindowDump
    signature: StateSignature
    at: float

    @property
    def key(self) -> StateKey:
        return (self.window.handle, self.signature.structural, self.signature.detailed)


@dataclass(frozen=True)
class _Known:
    """Último estado estable ya registrado en la base."""

    window: WindowRef
    dump: WindowDump
    key: StateKey
    recorded: RecordedState


@dataclass(frozen=True)
class _Trigger:
    """Acción del usuario que abrió la espera en curso.

    ``source`` es el estado desde el que se hizo, o ``None`` si no se puede atribuir.
    ``capture`` es ``None`` cuando la acción fue un atajo de teclado.
    """

    at: float
    source: _Known | None
    capture: PressCapture | None


class PassiveObserver:
    """Observa la ventana en primer plano y registra estados y transiciones."""

    def __init__(
        self,
        sensor: Sensor,
        store: CrawlerStore,
        privacy: PrivacyPolicy,
        rules: SignatureRules,
        settings: ObserverSettings,
        *,
        excluded_apps: Sequence[str],
        pause: PauseState | None = None,
        stats: PassiveStats | None = None,
        clock: Clock = time.perf_counter,
        thread_setup: Callable[[], None] | None = None,
        thread_teardown: Callable[[], None] | None = None,
    ) -> None:
        """
        Args:
            sensor: lecturas del escritorio.
            store: base del crawler. Solo escribe en ella el hilo de sondeo.
            privacy: filtros de privacidad.
            rules: reglas de firma.
            settings: tiempos del observador.
            excluded_apps: ejecutables excluidos; se pasan a ``record_dump`` como segunda
                barrera, además de ``privacy``.
            pause: interruptor de pausa compartido con el hilo de captura y el atajo.
            stats: contadores compartidos con el hilo de captura.
            clock: reloj monotónico en segundos; el mismo que marca los eventos.
            thread_setup: se llama al empezar el hilo de sondeo (inicialización de COM).
            thread_teardown: se llama al terminar el hilo de sondeo.
        """
        self._sensor = sensor
        self._store = store
        self._privacy = privacy
        self._rules = rules
        self._settings = settings
        self._excluded_apps = list(excluded_apps)
        self.pause = pause if pause is not None else PauseState()
        self.stats = stats if stats is not None else PassiveStats()
        self._clock = clock
        self._thread_setup = thread_setup
        self._thread_teardown = thread_teardown

        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

        # Todo lo que sigue solo lo toca el hilo de sondeo (o quien llame a tick en un test).
        self._current: _Known | None = None
        self._trigger: _Trigger | None = None
        self._tracker: StabilityTracker | None = None
        self._settle_unprompted = False
        self._settle_detected_at = 0.0
        self._force_sample = False
        self._was_paused = False
        self._blocked_reason: str | None = None
        self._last_sample_at: float | None = None
        self._input_tick_at_sample: int | None = None
        self._had_input = True
        self._idle_delay_s = settings.resync_interval_s

    # Interfaz para los otros hilos

    def post(self, event: ObserverEvent) -> None:
        """Publica un evento para el hilo de sondeo. Se puede llamar desde cualquier hilo."""
        self._queue.put(event)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("El hilo de sondeo ya está en marcha.")
        self._thread = threading.Thread(target=self.run, name="geass-passive-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Pide al hilo de sondeo que termine y espera a que lo haga."""
        thread = self._thread
        if thread is None:
            return
        self._queue.put(_STOP)
        thread.join(_JOIN_TIMEOUT_S)
        self._thread = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # Estado visible (para pruebas y estadísticas)

    @property
    def settling(self) -> bool:
        """Verdadero mientras hay una espera de estabilidad abierta."""
        return self._tracker is not None

    @property
    def current_state_id(self) -> int | None:
        """Id en la base del último estado estable registrado, si se conoce."""
        return self._current.recorded.state.id if self._current is not None else None

    # Bucle

    def run(self) -> None:
        """Cuerpo del hilo de sondeo. Termina cuando se llama a ``stop``."""
        if self._thread_setup is not None:
            self._thread_setup()
        try:
            while True:
                events = self._wait(self.next_timeout())
                if events is None:
                    return
                try:
                    self.tick(events)
                except Exception:
                    self.stats.increment("loop_errors")
                    logger.exception("Falló una vuelta del observador; se olvida el estado.")
                    self._reset()
        finally:
            if self._thread_teardown is not None:
                self._thread_teardown()

    def next_timeout(self) -> float:
        """Segundos que el bucle espera eventos antes de la siguiente vuelta."""
        if self._tracker is not None:
            return self._settings.stability_poll_s
        return self._settings.poll_interval_s

    def tick(self, events: Sequence[ObserverEvent] = ()) -> None:
        """Una vuelta del bucle: procesa los eventos recibidos y luego observa.

        Es lo que ``run`` llama en cada vuelta. Los tests la llaman directamente, con un
        reloj y un sensor falsos, sin hilos.
        """
        if self.pause.is_paused():
            if not self._was_paused:
                self._was_paused = True
                self._reset()
            return
        self._was_paused = False

        for event in events:
            self._handle(event)

        window = self._foreground()
        if window is None:
            return
        if self._tracker is not None:
            self._feed(self._sample(window))
        else:
            self._idle_step(window)

    def _wait(self, timeout_s: float) -> list[ObserverEvent] | None:
        """Espera eventos hasta ``timeout_s``. Devuelve ``None`` si se pidió parar."""
        events: list[ObserverEvent] = []
        try:
            item = self._queue.get(timeout=timeout_s)
        except queue.Empty:
            return events
        while True:
            if item is _STOP:
                return None
            events.append(item)
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return events

    # Eventos

    def _handle(self, event: ObserverEvent) -> None:
        if isinstance(event, PressCapture):
            handle = event.window.handle if event.window is not None else None
            self._on_trigger(event.press.at, event, handle)
        elif isinstance(event, ShortcutPressed):
            self.stats.increment("shortcuts_seen")
            current = self._current
            self._on_trigger(event.at, None, current.window.handle if current else None)
        elif isinstance(event, ChangeHint):
            self.stats.increment("hints_seen")
            self._force_sample = True

    def _on_trigger(self, at: float, capture: PressCapture | None, handle: int | None) -> None:
        """Una acción del usuario: abre una espera o interfiere con la que había."""
        source = self._current
        if source is not None and handle != source.window.handle:
            source = None

        tracker = self._tracker
        if tracker is None:
            self._open_settle(self._clock(), unprompted=False)
        elif self._trigger is None and self._settle_unprompted and at <= self._settle_detected_at:
            # La acción es anterior al volcado que detectó el cambio: es su causa probable
            # y el estado conocido sigue siendo el de origen. No hace falta reiniciar.
            pass
        else:
            # Otra acción en mitad de una espera. El estado intermedio no llegó a
            # estabilizarse, así que ya no se sabe desde dónde parte cada acción.
            self.stats.increment("triggers_interrupted")
            source = None
            self._open_settle(self._clock(), unprompted=False, count=False)

        if source is None and tracker is None:
            self.stats.increment("triggers_unattributed")
        self._trigger = _Trigger(at=at, source=source, capture=capture)

    def _open_settle(self, now: float, *, unprompted: bool, count: bool = True) -> None:
        self._tracker = StabilityTracker(
            self._settings.stability_window_s, self._settings.stability_timeout_s, now
        )
        self._settle_unprompted = unprompted
        self._settle_detected_at = now
        if count:
            self.stats.increment("settles_started")

    # Observación

    def _foreground(self) -> WindowRef | None:
        """Ventana en primer plano si se puede leer. Si no, olvida el estado conocido."""
        window = self._sensor.foreground()
        if window is None or not window.identified:
            self._block("skipped_uncertain_window")
            return None
        if not self._privacy.allows_window(window):
            self._block("skipped_excluded_window")
            return None
        self._blocked_reason = None
        return window

    def _block(self, reason: str) -> None:
        if self._blocked_reason != reason:
            self._blocked_reason = reason
            self.stats.increment(reason)
        self._reset()

    def _idle_step(self, window: WindowRef) -> None:
        current = self._current
        now = self._clock()
        due = (
            current is None
            or self._force_sample
            or window.handle != current.window.handle
            or window.title != current.window.title
            or self._resync_due(now)
        )
        if not due:
            return
        self._force_sample = False
        sample = self._sample(window)
        if sample is None:
            self._current = None
            return
        if current is not None and sample.key == current.key:
            self._current = replace(current, window=sample.window, dump=sample.dump)
            self._idle_delay_s = (
                self._settings.resync_interval_s
                if self._had_input
                else min(self._idle_delay_s * 2, self._settings.resync_max_interval_s)
            )
            return
        self._open_settle(now, unprompted=True)
        self._settle_detected_at = sample.at
        self._feed(sample)

    def _resync_due(self, now: float) -> bool:
        if self._last_sample_at is None:
            return True
        had_input = self._sensor.input_tick() != self._input_tick_at_sample
        delay = self._settings.resync_interval_s if had_input else self._idle_delay_s
        return now - self._last_sample_at >= delay

    def _sample(self, window: WindowRef) -> _Sample | None:
        """Vuelca la ventana. Devuelve ``None`` si falla o si ya no es la de primer plano."""
        tick = self._sensor.input_tick()
        self._had_input = tick != self._input_tick_at_sample
        self._input_tick_at_sample = tick
        start = self._clock()
        try:
            dump = self._sensor.dump(window)
        except GeassError as error:
            self.stats.increment("sample_errors")
            logger.debug("No se pudo volcar la ventana en primer plano: %s", error)
            return None
        finally:
            self.stats.increment("samples")
            self._last_sample_at = self._clock()
            self.stats.add_dump_time(self._last_sample_at - start)

        after = self._sensor.foreground()
        if (
            after is None
            or after.handle != window.handle
            or dump.handle != window.handle
            or dump.process_id != window.process_id
        ):
            # La ventana cambió mientras se leía: no hay certeza de qué se volcó.
            return None
        signature = compute_signature(dump, self._rules)
        return _Sample(window=window, dump=dump, signature=signature, at=self._clock())

    def _feed(self, sample: _Sample | None) -> None:
        tracker = self._tracker
        if tracker is None:
            return
        now = sample.at if sample is not None else self._clock()
        status = tracker.observe(sample.key if sample is not None else None, now)
        if status == "settling":
            return
        self.stats.increment("settles_stable" if status == "stable" else "settles_timed_out")
        self._finalize(sample, tracker.unchanged_since)

    def _finalize(self, sample: _Sample | None, unchanged_since: float | None) -> None:
        """Cierra la espera: registra el estado y, si procede, la transición."""
        trigger, self._trigger = self._trigger, None
        self._tracker = None
        self._idle_delay_s = self._settings.resync_interval_s
        previous = self._current

        if sample is None:
            self._current = None
            return
        if sample.dump.partial:
            self.stats.increment("skipped_partial_dump")
            self._current = None
            return
        if previous is not None and sample.key == previous.key:
            self._current = replace(previous, window=sample.window, dump=sample.dump)
            if trigger is not None:
                self.stats.increment("triggers_without_change")
            return

        try:
            recorded = record_dump(
                self._store, sample.dump, rules=self._rules, excluded_apps=self._excluded_apps
            )
        except StoreError:
            self.stats.increment("store_errors")
            logger.exception("No se pudo registrar el estado.")
            self._current = None
            return
        self.stats.increment("states_recorded")
        if recorded.is_new:
            self.stats.increment("states_new")
        known = _Known(window=sample.window, dump=sample.dump, key=sample.key, recorded=recorded)
        self._current = known

        if trigger is None or trigger.source is None:
            return
        end = unchanged_since if unchanged_since is not None else sample.at
        self._record_transition(trigger, trigger.source, known, max(0.0, end - trigger.at))

    def _record_transition(
        self, trigger: _Trigger, source: _Known, target: _Known, latency_s: float
    ) -> None:
        element_id: int | None = None
        action: ActionType = "unknown"
        capture = trigger.capture
        if capture is not None:
            patterns: tuple[str, ...] | None = None
            if capture.element is not None:
                matched = match_element(
                    source.dump, capture.element, capture.press.x, capture.press.y
                )
                if matched is not None:
                    # Solo cuentan los elementos que la base guarda: el contenido de listas
                    # y documentos queda fuera, y con él sus patrones.
                    element_id = source.recorded.element_ids.get(matched.path)
                if element_id is not None:
                    patterns = capture.element.patterns
            action = infer_action(capture.press.button, patterns)
        try:
            self._store.record_transition(
                source.recorded.state.id,
                target.recorded.state.id,
                action,
                "passive",
                element_id=element_id,
                latency_s=latency_s,
            )
        except StoreError:
            self.stats.increment("store_errors")
            logger.exception("No se pudo registrar la transición.")
            return
        self.stats.increment("transitions_recorded")
        if element_id is not None:
            self.stats.increment("transitions_with_element")

    def _reset(self) -> None:
        """Olvida el estado conocido y la espera en curso."""
        self._current = None
        self._trigger = None
        self._tracker = None
        self._force_sample = False
        self._idle_delay_s = self._settings.resync_interval_s
