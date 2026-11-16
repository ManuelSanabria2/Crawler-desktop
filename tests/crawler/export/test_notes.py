"""Tests de la escritura de notas (casos 20 a 33 de la especificación C06).

La bóveda es siempre una carpeta de ``tmp_path``, pasada con ``vault=``.
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from geass.core.errors import ConfigIncompleteError
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.export import ExportError, NoteResult, export_notes, note_path
from geass.crawler.graph import CrawlerGraph
from geass.crawler.store import AppRecord

START = "<!-- GEASS:BEGIN -->"
END = "<!-- GEASS:END -->"

LoadGraph = Callable[[Path], CrawlerGraph]


def by_app(results: list[NoteResult]) -> dict[str, NoteResult]:
    return {result.app.executable: result for result in results}


def outside_block(text: str) -> tuple[str, str]:
    """Texto anterior al marcador de inicio y posterior al de fin."""
    return text[: text.index(START)], text[text.index(END) + len(END) :]


def assert_only_notes(folder: Path) -> None:
    """En la carpeta de notas solo hay notas .md de apps: ningún temporal."""
    names = sorted(path.name for path in folder.iterdir())
    assert all(name.endswith(".md") and not name.startswith(".") for name in names), names


def test_first_export_creates_the_notes(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    results = by_app(export_notes(export_graph, config, vault=vault))
    assert set(results) == {"editor.exe", "calc.exe"}
    for result in results.values():
        assert result.status == "created"
        assert result.written is True
        assert result.detail == ""
    editor = notes_dir / "editor.md"
    assert results["editor.exe"].path == editor
    assert (notes_dir / "calc.md").is_file()
    text = editor.read_bytes().decode("utf-8")
    assert text.startswith(f"# Editor de Prueba\n\n{START}\n")
    assert text.endswith(f"{END}\n")
    assert_only_notes(notes_dir)


def test_second_export_without_changes(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    export_notes(export_graph, config, vault=vault)
    paths = [notes_dir / "editor.md", notes_dir / "calc.md"]
    past = 1_600_000_000
    for path in paths:
        os.utime(path, (past, past))
    before = {path: path.read_bytes() for path in paths}

    results = export_notes(export_graph, config, vault=vault)
    for result in results:
        assert result.status == "unchanged"
        assert result.written is False
    for path in paths:
        assert path.read_bytes() == before[path]
        assert path.stat().st_mtime == past
    assert_only_notes(notes_dir)


def test_hand_written_text_survives_two_exports(
    db_path: Path,
    load_graph: LoadGraph,
    config: CrawlerConfig,
    vault: Path,
    notes_dir: Path,
    add_transition: Callable[[], None],
) -> None:
    export_notes(load_graph(db_path), config, vault=vault)
    editor = notes_dir / "editor.md"
    original = editor.read_bytes().decode("utf-8")
    title = "# Editor de Prueba\n\n"
    assert original.startswith(title + START)
    prefix = title + "Mis apuntes de arriba.\n\n"
    suffix = "\n\n## Mis trucos\n\n- Ctrl+E exporta.\n"
    block = original[len(title) : original.index(END) + len(END)]
    editor.write_bytes((prefix + block + suffix).encode("utf-8"))

    add_transition()
    statuses = []
    for _ in range(2):
        result = by_app(export_notes(load_graph(db_path), config, vault=vault))["editor.exe"]
        statuses.append(result.status)
        text = editor.read_bytes().decode("utf-8")
        assert outside_block(text) == (prefix, suffix)
        assert '"Imprimir" (MenuItem), de "Menú Archivo" a "Principal": 1 vez' in text
    assert statuses == ["updated", "unchanged"]
    assert_only_notes(notes_dir)


def test_existing_note_without_markers(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    notes_dir.mkdir(parents=True)
    previous = "# Editor\n\nNotas previas de Manuel.\n"
    editor = notes_dir / "editor.md"
    editor.write_bytes(previous.encode("utf-8"))

    result = by_app(export_notes(export_graph, config, vault=vault))["editor.exe"]
    assert result.status == "updated"
    assert result.written is True
    text = editor.read_bytes().decode("utf-8")
    assert text.startswith(previous + "\n" + START + "\n")
    assert text.endswith(f"{END}\n")


def test_malformed_note_is_not_touched(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    notes_dir.mkdir(parents=True)
    editor = notes_dir / "editor.md"
    original = f"# Editor\n\n{START}\nsin cierre\n".encode()
    editor.write_bytes(original)

    results = by_app(export_notes(export_graph, config, vault=vault))
    assert results["editor.exe"].status == "error"
    assert results["editor.exe"].written is False
    assert results["editor.exe"].detail
    assert editor.read_bytes() == original
    assert results["calc.exe"].status == "created"
    assert (notes_dir / "calc.md").is_file()
    assert_only_notes(notes_dir)


def test_note_that_is_not_utf8(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    notes_dir.mkdir(parents=True)
    editor = notes_dir / "editor.md"
    original = b"\xff\xfe\x00mal"
    editor.write_bytes(original)

    results = by_app(export_notes(export_graph, config, vault=vault))
    assert results["editor.exe"].status == "error"
    assert results["editor.exe"].detail
    assert editor.read_bytes() == original
    assert results["calc.exe"].status == "created"


def test_note_with_crlf_newlines(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    notes_dir.mkdir(parents=True)
    editor = notes_dir / "editor.md"
    editor.write_bytes(b"# Editor\r\n\r\nApuntes.\r\n")

    result = by_app(export_notes(export_graph, config, vault=vault))["editor.exe"]
    assert result.status == "updated"
    data = editor.read_bytes()
    assert data.startswith(b"# Editor\r\n\r\nApuntes.\r\n\r\n")
    assert data.count(b"\n") == data.count(b"\r\n")
    assert by_app(export_notes(export_graph, config, vault=vault))["editor.exe"].status == (
        "unchanged"
    )


def test_dry_run_on_an_empty_vault(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path
) -> None:
    results = export_notes(export_graph, config, vault=vault, dry_run=True)
    assert len(results) == 2
    for result in results:
        assert result.status == "created"
        assert result.written is False
    assert not (vault / "GEASS").exists()
    assert list(vault.iterdir()) == []


def test_dry_run_on_exported_notes(
    db_path: Path,
    load_graph: LoadGraph,
    config: CrawlerConfig,
    vault: Path,
    notes_dir: Path,
    add_transition: Callable[[], None],
) -> None:
    export_notes(load_graph(db_path), config, vault=vault)
    paths = sorted(notes_dir.iterdir())
    before = {path: path.read_bytes() for path in paths}
    add_transition()

    results = by_app(export_notes(load_graph(db_path), config, vault=vault, dry_run=True))
    assert results["editor.exe"].status == "updated"
    assert results["editor.exe"].written is False
    assert results["calc.exe"].status == "unchanged"
    assert sorted(notes_dir.iterdir()) == paths
    for path in paths:
        assert path.read_bytes() == before[path]


def test_single_app(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    results = export_notes(export_graph, config, app="editor", vault=vault)
    assert len(results) == 1
    assert results[0].app.executable == "editor.exe"
    assert sorted(path.name for path in notes_dir.iterdir()) == ["editor.md"]


def test_excluded_app_is_skipped(
    export_graph: CrawlerGraph, config: CrawlerConfig, vault: Path, notes_dir: Path
) -> None:
    apps = config.apps.model_copy(update={"excluded": ["keepass.exe", "CALC.EXE"]})
    excluded = config.model_copy(update={"apps": apps})
    results = by_app(export_notes(export_graph, excluded, vault=vault))
    assert results["calc.exe"].status == "skipped_excluded"
    assert results["calc.exe"].path is None
    assert results["calc.exe"].written is False
    assert results["editor.exe"].status == "created"
    assert not (notes_dir / "calc.md").exists()


def test_missing_vault(export_graph: CrawlerGraph, config: CrawlerConfig, tmp_path: Path) -> None:
    missing = tmp_path / "no_existe"
    with pytest.raises(ExportError):
        export_notes(export_graph, config, vault=missing)
    assert not missing.exists()


@pytest.mark.parametrize("folder", ["../fuera", "GEASS/../../fuera", "absoluta"])
def test_notes_folder_outside_the_vault(
    export_graph: CrawlerGraph,
    config: CrawlerConfig,
    vault: Path,
    tmp_path: Path,
    folder: str,
) -> None:
    if folder == "absoluta":
        folder = str(tmp_path / "fuera")
    settings = config.export.model_copy(update={"notes_folder": folder})
    outside = config.model_copy(update={"export": settings})
    before = sorted(tmp_path.rglob("*"))
    with pytest.raises(ExportError):
        export_notes(export_graph, outside, vault=vault)
    with pytest.raises(ExportError):
        export_notes(export_graph, outside, vault=vault, dry_run=True)
    assert sorted(tmp_path.rglob("*")) == before
    assert not (tmp_path / "fuera").exists()


def test_vault_not_configured(export_graph: CrawlerGraph, config: CrawlerConfig) -> None:
    if not config.export.is_pending("obsidian_vault_path"):
        pytest.skip("La bóveda ya está configurada: no se usa la bóveda real en los tests.")
    with pytest.raises(ConfigIncompleteError) as raised:
        export_notes(export_graph, config, vault=None)
    assert raised.value.key == "export.obsidian_vault_path"


def test_note_path(vault: Path) -> None:
    def app(executable: str, app_id: int = 1) -> AppRecord:
        return AppRecord(id=app_id, executable=executable, display_name="x", app_user_model_id=None)

    folder = vault / "GEASS" / "Apps"
    assert note_path(vault, "GEASS/Apps", app("editor.exe")) == folder / "editor.md"
    assert note_path(vault, "GEASS/Apps", app("mi app (x86).exe")) == folder / "mi_app__x86_.md"
    assert note_path(vault, "GEASS/Apps", app(".exe", 7)) == folder / "app_7.md"
    with pytest.raises(ExportError):
        note_path(vault, "../fuera", app("editor.exe"))
