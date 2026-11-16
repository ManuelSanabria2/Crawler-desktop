"""Lanzador, acciones directas, ejecutor y puerta de seguridad.

Contiene el índice de apps, el lanzador por nombre aproximado, las acciones directas
(explorador y teclas multimedia), el ejecutor de órdenes sobre la interfaz, la puerta de
seguridad que clasifica cada acción, la confirmación y el registro de auditoría. Solo se usa
por orden expresa de Manuel; el crawler pasivo nunca importa este paquete.

Todo lo que toca el sistema operativo está en ``system``. ``WinDesktop`` importa pywinauto
al crearse, no al importar el paquete.
"""

from geass.crawler.actions.audit import AuditLog
from geass.crawler.actions.confirm import (
    CliConfirmer,
    ConfirmationRequest,
    Confirmer,
    DenyAllConfirmer,
)
from geass.crawler.actions.desktop_win import WinDesktop
from geass.crawler.actions.direct import DIRECT_ACTION_KEYS, MediaAction, media, open_explorer
from geass.crawler.actions.errors import ActionBlockedError, ActionDisabledError, ActionError
from geass.crawler.actions.executor import Attempt, ExecutionResult, ExecutionStatus, Executor
from geass.crawler.actions.gate import (
    NEVER_AUTOMATE_PROCESSES,
    GateDecision,
    GateOp,
    GateRule,
    GateVerdict,
    SafetyGate,
    split_identifier,
)
from geass.crawler.actions.index import AppEntry, AppIndex, AppSource
from geass.crawler.actions.launcher import (
    AppMatch,
    LaunchResult,
    LaunchStatus,
    launch,
    match_app,
)
from geass.crawler.actions.live import (
    Desktop,
    LiveElement,
    PatternOp,
    PatternState,
    VisualCandidate,
    VisualDetector,
    VisualQuery,
    WindowRef,
    element_label,
)
from geass.crawler.actions.request import (
    ActionRequest,
    ElementExpectation,
    Expectation,
    RequestAction,
    RequestKind,
)
from geass.crawler.actions.verify import (
    Baseline,
    capture_baseline,
    expectation_met,
    ui_fingerprint,
    wait_until,
)

__all__ = [
    "ActionBlockedError",
    "ActionDisabledError",
    "ActionError",
    "ActionRequest",
    "AppEntry",
    "AppIndex",
    "AppMatch",
    "AppSource",
    "Attempt",
    "AuditLog",
    "Baseline",
    "CliConfirmer",
    "ConfirmationRequest",
    "Confirmer",
    "DIRECT_ACTION_KEYS",
    "DenyAllConfirmer",
    "Desktop",
    "ElementExpectation",
    "ExecutionResult",
    "ExecutionStatus",
    "Executor",
    "Expectation",
    "GateDecision",
    "GateOp",
    "GateRule",
    "GateVerdict",
    "LaunchResult",
    "LaunchStatus",
    "LiveElement",
    "MediaAction",
    "NEVER_AUTOMATE_PROCESSES",
    "PatternOp",
    "PatternState",
    "RequestAction",
    "RequestKind",
    "SafetyGate",
    "VisualCandidate",
    "VisualDetector",
    "VisualQuery",
    "WinDesktop",
    "WindowRef",
    "capture_baseline",
    "element_label",
    "expectation_met",
    "launch",
    "match_app",
    "media",
    "open_explorer",
    "split_identifier",
    "ui_fingerprint",
    "wait_until",
]
