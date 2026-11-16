"""Captura los volcados de UI Automation que los tests usan como fixtures.

Uso, desde la raíz del repo y con el escritorio libre:

    python scripts/capture_uia_fixtures.py

Abre la Calculadora, el Bloc de notas y Configuración, vuelca cada ventana y la cierra.
Solo lee la interfaz: las apps se abren con su ejecutable o su URI y se cierran con el
mensaje WM_CLOSE. Si alguna ya está abierta, se omite para no tocar ventanas de Manuel.

Aviso: el Bloc de notas de Windows 11 recuerda las pestañas entre sesiones. Los dos archivos
temporales que abre este script quedan como pestañas en la sesión del Bloc de notas, y las
pestañas que Manuel ya tuviera aparecen en el volcado; sus nombres se sustituyen antes de guardar.

Los volcados se guardan en tests/fixtures/uia/ y van a git, así que antes de escribirlos se
tacha el nombre de usuario, el nombre del equipo y cualquier correo que aparezca.
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

import win32api
import win32gui

from geass.core.config import load_crawler_config
from geass.core.paths import project_root
from geass.crawler.uia import WindowDump, dump_window

FIXTURES_DIR = project_root() / "tests" / "fixtures" / "uia"
OPEN_TIMEOUT_S = 25.0
SETTLE_TIMEOUT_S = 15.0
POLL_S = 0.4
WM_CLOSE = 0x0010
REDACTED = "REDACTADO"
DWMWA_CLOAKED = 14
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")

NOTEPAD_FILES = {
    "notepad_short": ("nota.txt", "hola\n"),
    "notepad_long": (
        "informe 2026-11-11 v2.txt",
        "".join(f"Linea {i}: texto de prueba numero {i * 37}\n" for i in range(1, 121)),
    ),
}


def is_cloaked(handle: int) -> bool:
    """Las apps de la tienda suspendidas dejan una ventana "visible" pero oculta por el sistema."""
    value = wintypes.DWORD(0)
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(handle), DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value)
    )
    return bool(value.value)


def visible_windows() -> dict[int, str]:
    found: dict[int, str] = {}

    def collect(handle: int, _: object) -> bool:
        shown = win32gui.IsWindowVisible(handle) and not is_cloaked(handle)
        if shown and win32gui.GetWindowText(handle):
            found[handle] = win32gui.GetWindowText(handle)
        return True

    win32gui.EnumWindows(collect, None)
    return found


def open_window(launch: Callable[[], object], title_pattern: str) -> int | None:
    pattern = re.compile(title_pattern, re.IGNORECASE)
    before = visible_windows()
    if any(pattern.search(title) for title in before.values()):
        print(f"  omitido: ya hay una ventana '{title_pattern}' abierta")
        return None
    launch()
    deadline = time.monotonic() + OPEN_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(POLL_S)
        for handle, title in visible_windows().items():
            if handle not in before and pattern.search(title):
                return handle
    raise RuntimeError(f"no apareció la ventana '{title_pattern}'")


def close_window(handle: int) -> None:
    win32gui.PostMessage(handle, WM_CLOSE, 0, 0)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and handle in visible_windows():
        time.sleep(POLL_S)


def dump_settled(handle: int, differs_from: WindowDump | None = None) -> WindowDump:
    """Vuelca hasta que dos lecturas seguidas tienen los mismos elementos."""
    config = load_crawler_config()
    previous: list[tuple[str, str, str]] | None = None
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    while True:
        time.sleep(POLL_S)
        result = dump_window(handle, config=config)
        shape = [(e.path, e.automation_id, e.name) for e in result.root.iter_tree()]
        changed = differs_from is None or shape != [
            (e.path, e.automation_id, e.name) for e in differs_from.root.iter_tree()
        ]
        if (shape == previous and changed) or time.monotonic() > deadline:
            return result
        previous = shape


def sensitive_strings() -> list[str]:
    values = {os.environ.get("USERNAME", ""), os.environ.get("COMPUTERNAME", "")}
    for name_format in (2, 3, 8):
        try:
            values.add(win32api.GetUserNameEx(name_format))
        except win32api.error:
            continue
    parts = {part for value in values for part in re.split(r"[\\\s@]+", value)}
    return sorted((value for value in values | parts if len(value) >= 3), key=len, reverse=True)


def redact(text: str, sensitive: list[str]) -> str:
    text = EMAIL.sub(REDACTED, text)
    for value in sensitive:
        text = re.sub(re.escape(value), REDACTED, text, flags=re.IGNORECASE)
    return text


def rename_foreign_tabs(node: dict, own_names: tuple[str, ...], counter: list[int]) -> None:
    """Sustituye el nombre de las pestañas que no abrió este script."""
    if node["control_type"] == "TabItem" and not any(own in node["name"] for own in own_names):
        counter[0] += 1
        placeholder = f"archivo_ajeno_{counter[0]}.txt"
        node["name"] = f"{placeholder}. No modificado."
        for child in node["children"]:
            if child["control_type"] == "Text":
                child["name"] = placeholder
    for child in node["children"]:
        rename_foreign_tabs(child, own_names, counter)


def save(
    name: str, result: WindowDump, sensitive: list[str], own_tabs: tuple[str, ...] | None = None
) -> None:
    data = json.loads(result.model_dump_json())
    if own_tabs is not None:
        rename_foreign_tabs(data["root"], own_tabs, [0])

    def scrub(node: dict) -> None:
        node["name"] = redact(node["name"], sensitive)
        for child in node["children"]:
            scrub(child)

    data["title"] = redact(data["title"], sensitive)
    scrub(data["root"])
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    status = "PARCIAL" if result.partial else "completo"
    print(f"  {path.name}: {result.node_count} nodos, {result.elapsed_s * 1000:.0f} ms, {status}")


def capture_calculator(sensitive: list[str]) -> None:
    print("Calculadora")
    handle = open_window(lambda: subprocess.Popen(["calc.exe"]), r"calculadora|calculator")
    if handle is None:
        return
    try:
        save("calculator_a", dump_settled(handle), sensitive)
        time.sleep(1.0)
        save("calculator_b", dump_settled(handle), sensitive)
    finally:
        close_window(handle)


def capture_notepad(sensitive: list[str]) -> None:
    print("Bloc de notas")
    with tempfile.TemporaryDirectory() as folder:
        for fixture, (file_name, content) in NOTEPAD_FILES.items():
            path = Path(folder) / file_name
            path.write_text(content, encoding="utf-8")
            handle = open_window(
                lambda path=path: subprocess.Popen(["notepad.exe", str(path)]),
                r"bloc de notas|notepad",
            )
            if handle is None:
                return
            try:
                own_tabs = tuple(name for name, _ in NOTEPAD_FILES.values())
                save(fixture, dump_settled(handle), sensitive, own_tabs)
            finally:
                close_window(handle)
            time.sleep(1.0)


def capture_settings(sensitive: list[str]) -> None:
    print("Configuración")
    handle = open_window(
        lambda: os.startfile("ms-settings:mousetouchpad"), r"^configuraci[oó]n$|^settings$"
    )
    if handle is None:
        return
    try:
        first = dump_settled(handle)
        save("settings_mouse", first, sensitive)
        os.startfile("ms-settings:colors")
        save("settings_colors", dump_settled(handle, differs_from=first), sensitive)
    finally:
        close_window(handle)


CAPTURES = {
    "calculator": capture_calculator,
    "notepad": capture_notepad,
    "settings": capture_settings,
}


def main(argv: list[str]) -> int:
    """Sin argumentos captura todo. Con argumentos, solo las apps nombradas."""
    if sys.platform != "win32":
        print("Este script solo funciona en Windows.")
        return 1
    names = argv or list(CAPTURES)
    unknown = [name for name in names if name not in CAPTURES]
    if unknown:
        print(f"Capturas desconocidas: {unknown}. Opciones: {list(CAPTURES)}")
        return 2
    sensitive = sensitive_strings()
    for name in names:
        CAPTURES[name](sensitive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
