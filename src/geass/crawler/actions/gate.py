"""Puerta de seguridad del ejecutor: cada acción es libre, requiere confirmación o está prohibida.

Las listas salen de la sección ``crawler`` de safety.yaml y de ``apps.excluded`` de
crawler.yaml. Ante la duda la puerta no deja pasar: un elemento sin nombre pide
confirmación, y una ventana que no se puede identificar está prohibida. Los nombres
leídos de la pantalla solo se comparan con esas listas; nunca se obedecen.
"""

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions.live import WindowRef
from geass.crawler.graph.text import normalize_text
from geass.crawler.uia.reader import NodeProps

GateVerdict = Literal["free", "confirm", "forbidden"]
GateOp = Literal[
    "invoke", "toggle", "expand", "collapse", "select", "coordinate_click", "set_value", "unknown"
]
GateRule = Literal[
    "unknown_window",
    "excluded_app",
    "never_automate",
    "unsupported_action",
    "password",
    "authorization",
    "authorization_not_allowed",
    "blocklist_patterns",
    "confirmation_dialog_accept_patterns",
    "require_confirmation_patterns",
    "unnamed",
    "free",
]

# Procesos de cada entrada de safety.crawler.never_automate. Corren en el escritorio seguro.
NEVER_AUTOMATE_PROCESSES: dict[str, tuple[str, ...]] = {
    "uac": ("consent.exe",),
    "logon_screen": ("logonui.exe", "lockapp.exe"),
}

_UNSUPPORTED_OPS = frozenset({"set_value", "unknown"})
_IDENTIFIER_SEPARATORS = frozenset("_-.")


