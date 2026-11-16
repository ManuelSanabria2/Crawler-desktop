"""Comparación de dos volcados: qué diferencias afectan a cada firma y cuáles se ignoran."""

from collections import Counter
from dataclasses import dataclass, field

from geass.crawler.signature.compute import (
    StateSignature,
    Token,
    Visit,
    compute_signature,
    default_rules,
    tokens_of,
    visit_dump,
)
from geass.crawler.signature.rules import SignatureRules
from geass.crawler.uia.schema import WindowDump

SHORT_HASH = 12
NAME_PREVIEW = 48

_IGNORED_LABELS = {
    "ignored_type": "tipo de control ignorado (ignored_control_types)",
    "volatile_content": "contenido de un contenedor volátil (volatile_container_types)",
    "pruned_empty": "contenedor vacío descartado (prune_empty_control_types)",
    "variable_text": "texto variable normalizado (variable_text_patterns o título)",
}


@dataclass(frozen=True)
class Comparison:
    """Resultado de comparar dos volcados A y B."""

    signature_a: StateSignature
    signature_b: StateSignature
    header_differences: list[str]
    structural_only_a: list[tuple[str, int]]
    structural_only_b: list[tuple[str, int]]
    detailed_only_a: list[tuple[str, int]]
    detailed_only_b: list[tuple[str, int]]
    ignored_differences: dict[str, int] = field(default_factory=dict)

    @property
    def structural_equal(self) -> bool:
        return self.signature_a.structural == self.signature_b.structural

    @property
    def detailed_equal(self) -> bool:
        return self.signature_a.detailed == self.signature_b.detailed


def compare_dumps(a: WindowDump, b: WindowDump, rules: SignatureRules | None = None) -> Comparison:
    """Compara dos volcados y clasifica sus diferencias por la firma a la que afectan."""
    rules = rules if rules is not None else default_rules()
    visits_a, visits_b = visit_dump(a, rules), visit_dump(b, rules)
    tokens_a, tokens_b = tokens_of(visits_a), tokens_of(visits_b)

    header: list[str] = []
    if a.process_name.lower() != b.process_name.lower():
        header.append(f"proceso: {a.process_name} frente a {b.process_name}")
    if a.class_name != b.class_name:
        header.append(f"clase de ventana: {a.class_name} frente a {b.class_name}")

    structural_a = Counter(_describe_structural(t) for t in tokens_a)
    structural_b = Counter(_describe_structural(t) for t in tokens_b)
    # Diferencias que solo ve la firma detallada: se quitan las que ya son estructurales.
    detailed_a = Counter(_describe_detailed(t) for t in tokens_a)
    detailed_b = Counter(_describe_detailed(t) for t in tokens_b)

    return Comparison(
        signature_a=compute_signature(a, rules),
        signature_b=compute_signature(b, rules),
        header_differences=header,
        structural_only_a=_only(structural_a, structural_b),
        structural_only_b=_only(structural_b, structural_a),
        detailed_only_a=_only(detailed_a, detailed_b),
        detailed_only_b=_only(detailed_b, detailed_a),
        ignored_differences=_ignored(visits_a, visits_b),
    )


