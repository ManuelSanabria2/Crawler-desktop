"""Tests de la confirmación (casos 23 a 26 de la especificación C08a)."""

from collections.abc import Callable

import pytest

from geass.crawler.actions import CliConfirmer, ConfirmationRequest, DenyAllConfirmer


def request(**changes: object) -> ConfirmationRequest:
    data: dict[str, object] = {
        "description": 'Pulsar "Eliminar" (MenuItem) en notepad.exe',
        "app": "notepad.exe",
        "element": "Eliminar",
        "control_type": "MenuItem",
        "rule": "blocklist_patterns",
        "pattern": "eliminar",
        "reason": "Coincide con la lista de bloqueo.",
        "is_target": True,
    }
    data.update(changes)
    return ConfirmationRequest.model_validate(data)


def confirmer(answer: Callable[[str], str], lines: list[str] | None = None) -> CliConfirmer:
    out = lines if lines is not None else []
    return CliConfirmer("test:manuel", read=answer, write=out.append)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("s", True), ("Sí", True), (" YES ", True), ("n", False), ("", False), ("quizá", False)],
)
def test_cli_answers(answer: str, expected: bool) -> None:
    prompts: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        return answer

    assert confirmer(read)(request()) is expected
    assert prompts == ["¿Lo hago? [s/N]: "]


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_cli_end_of_input_is_no(error: type[BaseException]) -> None:
    def read(prompt: str) -> str:
        raise error

    assert confirmer(read)(request()) is False


def test_cli_writes_the_details() -> None:
    lines: list[str] = []
    confirmer(lambda _: "n", lines)(request())
    assert lines[0] == "Esta acción necesita tu confirmación:"
    assert '  Acción: Pulsar "Eliminar" (MenuItem) en notepad.exe' in lines
    assert "  Motivo: Coincide con la lista de bloqueo." in lines
    assert "  Regla: blocklist_patterns (patrón 'eliminar')" in lines
    assert not any("paso intermedio" in line for line in lines)

    lines = []
    confirmer(lambda _: "n", lines)(request(is_target=False, pattern=None, rule="unnamed"))
    assert "  Regla: unnamed" in lines
    assert "  Es un paso intermedio de la ruta hasta el objetivo." in lines

    lines = []
    confirmer(lambda _: "n", lines)(request(rule="authorization", pattern="permitir"))
    assert "  Es un botón de autorización. Solo se pulsa si lo confirmas ahora." in lines
    assert CliConfirmer().who == "cli:manuel"


def test_deny_all() -> None:
    deny = DenyAllConfirmer()
    assert deny.who == "nadie"
    first, second = request(), request(element="Borrar")
    assert deny(first) is False
    assert deny(second) is False
    assert deny.requests == [first, second]
    deny.requests.clear()  # es una copia
    assert len(deny.requests) == 2
