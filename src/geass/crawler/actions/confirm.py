"""Confirmación de Manuel para las acciones que la puerta no deja pasar sin preguntar.

Una respuesta vale para esa acción concreta y nada más: el ejecutor pregunta otra vez en
cada acción que lo requiera. Ante cualquier duda (respuesta vacía, otra respuesta, fin de
la entrada o interrupción) la respuesta es no.
"""

from collections.abc import Callable
from typing import Protocol

import typer
from pydantic import BaseModel, ConfigDict

from geass.crawler.actions.gate import GateRule

_YES = frozenset({"s", "si", "sí", "y", "yes"})


class ConfirmationRequest(BaseModel):
    """Lo que se le muestra a Manuel para que confirme una acción."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str  # p. ej.: Pulsar "Eliminar" (MenuItem) en notepad.exe
    app: str
    element: str  # etiqueta del elemento
    control_type: str
    rule: GateRule
    pattern: str | None
    reason: str
    is_target: bool  # falso en un paso intermedio de una ruta


class Confirmer(Protocol):
    """Callback que pregunta a Manuel. Devuelve verdadero solo si dijo que sí."""

    who: str  # quién confirma; queda en la auditoría

    def __call__(self, request: ConfirmationRequest) -> bool: ...


class CliConfirmer:
    """Pregunta en la terminal."""

    def __init__(
        self,
        who: str = "cli:manuel",
        *,
        read: Callable[[str], str] = input,
        write: Callable[[str], None] = typer.echo,
    ) -> None:
        self.who = who
        self._read = read
        self._write = write

    def __call__(self, request: ConfirmationRequest) -> bool:
        self._write("Esta acción necesita tu confirmación:")
        self._write(f"  Acción: {request.description}")
        self._write(f"  Motivo: {request.reason}")
        rule = f"  Regla: {request.rule}"
        if request.pattern is not None:
            rule += f" (patrón '{request.pattern}')"
        self._write(rule)
        if not request.is_target:
            self._write("  Es un paso intermedio de la ruta hasta el objetivo.")
        if request.rule == "authorization":
            self._write("  Es un botón de autorización. Solo se pulsa si lo confirmas ahora.")
        try:
            answer = self._read("¿Lo hago? [s/N]: ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().casefold() in _YES


class DenyAllConfirmer:
    """Niega siempre y anota lo que se le pidió. Para el benchmark y los tests."""

    def __init__(self, who: str = "nadie") -> None:
        self.who = who
        self._requests: list[ConfirmationRequest] = []

    @property
    def requests(self) -> list[ConfirmationRequest]:
        """Peticiones recibidas, en orden. Es una copia."""
        return list(self._requests)

    def __call__(self, request: ConfirmationRequest) -> bool:
        self._requests.append(request)
        return False
