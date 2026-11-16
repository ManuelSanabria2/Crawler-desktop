"""El crawler pasivo solo lee (caso 54 de la especificación C04).

Se revisa el código de ``src/geass/crawler/passive/`` en busca de APIs que generan entrada o
cambian el foco. Se mira el código (nombres, atributos, importaciones y cadenas), no los
comentarios ni los docstrings: un docstring puede nombrar una API justo para decir que no
se usa. Los archivos nuevos de C04 tampoco las nombran en el texto.
"""

import ast
from pathlib import Path

import pytest

PASSIVE_DIR = Path(__file__).resolve().parents[3] / "src" / "geass" / "crawler" / "passive"
FORBIDDEN = (
    "Controller",
    "SendInput",
    "send_keys",
    "type_keys",
    "click_input",
    "mouse_event",
    "keybd_event",
    "SetCursorPos",
    "set_focus",
    "SetForegroundWindow",
)
NEW_IN_C04 = ("privacy.py", "hotkey.py", "cpu.py", "runner.py", "__init__.py")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def code_words(source: str) -> list[str]:
    """Nombres, atributos, importaciones y cadenas del código, sin docstrings."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    words: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            words.append(node.id)
        elif isinstance(node, ast.Attribute):
            words.append(node.attr)
        elif isinstance(node, ast.alias):
            words.extend([node.name, node.asname or ""])
        elif isinstance(node, ast.ImportFrom):
            words.append(node.module or "")
        elif isinstance(node, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
            words.append(node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            words.append(node.value)
    return words


def passive_files() -> list[Path]:
    files = sorted(PASSIVE_DIR.glob("*.py"))
    assert files, f"No se encontraron archivos en {PASSIVE_DIR}"
    return files


@pytest.mark.parametrize("path", passive_files(), ids=lambda path: path.name)
def test_code_uses_no_input_or_focus_api(path: Path) -> None:
    words = code_words(path.read_text(encoding="utf-8"))
    used = [name for name in FORBIDDEN if any(name in word for word in words)]
    assert not used, f"{path.name} usa APIs de entrada o de foco: {used}"


@pytest.mark.parametrize("name", NEW_IN_C04)
def test_new_files_do_not_even_mention_them(name: str) -> None:
    text = (PASSIVE_DIR / name).read_text(encoding="utf-8")
    assert not [word for word in FORBIDDEN if word in text]


def test_the_check_finds_a_forbidden_call() -> None:
    sample = "from pynput.mouse import Controller\nController().click()\n"
    assert "Controller" in code_words(sample)
    assert "Controller" not in code_words('"""No usa Controller."""\nx = 1\n')
