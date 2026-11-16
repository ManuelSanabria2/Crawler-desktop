"""Firmas de estado: estructural y detallada (sección 6.3 de INSTRUCCIONES.md).

- La firma estructural identifica la vista. Es un hash del proceso, la clase de ventana y
  el multiconjunto, por nivel de profundidad, de tipo de control más AutomationId (o nombre
  normalizado si no hay AutomationId).
- La firma detallada distingue variantes de la misma vista. Añade a cada elemento su nombre
  normalizado y si está habilitado.

El orden de los hermanos no influye en ninguna de las dos.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from geass.core.config import load_crawler_config
from geass.crawler.signature.rules import SignatureRules
from geass.crawler.uia.schema import Element, WindowDump

ALGORITHM_VERSION = 1

Visibility = Literal["kept", "ignored_type", "volatile_content", "pruned_empty"]
StructuralKey = tuple[int, str, str, str]
DetailedKey = tuple[int, str, str, str, bool, str]


@dataclass(frozen=True)
class Token:
    """Lo que las firmas ven de un elemento."""

    depth: int
    control_type: str
    automation_id: str
    name: str
    enabled: bool
    bucket: str

    @property
    def structural_key(self) -> StructuralKey:
        identity = f"id={self.automation_id}" if self.automation_id else f"name={self.name}"
        return (self.depth, self.control_type, identity, self.bucket)

    @property
    def detailed_key(self) -> DetailedKey:
        return (
            self.depth,
            self.control_type,
            self.automation_id,
            self.name,
            self.enabled,
            self.bucket,
        )


@dataclass(frozen=True)
class Visit:
    """Un elemento del volcado y qué hicieron las reglas con él."""

    depth: int
    element: Element
    visibility: Visibility
    token: Token | None


class StateSignature(BaseModel):
    """Firmas de un volcado. Si ``partial`` es verdadero, salen de un árbol incompleto."""

    model_config = ConfigDict(frozen=True)

    structural: str
    detailed: str
    process_name: str
    window_class: str
    stable_title: str
    token_count: int
    partial: bool


def default_rules() -> SignatureRules:
    """Reglas de ``crawler.yaml``."""
    return SignatureRules.from_config(load_crawler_config().signature)


def visit_dump(dump: WindowDump, rules: SignatureRules) -> list[Visit]:
    """Aplica las reglas a cada elemento del volcado."""
    title = rules.stable_title(dump.title, dump.process_name)
    visits: list[Visit] = []
    _visit(dump.root, 0, rules, title, visits)
    return visits


def tokens_of(visits: list[Visit]) -> list[Token]:
    return [visit.token for visit in visits if visit.token is not None]


def compute_signature(dump: WindowDump, rules: SignatureRules | None = None) -> StateSignature:
    """Calcula la firma estructural y la detallada de un volcado."""
    rules = rules if rules is not None else default_rules()
    tokens = tokens_of(visit_dump(dump, rules))
    return StateSignature(
        structural=_digest("structural", dump, Counter(t.structural_key for t in tokens)),
        detailed=_digest("detailed", dump, Counter(t.detailed_key for t in tokens)),
        process_name=dump.process_name,
        window_class=dump.class_name,
        stable_title=rules.stable_title(dump.title, dump.process_name),
        token_count=len(tokens),
        partial=dump.partial,
    )


def _visit(
    element: Element, depth: int, rules: SignatureRules, title: str, out: list[Visit]
) -> bool:
    """Visita el subárbol y devuelve si el elemento sobrevive a las reglas."""
    if element.control_type in rules.ignored_control_types:
        _mark_subtree(element, depth, "ignored_type", out)
        return False

    name = title if depth == 0 else rules.normalize_name(element.name)
    position = len(out)
    out.append(Visit(depth, element, "kept", None))

    if element.control_type in rules.volatile_container_types:
        counted = 0
        for child in element.children:
            if child.control_type in rules.ignored_control_types:
                _mark_subtree(child, depth + 1, "ignored_type", out)
            else:
                counted += 1
                _mark_subtree(child, depth + 1, "volatile_content", out)
        bucket = rules.count_bucket(counted)
        out[position] = Visit(depth, element, "kept", _token(element, depth, name, bucket))
        return True

    survivors = 0
    for child in element.children:
        survivors += _visit(child, depth + 1, rules, title, out)
    if depth > 0 and survivors == 0 and element.control_type in rules.prune_empty_control_types:
        out[position] = Visit(depth, element, "pruned_empty", None)
        return False
    out[position] = Visit(depth, element, "kept", _token(element, depth, name, ""))
    return True


def _token(element: Element, depth: int, name: str, bucket: str) -> Token:
    return Token(
        depth=depth,
        control_type=element.control_type,
        automation_id=element.automation_id,
        name=name,
        enabled=element.enabled,
        bucket=bucket,
    )


def _mark_subtree(element: Element, depth: int, visibility: Visibility, out: list[Visit]) -> None:
    stack = [(element, depth)]
    while stack:
        current, level = stack.pop()
        out.append(Visit(level, current, visibility, None))
        stack.extend((child, level + 1) for child in reversed(current.children))


def _digest(kind: str, dump: WindowDump, counts: Counter) -> str:
    payload = {
        "version": ALGORITHM_VERSION,
        "kind": kind,
        "process": dump.process_name.lower(),
        "window_class": dump.class_name,
        "tokens": sorted([list(key), count] for key, count in counts.items()),
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
