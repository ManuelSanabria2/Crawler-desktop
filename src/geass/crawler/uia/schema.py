"""Esquema del volcado de una ventana leída con UI Automation.

Por privacidad el esquema no tiene ningún campo para el valor de los campos de texto ni
de los campos de contraseña: solo se registra que existen y de qué tipo son.
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

PartialReason = Literal["max_depth", "max_nodes", "timeout", "element_error"]


class Rect(BaseModel):
    """Rectángulo en coordenadas de pantalla (píxeles)."""

    model_config = ConfigDict(frozen=True)

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class Element(BaseModel):
    """Nodo del árbol de UI Automation."""

    model_config = ConfigDict(frozen=True)

    control_type: str
    name: str
    automation_id: str
    class_name: str
    rect: Rect | None
    enabled: bool
    offscreen: bool
    is_password: bool
    patterns: list[str]
    path: str
    children: list["Element"] = []

    def iter_tree(self) -> Iterator["Element"]:
        """Recorre este elemento y todos sus descendientes, en profundidad."""
        stack = [self]
        while stack:
            element = stack.pop()
            yield element
            stack.extend(reversed(element.children))

    def find_all(
        self,
        *,
        control_type: str | None = None,
        automation_id: str | None = None,
        name: str | None = None,
    ) -> list["Element"]:
        """Busca descendientes (y este mismo) que cumplan todos los criterios dados."""
        return [
            element
            for element in self.iter_tree()
            if (control_type is None or element.control_type == control_type)
            and (automation_id is None or element.automation_id == automation_id)
            and (name is None or element.name == name)
        ]


class WindowDump(BaseModel):
    """Volcado de una ventana. Si ``partial`` es verdadero, el árbol no está completo."""

    model_config = ConfigDict(frozen=True)

    handle: int
    title: str
    class_name: str
    process_id: int
    process_name: str
    captured_at: datetime
    elapsed_s: float
    node_count: int
    max_depth_reached: int
    partial: bool
    partial_reasons: list[PartialReason]
    error_count: int
    root: Element
