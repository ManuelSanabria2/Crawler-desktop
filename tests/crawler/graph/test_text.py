"""Tests de la normalización de textos (casos 1 y 2 de la especificación C05)."""

from geass.crawler.graph import normalize_text


def test_normalize_lowercases_strips_accents_and_collapses_spaces() -> None:
    assert normalize_text("  Configuración   AVANZADA ") == "configuracion avanzada"


def test_normalize_handles_enye_tabs_and_empty_text() -> None:
    assert normalize_text("Año\tNuevo") == "ano nuevo"
    assert normalize_text("") == ""
