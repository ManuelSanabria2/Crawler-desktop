"""Guarda un volcado en la base: app, estado y elementos.

Privacidad:

- No se guarda nada de las apps excluidas en ``crawler.yaml``.
- Solo se guardan los elementos que las reglas de firma conservan. El contenido de los
  contenedores volátiles (listas, cuadrículas, documentos, árboles, tablas) no se guarda:
  ahí están los nombres de archivos, los correos y el texto de los documentos.
- El título de la ventana se guarda solo en su parte estable, sin el nombre del archivo.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from geass.core.config import load_crawler_config
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.signature.compute import compute_signature, visit_dump
from geass.crawler.signature.rules import SignatureRules
from geass.crawler.store.db import CrawlerStore, StoreError
from geass.crawler.store.records import ElementInput, StateRecord
from geass.crawler.uia.schema import Rect, WindowDump


class ExcludedAppRecordError(StoreError):
    """Se intentó guardar un volcado de una app excluida."""


@dataclass(frozen=True)
class RecordedState:
    """Resultado de guardar un volcado."""

    app_id: int
    state: StateRecord
    is_new: bool
    element_ids: dict[str, int]


def record_dump(
    store: CrawlerStore,
    dump: WindowDump,
    *,
    rules: SignatureRules | None = None,
    excluded_apps: Sequence[str] | None = None,
    config: CrawlerConfig | None = None,
    screenshot_path: str | None = None,
) -> RecordedState:
    """Registra la app, el estado y los elementos de un volcado.

    Args:
        store: base del crawler.
        dump: volcado de la ventana.
        rules: reglas de firma. Por defecto, las de ``crawler.yaml``.
        excluded_apps: ejecutables que no se registran. Por defecto, ``apps.excluded``.
        config: configuración ya cargada. Por defecto se carga ``crawler.yaml``.
        screenshot_path: nombre del archivo de captura asociado, si lo hay.

    Raises:
        ExcludedAppRecordError: el volcado es de una app excluida o sin proceso conocido.
    """
    if rules is None or excluded_apps is None:
        config = config if config is not None else load_crawler_config()
    if rules is None:
        rules = SignatureRules.from_config(config.signature)
    if excluded_apps is None:
        excluded_apps = config.apps.excluded

    executable = dump.process_name.strip().lower()
    if not executable:
        raise ExcludedAppRecordError("El volcado no dice de qué proceso es; no se guarda.")
    if executable in {name.strip().lower() for name in excluded_apps}:
        raise ExcludedAppRecordError(f"La app {dump.process_name} está excluida; no se guarda.")

    signature = compute_signature(dump, rules)
    elements = elements_to_store(dump, rules)

    known = store.find_app(executable)
    app = known if known is not None else store.upsert_app(executable, dump.process_name)
    is_new = store.find_state(app.id, signature.structural) is None
    state = store.upsert_state(
        app.id,
        signature.structural,
        signature.detailed,
        signature.stable_title,
        screenshot_path=screenshot_path,
    )
    element_ids = store.upsert_elements(state.id, elements)
    return RecordedState(app_id=app.id, state=state, is_new=is_new, element_ids=element_ids)


def elements_to_store(dump: WindowDump, rules: SignatureRules) -> list[ElementInput]:
    """Elementos del volcado que se guardan, con el rectángulo relativo a la ventana."""
    origin = dump.root.rect
    stable_title = rules.stable_title(dump.title, dump.process_name)
    result: list[ElementInput] = []
    for visit in visit_dump(dump, rules):
        if visit.visibility != "kept":
            continue
        element = visit.element
        result.append(
            ElementInput(
                path=element.path,
                control_type=element.control_type,
                name=stable_title if visit.depth == 0 else element.name,
                automation_id=element.automation_id,
                rect=_relative(element.rect, origin),
                patterns=element.patterns,
                enabled=element.enabled,
            )
        )
    return result


def _relative(rect: Rect | None, origin: Rect | None) -> Rect | None:
    if rect is None or origin is None:
        return None
    return Rect(
        left=rect.left - origin.left,
        top=rect.top - origin.top,
        right=rect.right - origin.left,
        bottom=rect.bottom - origin.top,
    )
