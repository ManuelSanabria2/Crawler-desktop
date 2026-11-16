"""Lanzador de apps por nombre aproximado.

``match_app`` solo decide qué app se pidió; ``launch`` la abre si la decisión es clara.
Ante la duda no se hace nada: si hay ambigüedad o ninguna coincidencia, se devuelven
opciones y no se abre nada. Nunca se lanza una ruta ni un comando que dé el usuario: solo
entradas del índice.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions import system
from geass.crawler.actions.audit import AuditLog
from geass.crawler.actions.errors import ActionError
from geass.crawler.actions.index import AppEntry, AppIndex
from geass.crawler.graph.text import normalize_text

LaunchStatus = Literal["launched", "would_launch", "ambiguous", "not_found", "blocked"]

_EXACT_SCORE = 100.0


class AppMatch(BaseModel):
    """Una app candidata y su puntuación."""

    model_config = ConfigDict(frozen=True)

    entry: AppEntry
    score: float  # 0 a 100


class LaunchResult(BaseModel):
    """Qué se decidió (y, con ``launched``, qué se hizo) para un nombre pedido."""

    model_config = ConfigDict(frozen=True)

    query: str
    status: LaunchStatus
    entry: AppEntry | None  # la app elegida; None si es ambiguo o no hay coincidencia
    options: list[AppMatch]  # opciones a mostrar; vacía si se eligió una


def match_app(index: AppIndex, name: str, config: CrawlerConfig) -> LaunchResult:
    """Decide qué app del índice corresponde a ``name``. No lanza nada.

    Raises:
        ValueError: ``name`` queda vacío al normalizarlo.
    """
    query = normalize_text(name)
    if not query:
        raise ValueError("Falta el nombre de la app que hay que abrir.")
    launcher = config.launcher

    scored: list[tuple[float, str, AppEntry]] = []
    for entry in index.entries:
        normalized = normalize_text(entry.name)
        score = _EXACT_SCORE if normalized == query else float(fuzz.WRatio(query, normalized))
        scored.append((score, normalized, entry))
    ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
    candidates = [item for item in ranked if item[0] >= launcher.fuzzy_match_threshold]

    if not candidates:
        return LaunchResult(
            query=name,
            status="not_found",
            entry=None,
            options=_options(ranked, launcher.max_options),
        )

    exact = [item for item in candidates if item[1] == query]
    if len(exact) == 1:
        chosen = exact[0][2]
    elif len(exact) > 1 or (
        len(candidates) >= 2 and candidates[0][0] - candidates[1][0] < launcher.ambiguity_margin
    ):
        # Dos coincidencias exactas o dos candidatas casi empatadas: no se adivina.
        return LaunchResult(
            query=name,
            status="ambiguous",
            entry=None,
            options=_options(candidates, launcher.max_options),
        )
    else:
        chosen = candidates[0][2]

    status: LaunchStatus = "blocked" if chosen.blocked else "would_launch"
    return LaunchResult(query=name, status=status, entry=chosen, options=[])


def launch(
    name: str,
    *,
    authorized_by: str,
    index: AppIndex | None = None,
    config: CrawlerConfig | None = None,
    safety: SafetyConfig | None = None,
    audit: AuditLog | None = None,
    dry_run: bool = False,
) -> LaunchResult:
    """Abre la app que corresponde a ``name``, solo si la coincidencia es clara.

    Sin ``dry_run`` cada llamada deja una línea en la auditoría.

    Raises:
        ValueError: ``authorized_by`` o ``name`` están vacíos.
        ActionError: Windows no pudo abrir la app. Se registra antes de dejarlo pasar.
    """
    if not authorized_by.strip():
        raise ValueError("Toda acción necesita quién la autorizó: authorized_by está vacío.")
    if config is None or safety is None:
        from geass.core.config import load_crawler_config, load_safety_config

        config = config if config is not None else load_crawler_config()
        safety = safety if safety is not None else load_safety_config()
    if index is None:
        index = AppIndex.load(config, safety)

    result = match_app(index, name, config)
    if dry_run:
        return result

    if audit is None:
        audit = AuditLog.from_config(safety)
    entry = result.entry
    app = entry.name if entry is not None else ""
    if result.status != "would_launch" or entry is None:
        audit.record(
            action="launch", target=name, app=app, result=result.status, authorized_by=authorized_by
        )
        return result

    try:
        if entry.source == "shortcut":
            system.start_shortcut(Path(str(entry.shortcut_path)))
        else:
            system.start_app_id(str(entry.app_id))
    except ActionError as error:
        audit.record(
            action="launch",
            target=name,
            app=app,
            result=f"error: {error}",
            authorized_by=authorized_by,
        )
        raise
    launched = result.model_copy(update={"status": "launched"})
    audit.record(
        action="launch", target=name, app=app, result="launched", authorized_by=authorized_by
    )
    return launched


def _options(ranked: list[tuple[float, str, AppEntry]], limit: int) -> list[AppMatch]:
    return [AppMatch(entry=entry, score=score) for score, _, entry in ranked[:limit]]