def explain(comparison: Comparison, max_lines: int = 15) -> list[str]:
    """Explicación legible de la comparación, línea a línea."""
    a, b = comparison.signature_a, comparison.signature_b
    lines = [
        f"A: {a.process_name} | clase {a.window_class} | título '{a.stable_title}' | "
        f"{a.token_count} elementos en la firma" + (" | volcado PARCIAL" if a.partial else ""),
        f"B: {b.process_name} | clase {b.window_class} | título '{b.stable_title}' | "
        f"{b.token_count} elementos en la firma" + (" | volcado PARCIAL" if b.partial else ""),
        "",
    ]

    if comparison.structural_equal:
        lines.append(f"Firma estructural: IGUAL ({a.structural[:SHORT_HASH]}). Es la misma vista.")
    else:
        lines.append(
            f"Firma estructural: DISTINTA ({a.structural[:SHORT_HASH]} frente a "
            f"{b.structural[:SHORT_HASH]}). Son vistas distintas."
        )
        lines.extend(f"  {item}" for item in comparison.header_differences)
        lines.extend(_listing("solo en A", comparison.structural_only_a, max_lines))
        lines.extend(_listing("solo en B", comparison.structural_only_b, max_lines))
    lines.append("")

    if comparison.detailed_equal:
        lines.append(f"Firma detallada: IGUAL ({a.detailed[:SHORT_HASH]}). Es la misma variante.")
    else:
        lines.append(
            f"Firma detallada: DISTINTA ({a.detailed[:SHORT_HASH]} frente a "
            f"{b.detailed[:SHORT_HASH]})."
        )
        if comparison.structural_equal:
            lines.append(
                "  Misma vista, distinta variante. Cambian nombres o elementos habilitados:"
            )
            lines.extend(_listing("solo en A", comparison.detailed_only_a, max_lines))
            lines.extend(_listing("solo en B", comparison.detailed_only_b, max_lines))
        else:
            lines.append("  Es consecuencia de que la vista es distinta; no se listan variantes.")
    lines.append("")

    total = sum(comparison.ignored_differences.values())
    if total:
        lines.append(f"Diferencias que ninguna firma tiene en cuenta: {total}")
        for reason, count in sorted(comparison.ignored_differences.items()):
            lines.append(f"  {count} por {_IGNORED_LABELS[reason]}")
    else:
        lines.append("No hay diferencias ignoradas por la normalización.")
    return lines


def _describe_structural(token: Token) -> str:
    identity = (
        f"id={token.automation_id}"
        if token.automation_id
        else f"nombre='{token.name[:NAME_PREVIEW]}'"
    )
    return f"nivel {token.depth}: {token.control_type} {identity}{_bucket(token)}"


def _describe_detailed(token: Token) -> str:
    state = "habilitado" if token.enabled else "deshabilitado"
    return (
        f"nivel {token.depth}: {token.control_type} id={token.automation_id or '(sin id)'} "
        f"nombre='{token.name[:NAME_PREVIEW]}' {state}{_bucket(token)}"
    )


def _bucket(token: Token) -> str:
    return f" con {token.bucket} hijos" if token.bucket else ""


def _only(left: Counter, right: Counter) -> list[tuple[str, int]]:
    return sorted((left - right).items())


def _listing(title: str, items: list[tuple[str, int]], max_lines: int) -> list[str]:
    if not items:
        return []
    lines = [f"  {title} ({sum(count for _, count in items)}):"]
    for description, count in items[:max_lines]:
        lines.append(f"    {description}" + (f" x{count}" if count > 1 else ""))
    if len(items) > max_lines:
        lines.append(f"    ... y {len(items) - max_lines} más")
    return lines


def _ignored(visits_a: list[Visit], visits_b: list[Visit]) -> dict[str, int]:
    """Cuenta las diferencias en bruto que las reglas dejan fuera de ambas firmas."""
    result: dict[str, int] = {}
    for reason in ("ignored_type", "volatile_content", "pruned_empty"):
        raw_a = Counter(_raw(v) for v in visits_a if v.visibility == reason)
        raw_b = Counter(_raw(v) for v in visits_b if v.visibility == reason)
        count = sum(((raw_a - raw_b) + (raw_b - raw_a)).values())
        if count:
            result[reason] = count

    kept_a = [v for v in visits_a if v.token is not None]
    kept_b = [v for v in visits_b if v.token is not None]
    raw_difference = Counter(_raw(v) for v in kept_a)
    raw_difference.subtract(Counter(_raw(v) for v in kept_b))
    seen_difference = Counter(v.token.detailed_key for v in kept_a if v.token)
    seen_difference.subtract(Counter(v.token.detailed_key for v in kept_b if v.token))
    raw_count = sum(abs(count) for count in raw_difference.values())
    seen_count = sum(abs(count) for count in seen_difference.values())
    if raw_count > seen_count:
        result["variable_text"] = raw_count - seen_count
    return result


def _raw(visit: Visit) -> tuple[int, str, str, str, bool]:
    element = visit.element
    return (
        visit.depth,
        element.control_type,
        element.automation_id,
        element.name,
        element.enabled,
    )
