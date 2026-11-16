"""Órdenes para el ejecutor: qué hacer y qué debe verse después.

Una orden es un dato que da Manuel. Nunca se construye a partir de texto leído en pantalla.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from geass.crawler.actions.direct import MediaAction

RequestKind = Literal["launch", "explorer", "media", "element"]
RequestAction = Literal["invoke", "toggle", "expand", "collapse", "select", "coordinate_click"]

# Verbo de cada operación sobre un elemento; None es "según los patrones del elemento".
_VERBS: dict[str | None, str] = {
    "invoke": "Pulsar",
    "toggle": "Alternar",
    "expand": "Desplegar",
    "collapse": "Contraer",
    "select": "Seleccionar",
    "coordinate_click": "Hacer clic en",
    None: "Usar",
}
_EXECUTABLE_SUFFIX = ".exe"


def _describe_element(
    op: str | None, name: str, automation_id: str, control_type: str, app: str
) -> str:
    """Texto legible de una acción sobre un elemento: Pulsar "Dos" (Button) en x.exe."""
    if name.strip():
        label = f'"{name}"'
    elif automation_id.strip():
        label = f"[{automation_id}]"
    else:
        label = "[sin nombre]"
    kind = f" ({control_type})" if control_type else ""
    return f"{_VERBS[op]} {label}{kind} en {app}"


class ElementExpectation(BaseModel):
    """Un elemento que debe estar presente. Se compara con todos los criterios dados."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = ""  # nombre; se compara normalizado y exacto
    automation_id: str = ""  # exacto
    control_type: str = ""  # exacto

    @model_validator(mode="after")
    def _check(self) -> "ElementExpectation":
        if not self.name.strip() and not self.automation_id.strip():
            raise ValueError("el elemento esperado necesita name o automation_id")
        return self


class Expectation(BaseModel):
    """Lo que debe verse después de la acción. Todas las condiciones dadas a la vez."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    process: str = ""  # ejecutable de la ventana
    title: str = ""  # expresión regular, con re.search e IGNORECASE
    window_class: str = ""  # clase de la ventana, exacta
    new_window: bool = False  # las condiciones se buscan en una ventana que no estaba antes
    element: ElementExpectation | None = None

    @model_validator(mode="after")
    def _check(self) -> "Expectation":
        conditions = (self.process, self.title, self.window_class, self.new_window, self.element)
        if not any(conditions):
            raise ValueError("la expectativa necesita al menos una condición")
        try:
            re.compile(self.title, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"title no es una expresión regular válida: {error}") from error
        return self


class ActionRequest(BaseModel):
    """Una orden para el ejecutor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RequestKind
    app: str = ""  # launch: nombre aproximado de la app; element: ejecutable, p. ej. "notepad.exe"
    path: str | None = None  # explorer: carpeta, opcional
    media: MediaAction | None = None  # media: la acción multimedia
    name: str = ""  # element: nombre del elemento
    automation_id: str = ""  # element: AutomationId
    control_type: str = ""  # element: tipo de control, opcional
    action: RequestAction | None = None  # element: None = según los patrones del elemento
    authorize: bool = False  # element: orden explícita de pulsar un botón de autorización
    expect: Expectation | None = None

    @model_validator(mode="after")
    def _check(self) -> "ActionRequest":
        element_fields = bool(self.name or self.automation_id or self.control_type)
        if self.kind == "launch":
            if not self.app.strip():
                raise ValueError("launch necesita app: el nombre de la app que hay que abrir")
            if self.path is not None or self.media is not None or self.action is not None:
                raise ValueError("launch no admite path, media ni action")
            if element_fields or self.authorize:
                raise ValueError("launch no admite name, automation_id, control_type ni authorize")
        elif self.kind == "explorer":
            if self.app or element_fields:
                raise ValueError("explorer no admite app, name, automation_id ni control_type")
            if self.media is not None or self.action is not None or self.authorize:
                raise ValueError("explorer no admite media, action ni authorize")
        elif self.kind == "media":
            if self.media is None:
                raise ValueError("media necesita la acción multimedia")
            if self.app or element_fields or self.path is not None or self.action is not None:
                raise ValueError("media solo admite la acción multimedia")
            if self.authorize:
                raise ValueError("media no admite authorize")
            if self.expect is not None:
                raise ValueError("una tecla multimedia no tiene nada que comprobar: sin expect")
        else:
            if not self.app.strip().lower().endswith(_EXECUTABLE_SUFFIX):
                raise ValueError("element necesita app: el ejecutable, por ejemplo notepad.exe")
            if not self.name.strip() and not self.automation_id.strip():
                raise ValueError("element necesita name o automation_id")
            if self.path is not None or self.media is not None:
                raise ValueError("element no admite path ni media")
        return self

    def describe(self) -> str:
        """Texto legible de la orden."""
        if self.kind == "launch":
            return f'Abrir "{self.app}"'
        if self.kind == "explorer":
            return "Abrir el explorador" if not self.path else f"Abrir el explorador en {self.path}"
        if self.kind == "media":
            return f"Tecla multimedia {self.media}"
        return _describe_element(
            self.action, self.name, self.automation_id, self.control_type, self.app
        )
