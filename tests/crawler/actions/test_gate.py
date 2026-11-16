"""Tests de la puerta de seguridad (casos 7 a 22 de la especificación C08a).

Usan el safety.yaml real salvo donde se dice, y una ventana de SystemSettings.exe.
"""

from types import SimpleNamespace

import pytest

from geass.core.config import load_crawler_config, load_safety_config
from geass.core.errors import ConfigIncompleteError
from geass.crawler.actions import GateDecision, SafetyGate, WindowRef, split_identifier
from geass.crawler.uia.reader import NodeProps

SAFETY = load_safety_config()
CONFIG = load_crawler_config()
SETTINGS_WINDOW = WindowRef(
    handle=1,
    process_id=10,
    process_name="SystemSettings.exe",
    class_name="ApplicationFrameWindow",
    title="Configuración",
    rect=None,
)


def props(name: str = "", automation_id: str = "", *, is_password: bool = False) -> NodeProps:
    return NodeProps(
        control_type="Button",
        name=name,
        automation_id=automation_id,
        class_name="",
        rect=None,
        enabled=True,
        offscreen=False,
        is_password=is_password,
        patterns=("Invoke",),
    )


def window(process_name: str, title: str = "") -> WindowRef:
    return WindowRef(
        handle=2, process_id=20, process_name=process_name, class_name="", title=title, rect=None
    )


def check(
    name: str = "",
    automation_id: str = "",
    *,
    gate: SafetyGate | None = None,
    op: str = "invoke",
    on: WindowRef | None = SETTINGS_WINDOW,
    is_target: bool = True,
    authorize: bool = False,
    is_password: bool = False,
) -> GateDecision:
    gate = gate if gate is not None else SafetyGate(SAFETY, CONFIG)
    return gate.check(
        props=props(name, automation_id, is_password=is_password),
        op=op,  # type: ignore[arg-type]
        window=on,
        is_target=is_target,
        authorize=authorize,
    )


def titled(pattern: str) -> str:
    return pattern[0].upper() + pattern[1:]


def test_free_button() -> None:
    decision = check("Pantalla")
    assert (decision.verdict, decision.rule) == ("free", "free")


@pytest.mark.parametrize("pattern", SAFETY.crawler.blocklist_patterns)
def test_blocklist_patterns(pattern: str) -> None:
    decision = check(f"{titled(pattern)} todo")
    assert (decision.verdict, decision.rule) == ("confirm", "blocklist_patterns")
    assert decision.pattern == pattern


@pytest.mark.parametrize("pattern", SAFETY.crawler.require_confirmation_patterns)
def test_require_confirmation_patterns(pattern: str) -> None:
    decision = check(f"{titled(pattern)} todo")
    assert decision.verdict == "confirm"
    if pattern == "apagar":
        # "apagar" contiene "pagar", y la lista de bloqueo va antes.
        assert (decision.rule, decision.pattern) == ("blocklist_patterns", "pagar")
    else:
        assert (decision.rule, decision.pattern) == ("require_confirmation_patterns", pattern)


@pytest.mark.parametrize("pattern", SAFETY.crawler.confirmation_dialog_accept_patterns)
def test_accept_patterns(pattern: str) -> None:
    decision = check(titled(pattern))
    assert (decision.verdict, decision.rule) == ("confirm", "confirmation_dialog_accept_patterns")
    assert decision.pattern == pattern


@pytest.mark.parametrize(
    ("name", "verdict"),
    [
        ("Sistema", "free"),
        ("Token", "free"),
        ("Bookmarks", "free"),
        ("Siguiente", "free"),
        ("Sí, continuar", "confirm"),
        ("Aceptar y cerrar", "confirm"),
    ],
)
def test_accept_patterns_match_whole_words(name: str, verdict: str) -> None:
    decision = check(name)
    assert decision.verdict == verdict
    if verdict == "confirm":
        assert decision.rule == "confirmation_dialog_accept_patterns"


def test_blocklist_matches_substrings() -> None:
    decision = check("Formato")
    assert (decision.verdict, decision.rule, decision.pattern) == (
        "confirm",
        "blocklist_patterns",
        "format",
    )
    # Comportamiento elegido: "eliminacion" no contiene "eliminar".
    assert check("Eliminación").verdict == "free"


