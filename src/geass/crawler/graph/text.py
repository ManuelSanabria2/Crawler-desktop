"""Normalización de textos para comparar nombres de apps y de elementos."""

import unicodedata


def normalize_text(text: str) -> str:
    """Texto listo para comparar: minúsculas, sin tildes y con espacios simples."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.split())
