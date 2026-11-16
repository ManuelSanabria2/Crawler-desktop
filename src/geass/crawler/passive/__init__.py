"""Observador pasivo: solo lee, nunca actúa."""

from geass.crawler.passive.control import COUNTER_NAMES, PassiveStats, PauseState
from geass.crawler.passive.cpu import CpuMeter, CpuUsage
from geass.crawler.passive.events import (
    ChangeHint,
    MousePress,
    ObserverEvent,
    PressCapture,
    PrivacyPolicy,
    Sensor,
    ShortcutPressed,
    WindowRef,
)
from geass.crawler.passive.hotkey import HotkeyError, PauseHotkey, parse_hotkey
from geass.crawler.passive.observer import ObserverSettings, PassiveObserver
from geass.crawler.passive.pointer import (
    MouseListener,
    PressCapturer,
    infer_action,
    match_element,
)
from geass.crawler.passive.privacy import (
    ALT_GR,
    MODIFIER_NAMES,
    ConfigPrivacyPolicy,
    ShortcutListener,
    ShortcutTracker,
    key_name,
)
from geass.crawler.passive.runner import (
    STOP_EVENT_NAME,
    AlreadyRunningError,
    NamedStopEvent,
    PassiveError,
    PassiveRunner,
    PassiveStatus,
    StopSignal,
    read_status,
    request_stop,
)
from geass.crawler.passive.stability import StabilityStatus, StabilityTracker

__all__ = [
    "ALT_GR",
    "COUNTER_NAMES",
    "MODIFIER_NAMES",
    "STOP_EVENT_NAME",
    "AlreadyRunningError",
    "ChangeHint",
    "ConfigPrivacyPolicy",
    "CpuMeter",
    "CpuUsage",
    "HotkeyError",
    "MouseListener",
    "MousePress",
    "NamedStopEvent",
    "ObserverEvent",
    "ObserverSettings",
    "PassiveError",
    "PassiveObserver",
    "PassiveRunner",
    "PassiveStats",
    "PassiveStatus",
    "PauseHotkey",
    "PauseState",
    "PressCapture",
    "PressCapturer",
    "PrivacyPolicy",
    "Sensor",
    "ShortcutListener",
    "ShortcutPressed",
    "ShortcutTracker",
    "StabilityStatus",
    "StabilityTracker",
    "StopSignal",
    "WindowRef",
    "infer_action",
    "key_name",
    "match_element",
    "parse_hotkey",
    "read_status",
    "request_stop",
]