def test_automation_id_matches_words() -> None:
    decision = check(automation_id="DeleteButton")
    assert (decision.verdict, decision.rule) == ("confirm", "blocklist_patterns")
    decision = check(automation_id="OkButton")
    assert (decision.verdict, decision.rule) == ("confirm", "confirmation_dialog_accept_patterns")
    decision = check("Brillo", "SystemSettings_Display_Brightness")
    assert decision.verdict == "free"


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("OkButton", "Ok Button"),
        ("num2Button", "num 2 Button"),
        ("SystemSettings_Input_Mouse", "System Settings Input Mouse"),
        ("a-b.c", "a b c"),
    ],
)
def test_split_identifier(identifier: str, expected: str) -> None:
    assert split_identifier(identifier) == expected


def test_unnamed_needs_confirmation() -> None:
    decision = check()
    assert (decision.verdict, decision.rule) == ("confirm", "unnamed")


def test_password_is_forbidden() -> None:
    decision = check("Pantalla", is_password=True)
    assert (decision.verdict, decision.rule) == ("forbidden", "password")


@pytest.mark.parametrize("op", ["set_value", "unknown"])
def test_unsupported_ops(op: str) -> None:
    decision = check("Pantalla", op=op)
    assert (decision.verdict, decision.rule) == ("forbidden", "unsupported_action")


@pytest.mark.parametrize(
    ("on", "rule"),
    [
        (None, "unknown_window"),
        (window(""), "unknown_window"),
        (window("chrome.exe"), "excluded_app"),
        (window("consent.exe"), "never_automate"),
        (window("LogonUI.exe"), "never_automate"),
    ],
)
def test_forbidden_windows(on: WindowRef | None, rule: str) -> None:
    decision = check("Pantalla", on=on)
    assert (decision.verdict, decision.rule) == ("forbidden", rule)


def test_check_app() -> None:
    gate = SafetyGate(SAFETY, CONFIG)
    decision = gate.check_app("Chrome.EXE")
    assert decision is not None and decision.rule == "excluded_app"
    assert gate.check_app("lockapp.exe") is not None
    assert gate.check_app("notepad.exe") is None


@pytest.mark.parametrize("name", ["Permitir", "Allow", "Aprobar", "Conceder acceso"])
def test_authorization_buttons_need_an_explicit_order(name: str) -> None:
    for is_target, authorize in ((False, True), (True, False), (False, False)):
        decision = check(name, is_target=is_target, authorize=authorize)
        assert (decision.verdict, decision.rule) == ("forbidden", "authorization")
    reason = check(name).reason
    assert reason == (
        "Botón de autorización: solo se pulsa con una orden explícita y actual tuya para esta "
        "acción concreta."
    )


def test_authorization_with_pending_allowed_windows() -> None:
    with pytest.raises(ConfigIncompleteError, match="allowed_windows"):
        check("Permitir", is_target=True, authorize=True)


def test_authorization_allowed_windows(fakes: SimpleNamespace) -> None:
    safety = fakes.safety_with_allowed_windows(
        [{"executable": "foo.exe", "title_pattern": "Permisos"}]
    )
    gate = SafetyGate(safety, CONFIG)

    def decide(on: WindowRef) -> GateDecision:
        return check("Permitir", gate=gate, on=on, is_target=True, authorize=True)

    decision = decide(window("foo.exe", "Permisos de Foo"))
    assert (decision.verdict, decision.rule) == ("confirm", "authorization")
    decision = decide(window("bar.exe", "Permisos de Foo"))
    assert (decision.verdict, decision.rule) == ("forbidden", "authorization_not_allowed")
    decision = decide(window("FOO.EXE", "Otra cosa"))
    assert (decision.verdict, decision.rule) == ("forbidden", "authorization_not_allowed")


def test_precedence_and_unknown_never_automate(fakes: SimpleNamespace) -> None:
    decision = check("Permitir y eliminar")
    assert (decision.verdict, decision.rule) == ("forbidden", "authorization")
    safety = fakes.safety_from_yaml(never_automate=["uac", "pantalla_rara"])
    with pytest.raises(ValueError, match="pantalla_rara"):
        SafetyGate(safety, CONFIG)
