"""Reglas de normalización de las firmas, compiladas desde ``crawler.yaml``.

Este módulo no define ninguna regla: solo aplica las de la sección ``signature`` de la
configuración.
"""

import re
from dataclasses import dataclass

from geass.core.schemas.crawler import Signature as SignatureSettings

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class SignatureRules:
    """Reglas listas para aplicar."""

    ignored_control_types: frozenset[str]
    volatile_container_types: frozenset[str]
    prune_empty_control_types: frozenset[str]
    count_buckets: tuple[int, ...]
    variable_text_patterns: tuple[tuple[re.Pattern[str], str], ...]
    title_patterns: dict[str, re.Pattern[str]]
    default_title_pattern: re.Pattern[str]

    @classmethod
    def from_config(cls, settings: SignatureSettings) -> "SignatureRules":
        return cls(
            ignored_control_types=frozenset(settings.ignored_control_types),
            volatile_container_types=frozenset(settings.volatile_container_types),
            prune_empty_control_types=frozenset(settings.prune_empty_control_types),
            count_buckets=tuple(settings.count_buckets),
            variable_text_patterns=tuple(
                (re.compile(item.pattern), item.replacement)
                for item in settings.variable_text_patterns
            ),
            title_patterns={
                executable: re.compile(pattern)
                for executable, pattern in settings.title_patterns.items()
            },
            default_title_pattern=re.compile(settings.default_title_pattern),
        )

    def normalize_name(self, name: str) -> str:
        """Nombre en minúsculas, sin espacios sobrantes y con el texto variable sustituido."""
        text = _WHITESPACE.sub(" ", name).strip().lower()
        for pattern, replacement in self.variable_text_patterns:
            text = pattern.sub(replacement, text)
        return text

    def stable_title(self, title: str, process_name: str) -> str:
        """Parte estable del título: el nombre de la app sin el archivo abierto."""
        pattern = self.title_patterns.get(process_name.lower(), self.default_title_pattern)
        match = pattern.search(title)
        stable = match.group("stable") if match else None
        return self.normalize_name(stable or "")

    def count_bucket(self, count: int) -> str:
        """Rango de cantidad al que pertenece ``count``, por ejemplo ``5-19`` o ``100+``."""
        lower = self.count_buckets[0]
        upper: int | None = None
        for index, bound in enumerate(self.count_buckets):
            if count >= bound:
                lower = bound
                following = self.count_buckets[index + 1 :]
                upper = following[0] if following else None
        if upper is None:
            return f"{lower}+"
        return str(lower) if upper - lower == 1 else f"{lower}-{upper - 1}"
