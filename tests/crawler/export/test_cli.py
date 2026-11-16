"""Tests del comando export (casos 34 a 38 de la especificación C06).

Siempre con --db y --vault en tmp_path: nunca se toca la base real ni la bóveda de Manuel.
"""

from pathlib import Path

from typer.testing import CliRunner

from geass.crawler.__main__ import app

START = "<!-- GEASS:BEGIN -->"


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args))
    return result.exit_code, result.output


def test_export_creates_the_notes(db_path: Path, vault: Path, notes_dir: Path) -> None:
    code, output = run("export", "--db", str(db_path), "--vault", str(vault))
    assert code == 0, output
    assert output.count("creada:") == 2
    assert (notes_dir / "editor.md").is_file()
    assert (notes_dir / "calc.md").is_file()


def test_export_twice(db_path: Path, vault: Path) -> None:
    run("export", "--db", str(db_path), "--vault", str(vault))
    code, output = run("export", "--db", str(db_path), "--vault", str(vault))
    assert code == 0, output
    assert output.count("sin cambios:") == 2


def test_dry_run(db_path: Path, vault: Path) -> None:
    code, output = run("export", "--dry-run", "--db", str(db_path), "--vault", str(vault))
    assert code == 0, output
    assert output.splitlines()[0] == "Simulación: no se escribe nada."
    assert "se crearía:" in output
    assert list(vault.iterdir()) == []


def test_single_app(db_path: Path, vault: Path, notes_dir: Path) -> None:
    code, output = run("export", "--app", "editor", "--db", str(db_path), "--vault", str(vault))
    assert code == 0, output
    assert sorted(path.name for path in notes_dir.iterdir()) == ["editor.md"]
    code, _ = run("export", "--app", "zzzz", "--db", str(db_path), "--vault", str(vault))
    assert code == 1


def test_malformed_note_exits_with_1(db_path: Path, vault: Path, notes_dir: Path) -> None:
    notes_dir.mkdir(parents=True)
    editor = notes_dir / "editor.md"
    original = f"# Editor\n\n{START}\n".encode()
    editor.write_bytes(original)
    code, output = run("export", "--db", str(db_path), "--vault", str(vault))
    assert code == 1
    assert "error" in output
    assert editor.read_bytes() == original
    assert (notes_dir / "calc.md").is_file()


def test_missing_database(tmp_path: Path, vault: Path) -> None:
    missing = tmp_path / "no_existe" / "geass_crawler.sqlite"
    code, output = run("export", "--db", str(missing), "--vault", str(vault))
    assert code == 1
    assert "La base todavía no existe" in output
    assert not missing.parent.exists()
    assert list(vault.iterdir()) == []


def test_missing_vault(db_path: Path, tmp_path: Path) -> None:
    missing = tmp_path / "sin_boveda"
    code, output = run("export", "--db", str(db_path), "--vault", str(missing))
    assert code == 1
    assert "bóveda" in output
    assert not missing.exists()


def test_database_without_apps(tmp_path: Path, vault: Path) -> None:
    from geass.crawler.store import CrawlerStore

    path = tmp_path / "vacia" / "geass_crawler.sqlite"
    CrawlerStore(path).close()
    code, output = run("export", "--db", str(path), "--vault", str(vault))
    assert code == 0, output
    assert "La base no tiene ninguna app registrada." in output
    assert list(vault.iterdir()) == []
