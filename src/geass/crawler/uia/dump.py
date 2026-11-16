"""Volcado de una ventana a un ``WindowDump``. Solo lee: nunca actúa sobre la interfaz."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from geass.core.config import load_crawler_config
from geass.core.errors import GeassError
from geass.core.log import get_logger
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.uia.reader import DumpLimits, RawNode, UiaNodeError, walk_tree
from geass.crawler.uia.schema import WindowDump

logger = get_logger(__name__)


class UiaError(GeassError):
    """No se pudo volcar la ventana."""


class WindowNotFoundError(UiaError):
    """No hay ventana activa o el handle no corresponde a una ventana."""


class ExcludedAppError(UiaError):
    """La ventana pertenece a una app excluida en ``crawler.yaml``. No se lee nada de ella."""


class WindowInfoLike(Protocol):
    handle: int
    title: str
    class_name: str
    process_id: int
    process_name: str


class UiaBackend(Protocol):
    """Lo que ``dump_window`` necesita del sistema. El real es ``WinUiaBackend``."""

    def foreground_handle(self) -> int: ...

    def is_window(self, handle: int) -> bool: ...

    def window_info(self, handle: int) -> WindowInfoLike: ...

    def root_node(self, handle: int) -> RawNode: ...


def limits_from_config(config: CrawlerConfig) -> DumpLimits:
    """Límites del volcado según ``crawler.yaml``."""
    return DumpLimits(
        max_depth=config.uia.max_depth,
        max_nodes=config.uia.max_nodes,
        timeout_s=config.uia.dump_timeout_s,
    )


def dump_window(
    handle: int | None = None,
    *,
    limits: DumpLimits | None = None,
    excluded_apps: Sequence[str] | None = None,
    config: CrawlerConfig | None = None,
    backend: UiaBackend | None = None,
) -> WindowDump:
    """Vuelca el árbol de UI Automation de una ventana.

    Args:
        handle: handle de la ventana. Si es ``None`` se usa la ventana en primer plano.
        limits: límites de profundidad, nodos y tiempo. Por defecto, los de ``crawler.yaml``.
        excluded_apps: ejecutables de los que no se lee nada. Por defecto, ``apps.excluded``
            de ``crawler.yaml``; si esa clave sigue como TODO se lanza ``ConfigIncompleteError``.
        config: configuración del crawler ya cargada. Por defecto se carga ``crawler.yaml``.
        backend: acceso al sistema. Por defecto, UI Automation mediante pywinauto.

    Returns:
        El volcado. Si ``partial`` es verdadero, falta parte del árbol y
        ``partial_reasons`` dice por qué.

    Raises:
        WindowNotFoundError: no hay ventana activa o el handle no es válido.
        ExcludedAppError: la ventana es de una app excluida.
        UiaError: no se pudo leer ni la raíz de la ventana.
    """
    if limits is None or excluded_apps is None or backend is None:
        config = config if config is not None else load_crawler_config()
    if limits is None:
        limits = limits_from_config(config)
    if excluded_apps is None:
        excluded_apps = config.apps.excluded
    if backend is None:
        from geass.crawler.uia.backend_win import WinUiaBackend

        backend = WinUiaBackend(tree_view=config.uia.tree_view)

    if handle is None:
        handle = backend.foreground_handle()
        if not handle:
            raise WindowNotFoundError("No se pudo determinar la ventana en primer plano.")
    elif not backend.is_window(handle):
        raise WindowNotFoundError(f"El handle {handle} no corresponde a una ventana.")

    info = backend.window_info(handle)
    excluded = {name.strip().lower() for name in excluded_apps}
    process_name = info.process_name.lower()
    if not process_name and excluded:
        raise ExcludedAppError(
            "No se pudo identificar el proceso de la ventana; no se lee por precaución."
        )
    if process_name in excluded:
        raise ExcludedAppError(f"La app {info.process_name} está excluida en crawler.yaml.")

    try:
        result = walk_tree(backend.root_node(handle), limits)
    except UiaNodeError as error:
        raise UiaError(f"No se pudo leer la ventana {handle}: {error}") from error

    if result.partial:
        logger.warning(
            "Volcado parcial de %s (%s): %d nodos, %d errores",
            info.process_name,
            ", ".join(result.partial_reasons),
            result.node_count,
            result.error_count,
        )
    return WindowDump(
        handle=handle,
        title=info.title,
        class_name=info.class_name,
        process_id=info.process_id,
        process_name=info.process_name,
        captured_at=datetime.now(UTC),
        elapsed_s=result.elapsed_s,
        node_count=result.node_count,
        max_depth_reached=result.max_depth_reached,
        partial=result.partial,
        partial_reasons=result.partial_reasons,
        error_count=result.error_count,
        root=result.root,
    )
