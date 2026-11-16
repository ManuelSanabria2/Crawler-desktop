"""Recorrido del árbol de UI Automation con límites de profundidad, nodos y tiempo.

Este módulo no depende de Windows: trabaja sobre el protocolo ``RawNode``. El backend real
está en ``backend_win``; los tests usan nodos falsos.
"""

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from geass.crawler.uia.schema import Element, PartialReason, Rect


class UiaNodeError(Exception):
    """Un elemento no se pudo leer: desapareció durante la lectura o falló la llamada COM."""


@dataclass(frozen=True)
class NodeProps:
    """Propiedades de un elemento, ya leídas. No incluye ningún valor de texto."""

    control_type: str
    name: str
    automation_id: str
    class_name: str
    rect: Rect | None
    enabled: bool
    offscreen: bool
    is_password: bool
    patterns: tuple[str, ...]


class RawNode(Protocol):
    """Elemento de la interfaz tal como lo entrega un backend."""

    def read(self) -> NodeProps:
        """Lee las propiedades. Lanza ``UiaNodeError`` si el elemento ya no existe."""
        ...

    def children(self) -> Sequence["RawNode"]:
        """Devuelve los hijos. Lanza ``UiaNodeError`` si el elemento ya no existe."""
        ...


@dataclass(frozen=True)
class DumpLimits:
    """Límites de un volcado. En uso normal salen de ``crawler.yaml``."""

    max_depth: int
    max_nodes: int
    timeout_s: float


@dataclass(frozen=True)
class WalkResult:
    root: Element
    node_count: int
    max_depth_reached: int
    partial_reasons: list[PartialReason]
    error_count: int
    elapsed_s: float

    @property
    def partial(self) -> bool:
        return bool(self.partial_reasons)


@dataclass
class _Draft:
    props: NodeProps
    path: str
    children: list["_Draft"] = field(default_factory=list)


def walk_tree(
    root: RawNode,
    limits: DumpLimits,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> WalkResult:
    """Recorre el árbol en anchura y lo convierte en elementos del esquema.

    Un fallo al leer un elemento o sus hijos no aborta el volcado: se omite esa rama y el
    resultado queda marcado como parcial. Lo mismo ocurre al alcanzar cualquier límite.

    Raises:
        UiaNodeError: si no se puede leer ni siquiera el elemento raíz.
    """
    start = clock()
    reasons: list[PartialReason] = []
    errors = 0

    def mark(reason: PartialReason) -> None:
        if reason not in reasons:
            reasons.append(reason)

    root_draft = _Draft(props=root.read(), path=_segment(root.read().control_type, 0))
    node_count = 1
    max_depth_reached = 0
    queue: deque[tuple[RawNode, _Draft, int]] = deque([(root, root_draft, 0)])

    while queue:
        if clock() - start > limits.timeout_s:
            mark("timeout")
            break
        node, draft, depth = queue.popleft()
        try:
            children = node.children()
        except UiaNodeError:
            errors += 1
            mark("element_error")
            continue
        if not children:
            continue
        if depth >= limits.max_depth:
            mark("max_depth")
            continue

        type_counts: dict[str, int] = {}
        for child in children:
            if node_count >= limits.max_nodes:
                mark("max_nodes")
                break
            try:
                props = child.read()
            except UiaNodeError:
                errors += 1
                mark("element_error")
                continue
            index = type_counts.get(props.control_type, 0)
            type_counts[props.control_type] = index + 1
            child_draft = _Draft(
                props=props, path=f"{draft.path}/{_segment(props.control_type, index)}"
            )
            draft.children.append(child_draft)
            node_count += 1
            max_depth_reached = max(max_depth_reached, depth + 1)
            queue.append((child, child_draft, depth + 1))
        if "max_nodes" in reasons:
            break

    return WalkResult(
        root=_to_element(root_draft),
        node_count=node_count,
        max_depth_reached=max_depth_reached,
        partial_reasons=reasons,
        error_count=errors,
        elapsed_s=clock() - start,
    )


def _segment(control_type: str, index: int) -> str:
    return f"{control_type}[{index}]"


def _to_element(root: _Draft) -> Element:
    """Convierte el borrador en elementos inmutables, de las hojas hacia la raíz."""
    order: list[_Draft] = []
    stack = [root]
    while stack:
        draft = stack.pop()
        order.append(draft)
        stack.extend(draft.children)
    built: dict[int, Element] = {}
    for draft in reversed(order):
        props = draft.props
        built[id(draft)] = Element(
            control_type=props.control_type,
            name=props.name,
            automation_id=props.automation_id,
            class_name=props.class_name,
            rect=props.rect,
            enabled=props.enabled,
            offscreen=props.offscreen,
            is_password=props.is_password,
            patterns=list(props.patterns),
            path=draft.path,
            children=[built[id(child)] for child in draft.children],
        )
    return built[id(root)]
