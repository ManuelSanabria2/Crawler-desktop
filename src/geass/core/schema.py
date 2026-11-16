"""Base de los esquemas de configuración y manejo de los valores TODO.

Un dato pendiente se escribe en el YAML como la cadena ``TODO``. La carga no falla, pero
leer ese atributo lanza ``ConfigIncompleteError`` con la ruta de la clave y su significado.
El significado sale de la descripción del campo en el modelo.
"""

from collections.abc import Callable, Iterator
from typing import Annotated, Any, Final, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, WrapSerializer, WrapValidator
from pydantic.fields import FieldInfo

from geass.core.errors import ConfigIncompleteError

TODO_MARKER: Final = "TODO"
NO_MEANING: Final = "sin descripción en el esquema"

T = TypeVar("T")


class Pending:
    """Marca interna de un valor que sigue como TODO. Hay una sola instancia: ``PENDING``."""

    _instance: "Pending | None" = None

    def __new__(cls) -> "Pending":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return TODO_MARKER


PENDING: Final = Pending()


def _validate_todo(value: Any, handler: Callable[[Any], Any]) -> Any:
    if value is PENDING or (isinstance(value, str) and value == TODO_MARKER):
        return PENDING
    return handler(value)


def _serialize_todo(value: Any, handler: Callable[[Any], Any]) -> Any:
    if value is PENDING:
        return TODO_MARKER
    return handler(value)


_TODO_VALIDATOR: Final = WrapValidator(_validate_todo)
_TODO_SERIALIZER: Final = WrapSerializer(_serialize_todo)

Todo = Annotated[T, _TODO_VALIDATOR, _TODO_SERIALIZER]
"""Tipo de un campo que admite ``TODO`` en el YAML. Uso: ``a_m: Todo[float] = todo("...")``."""


def todo(meaning: str, **kwargs: Any) -> Any:
    """Declara un campo que puede estar pendiente. ``meaning`` dice qué es y en qué unidad."""
    return Field(description=meaning, **kwargs)


def is_todo_field(info: FieldInfo) -> bool:
    """Indica si un campo fue declarado con ``Todo[...]``."""
    return any(item is _TODO_VALIDATOR for item in info.metadata)


class ConfigModel(BaseModel):
    """Base de todos los modelos de configuración.

    Es inmutable, rechaza claves desconocidas y lanza ``ConfigIncompleteError`` al leer un
    campo que sigue como TODO.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())

    _source: str = PrivateAttr(default="")
    _path: str = PrivateAttr(default="")

    def __getattribute__(self, name: str) -> Any:
        value = super().__getattribute__(name)
        if value is PENDING:
            raise ConfigIncompleteError(
                key=self._key_path(name),
                meaning=self._meaning(name),
                source=self._private("_source"),
            )
        return value

    def _private(self, name: str) -> str:
        private = object.__getattribute__(self, "__pydantic_private__") or {}
        value = private.get(name, "")
        return value if isinstance(value, str) else ""

    def _key_path(self, name: str) -> str:
        prefix = self._private("_path")
        return f"{prefix}.{name}" if prefix else name

    def _meaning(self, name: str) -> str:
        info = type(self).model_fields.get(name)
        return info.description if info is not None and info.description else NO_MEANING

    def is_pending(self, name: str) -> bool:
        """Dice si el campo ``name`` sigue como TODO, sin lanzar error."""
        return self.__dict__.get(name) is PENDING

    def known(self, name: str) -> Any:
        """Devuelve el valor del campo, o ``None`` si sigue como TODO."""
        value = self.__dict__.get(name)
        return None if value is PENDING else value

    def pending_keys(self) -> list[str]:
        """Lista las rutas de todas las claves que siguen como TODO, recursivamente."""
        return [key for key, _ in self.iter_pending()]

    def iter_pending(self) -> Iterator[tuple[str, str]]:
        """Recorre las claves pendientes como pares (ruta, significado)."""
        for name, value in self.__dict__.items():
            if value is PENDING:
                yield self._key_path(name), self._meaning(name)
            else:
                yield from _iter_pending_in(value)


def _iter_pending_in(value: Any) -> Iterator[tuple[str, str]]:
    if isinstance(value, ConfigModel):
        yield from value.iter_pending()
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _iter_pending_in(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_pending_in(item)


def attach_paths(model: ConfigModel, source: str, prefix: str = "") -> None:
    """Registra en cada submodelo el archivo de origen y su ruta dentro del YAML."""
    private = object.__getattribute__(model, "__pydantic_private__")
    private["_source"] = source
    private["_path"] = prefix
    for name, value in model.__dict__.items():
        _attach_in(value, source, f"{prefix}.{name}" if prefix else name)


def _attach_in(value: Any, source: str, path: str) -> None:
    if isinstance(value, ConfigModel):
        attach_paths(value, source, path)
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _attach_in(item, source, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _attach_in(item, source, f"{path}.{key}")
