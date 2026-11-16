"""Bloque administrado de una nota: el único trozo que GEASS reescribe.

Todo lo que queda fuera de los marcadores se conserva byte a byte. Ante unos marcadores que
no forman un único bloque válido no se adivina nada: se lanza ``MalformedNoteError``.
"""

from geass.core.errors import GeassError


class ExportError(GeassError):
    """Problema al exportar las notas."""


class MalformedNoteError(ExportError):
    """Los marcadores del bloque administrado de una nota no forman un único bloque válido."""


def replace_managed_block(text: str, body: str, *, start: str, end: str) -> str:
    """Devuelve el texto de la nota con el bloque administrado puesto o reemplazado."""
    _check_marker(start, "start")
    _check_marker(end, "end")
    if start == end:
        raise ValueError("Los marcadores de inicio y de fin no pueden ser iguales.")

    body = body.replace("\r\n", "\n")
    for line in body.split("\n"):
        if line.strip() in (start, end):
            raise ValueError("El cuerpo del bloque contiene una línea que es un marcador.")

    newline = "\r\n" if "\r\n" in text else "\n"
    block = newline.join([start, *body.split("\n"), end])

    starts, ends = _find_markers(text, start, end)
    if not starts and not ends:
        if not text:
            return block + newline
        separator = "" if text.endswith("\n") else newline
        return text + separator + newline + block + newline

    if len(starts) == 1 and len(ends) == 1 and starts[0][0] < ends[0][0]:
        return text[: starts[0][0]] + block + text[ends[0][1] :]

    raise MalformedNoteError(_problem(len(starts), len(ends), start, end))


def _check_marker(marker: str, name: str) -> None:
    if not marker.strip():
        raise ValueError(f"El marcador {name} está vacío.")
    if marker != marker.strip() or "\n" in marker or "\r" in marker:
        raise ValueError(
            f"El marcador {name} no puede tener espacios en los extremos ni saltos de línea."
        )


def _find_markers(
    text: str, start: str, end: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Posiciones (primer carácter, fin sin el salto) de las líneas que son marcadores."""
    starts: list[tuple[int, int]] = []
    ends: list[tuple[int, int]] = []
    position = 0
    lines = text.split("\n")
    for index, line in enumerate(lines):
        line_start = position
        line_end = position + len(line)
        position = line_end + 1
        # Un "\r" seguido de "\n" es parte del salto, no de la línea.
        if index < len(lines) - 1 and line.endswith("\r"):
            line_end -= 1
        stripped = line.strip()
        if stripped == start:
            starts.append((line_start, line_end))
        elif stripped == end:
            ends.append((line_start, line_end))
    return starts, ends


def _problem(starts: int, ends: int, start: str, end: str) -> str:
    if starts > 1:
        return f"La nota tiene {starts} marcadores de inicio ({start}); debe tener uno."
    if ends > 1:
        return f"La nota tiene {ends} marcadores de fin ({end}); debe tener uno."
    if starts == 1 and ends == 0:
        return f"La nota tiene el marcador de inicio ({start}) pero no el de fin ({end})."
    if starts == 0 and ends == 1:
        return f"La nota tiene el marcador de fin ({end}) pero no el de inicio ({start})."
    return f"El marcador de fin ({end}) aparece antes que el de inicio ({start})."
