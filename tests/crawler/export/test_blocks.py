"""Tests del bloque administrado (casos 1 a 12 de la especificación C06)."""

import pytest

from geass.crawler.export import ExportError, MalformedNoteError, replace_managed_block

START = "<!-- GEASS:BEGIN -->"
END = "<!-- GEASS:END -->"


def replace(text: str, body: str = "cuerpo") -> str:
    return replace_managed_block(text, body, start=START, end=END)


def test_empty_text() -> None:
    assert replace("") == f"{START}\ncuerpo\n{END}\n"


def test_text_without_markers_keeps_it_and_appends_the_block() -> None:
    text = "# Mi nota\n\nApuntes míos.\n"
    result = replace(text)
    assert result.startswith(text)
    assert result[len(text) :] == f"\n{START}\ncuerpo\n{END}\n"


def test_text_without_final_newline() -> None:
    assert replace("Apuntes") == f"Apuntes\n\n{START}\ncuerpo\n{END}\n"


def test_replaces_an_existing_block() -> None:
    text = f"antes\n{START}\nviejo\n{END}\ndespués\n"
    assert replace(text, "nuevo") == f"antes\n{START}\nnuevo\n{END}\ndespués\n"


@pytest.mark.parametrize(
    "text",
    ["", "# Mi nota\n\nApuntes míos.\n", "Apuntes", f"antes\n{START}\nviejo\n{END}\ndespués\n"],
)
def test_idempotent(text: str) -> None:
    once = replace(text)
    assert replace(once) == once


def test_keeps_crlf_newlines() -> None:
    text = f"antes\r\n{START}\r\nviejo\r\n{END}\r\ndespués\r\n"
    assert replace(text, "a\nb") == f"antes\r\n{START}\r\na\r\nb\r\n{END}\r\ndespués\r\n"


def test_crlf_text_without_markers_uses_crlf() -> None:
    result = replace("# Nota\r\n", "a\nb")
    assert result == f"# Nota\r\n\r\n{START}\r\na\r\nb\r\n{END}\r\n"
    assert replace(result, "a\nb") == result


def test_indented_markers_are_recognized() -> None:
    text = f"  {START}  \nviejo\n\t{END}\nfinal"
    assert replace(text, "nuevo") == f"{START}\nnuevo\n{END}\nfinal"


def test_marker_inside_a_longer_line_does_not_count() -> None:
    text = f"mira {START} aquí\n"
    assert replace(text) == f"{text}\n{START}\ncuerpo\n{END}\n"


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (f"{START}\nalgo\n", "fin"),
        (f"algo\n{END}\n", "inicio"),
        (f"{END}\nalgo\n{START}\n", "antes"),
        (f"{START}\n{START}\nalgo\n{END}\n", "marcadores de inicio"),
        (f"{START}\na\n{END}\n{START}\nb\n{END}\n", "marcadores de inicio"),
    ],
    ids=["solo-inicio", "solo-fin", "fin-antes", "dos-inicios", "dos-bloques"],
)
def test_malformed_markers(text: str, problem: str) -> None:
    with pytest.raises(MalformedNoteError, match=problem):
        replace(text)


def test_malformed_note_error_is_an_export_error() -> None:
    assert issubclass(MalformedNoteError, ExportError)


@pytest.mark.parametrize(
    ("body", "start", "end"),
    [
        (f"a\n{END}\nb", START, END),
        (f"a\n  {START}", START, END),
        ("cuerpo", START, START),
        ("cuerpo", "", END),
        ("cuerpo", START, ""),
    ],
    ids=["cuerpo-con-fin", "cuerpo-con-inicio", "iguales", "inicio-vacio", "fin-vacio"],
)
def test_invalid_arguments(body: str, start: str, end: str) -> None:
    with pytest.raises(ValueError):
        replace_managed_block("texto\n", body, start=start, end=end)


def test_outside_text_is_unchanged() -> None:
    before_lines = ["", "", "# Apuntes de Manuel", ""]
    after_lines = ["", "## Más cosas", ""]
    for number in range(95):
        before_lines.append(f"Línea {number} con tildes: áéíóú ñ ü, y una tabla | a | b |")
        after_lines.append(f"- punto {number} `código` <b>html</b> ¿pregunta?")
    before_lines += ["```python", "x = 1  # comentario", "```", "| c | d |", "|---|---|"]
    after_lines += ["```", "bloque sin lenguaje", "```", "", ""]
    prefix = "\n".join(before_lines) + "\n"
    suffix = "\n" + "\n".join(after_lines)
    text = f"{prefix}{START}\nviejo\ncontenido\n{END}{suffix}"
    assert text.count("\n") >= 200

    result = replace(text, "nuevo\ncuerpo")
    assert result.startswith(prefix)
    assert result.endswith(suffix)
    assert result == f"{prefix}{START}\nnuevo\ncuerpo\n{END}{suffix}"


def test_bom_is_kept() -> None:
    text = "﻿# Nota\n"
    result = replace(text)
    assert result.startswith(text)
    assert replace(result) == result