class GateDecision(BaseModel):
    """Lo que decidió la puerta sobre una acción."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: GateVerdict
    rule: GateRule
    reason: str  # en español, para mostrar a Manuel
    pattern: str | None = None  # patrón de safety.yaml que coincidió, tal como está escrito


def split_identifier(identifier: str) -> str:
    """Separa un AutomationId en palabras.

    "OkButton" da "Ok Button"; "num2Button", "num 2 Button". Inserta un espacio entre
    minúscula o dígito y mayúscula, y entre letra y dígito (en los dos sentidos); cambia
    ``_``, ``-`` y ``.`` por espacios.
    """
    out: list[str] = []
    previous = ""
    for char in identifier:
        if char in _IDENTIFIER_SEPARATORS:
            out.append(" ")
            previous = " "
            continue
        if previous and (
            ((previous.islower() or previous.isdigit()) and char.isupper())
            or (previous.isalpha() and char.isdigit())
            or (previous.isdigit() and char.isalpha())
        ):
            out.append(" ")
        out.append(char)
        previous = char
    return "".join(out)


def _unknown_window() -> GateDecision:
    return GateDecision(
        verdict="forbidden",
        rule="unknown_window",
        reason="No se pudo confirmar en qué ventana se está: no se actúa.",
    )


def _same_executable(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _words(text: str) -> list[str]:
    """Palabras del texto: todo lo que no es letra o dígito separa."""
    return "".join(char if char.isalnum() else " " for char in text).split()


def _contains_words(text: str, pattern: str) -> bool:
    """La secuencia de palabras del patrón aparece seguida en la del texto."""
    haystack, needle = _words(text), _words(pattern)
    if not needle:
        return False
    size = len(needle)
    return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))


class _PatternList:
    """Una lista de patrones de safety.yaml, con su forma original y la normalizada."""

    def __init__(self, patterns: Sequence[str]) -> None:
        self._items = [
            (pattern, normalize_text(pattern)) for pattern in patterns if normalize_text(pattern)
        ]

    def first_match(self, name: str, ident: str, *, name_by_word: bool) -> str | None:
        """Primer patrón (en el orden del YAML) que coincide con el nombre o el identificador."""
        for original, normalized in self._items:
            if name:
                found = _contains_words(name, normalized) if name_by_word else normalized in name
                if found:
                    return original
            if ident and _contains_words(ident, normalized):
                return original
        return None


class SafetyGate:
    """Clasifica las acciones del ejecutor según safety.yaml."""

    def __init__(self, safety: SafetyConfig, config: CrawlerConfig) -> None:
        """
        Raises:
            ValueError: una entrada de ``never_automate`` no tiene procesos conocidos en
                ``NEVER_AUTOMATE_PROCESSES``. Así una entrada nueva no se ignora en silencio.
        """
        crawler = safety.crawler
        unknown = [name for name in crawler.never_automate if name not in NEVER_AUTOMATE_PROCESSES]
        if unknown:
            raise ValueError(
                "Entradas de never_automate en safety.yaml sin procesos conocidos: "
                f"{', '.join(unknown)}. Hay que añadirlas a NEVER_AUTOMATE_PROCESSES."
            )
        self._safety = safety
        self._excluded = frozenset(name.strip().casefold() for name in config.apps.excluded)
        self._never = frozenset(
            process.casefold()
            for name in crawler.never_automate
            for process in NEVER_AUTOMATE_PROCESSES[name]
        )
        self._authorization = _PatternList(crawler.authorization.button_patterns)
        self._blocklist = _PatternList(crawler.blocklist_patterns)
        self._accept = _PatternList(crawler.confirmation_dialog_accept_patterns)
        self._require = _PatternList(crawler.require_confirmation_patterns)

    def check_app(self, executable: str) -> GateDecision | None:
        """Prohíbe las apps excluidas y las de ``never_automate``. Si no, ``None``."""
        key = executable.strip().casefold()
        if key in self._excluded:
            return GateDecision(
                verdict="forbidden",
                rule="excluded_app",
                reason=f"{executable} está en apps.excluded de crawler.yaml: no se lee ni se toca.",
            )
        if key in self._never:
            return GateDecision(
                verdict="forbidden",
                rule="never_automate",
                reason=(
                    f"{executable} corre en el escritorio seguro (UAC o inicio de sesión): "
                    "queda fuera del alcance a propósito."
                ),
            )
        return None

    def check_window(self, window: WindowRef | None) -> GateDecision | None:
        """Prohíbe una ventana que no se puede identificar, y las de ``check_app``."""
        if window is None or not window.process_name.strip():
            return _unknown_window()
        return self.check_app(window.process_name)

    def check(
        self,
        *,
        props: NodeProps,
        op: GateOp,
        window: WindowRef | None,
        is_target: bool,
        authorize: bool,
    ) -> GateDecision:
        """Decide sobre una acción. Gana la primera regla que se cumpla.

        Raises:
            ConfigIncompleteError: se pidió pulsar un botón de autorización y
                ``authorization.allowed_windows`` de safety.yaml sigue como TODO.
        """
        decision = self.check_window(window)
        if decision is not None:
            return decision
        if window is None:  # check_window ya la prohíbe; esto solo informa al tipado
            return _unknown_window()
        if op in _UNSUPPORTED_OPS:
            return GateDecision(
                verdict="forbidden",
                rule="unsupported_action",
                reason="El ejecutor no escribe texto ni hace acciones desconocidas.",
            )
        if props.is_password:
            return GateDecision(
                verdict="forbidden",
                rule="password",
                reason="Es un campo de contraseña: nunca se toca.",
            )

        name = normalize_text(props.name)
        ident = normalize_text(split_identifier(props.automation_id))

        pattern = self._authorization.first_match(name, ident, name_by_word=False)
        if pattern is not None:
            return self._authorization_decision(pattern, window, is_target, authorize)

        pattern = self._blocklist.first_match(name, ident, name_by_word=False)
        if pattern is not None:
            return GateDecision(
                verdict="confirm",
                rule="blocklist_patterns",
                reason="Coincide con la lista de bloqueo de safety.yaml: acción destructiva.",
                pattern=pattern,
            )
        pattern = self._accept.first_match(name, ident, name_by_word=True)
        if pattern is not None:
            return GateDecision(
                verdict="confirm",
                rule="confirmation_dialog_accept_patterns",
                reason="Parece el botón de aceptar de un diálogo de confirmación.",
                pattern=pattern,
            )
        pattern = self._require.first_match(name, ident, name_by_word=False)
        if pattern is not None:
            return GateDecision(
                verdict="confirm",
                rule="require_confirmation_patterns",
                reason="safety.yaml pide confirmación para esta acción.",
                pattern=pattern,
            )
        if not name and not ident:
            return GateDecision(
                verdict="confirm",
                rule="unnamed",
                reason="Elemento sin nombre: no se puede comprobar con las listas de safety.yaml.",
            )
        return GateDecision(verdict="free", rule="free", reason="No coincide con ninguna lista.")

    def _authorization_decision(
        self, pattern: str, window: WindowRef, is_target: bool, authorize: bool
    ) -> GateDecision:
        if not (is_target and authorize):
            return GateDecision(
                verdict="forbidden",
                rule="authorization",
                reason=(
                    "Botón de autorización: solo se pulsa con una orden explícita y actual tuya "
                    "para esta acción concreta."
                ),
                pattern=pattern,
            )
        # Si allowed_windows sigue como TODO, esta lectura lanza ConfigIncompleteError.
        allowed = self._safety.crawler.authorization.allowed_windows
        for entry in allowed:
            if _same_executable(entry.executable, window.process_name) and re.search(
                entry.title_pattern, window.title, re.IGNORECASE
            ):
                return GateDecision(
                    verdict="confirm",
                    rule="authorization",
                    reason=(
                        "Botón de autorización en una ventana permitida: solo se pulsa si lo "
                        "confirmas ahora."
                    ),
                    pattern=pattern,
                )
        return GateDecision(
            verdict="forbidden",
            rule="authorization_not_allowed",
            reason=(
                "Botón de autorización en una ventana que no está en "
                "authorization.allowed_windows de safety.yaml."
            ),
            pattern=pattern,
        )
