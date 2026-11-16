"""Tests de los comandos query y coverage (casos 30 a 36 de la especificación C05).

Siempre con --db apuntando a una base en tmp_path: nunca se toca la base real.
"""

from pathlib import Path

from typer.testing import CliRunner

from geass.crawler.__main__ import app

Ids = dict[str, int]


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args))
    return result.exit_code, result.output


def test_query_lists_the_matches(synthetic_db: tuple[Path, Ids]) -> None:
    path, ids = synthetic_db
    code, output = run("query", "exportar pdf", "--app", "editor", "--db", str(path))
    assert code == 0, output
    assert '"Exportar a PDF" (MenuItem)' in output
    assert f"estado {ids['E3']}: Menú Exportar" in output
    assert "calc.exe" not in output


def test_query_with_from_describes_the_route(synthetic_db: tuple[Path, Ids]) -> None:
    path, ids = synthetic_db
    code, output = run(
        "query", "exportar pdf", "--app", "editor", "--from", str(ids["E1"]), "--db", str(path)
    )
    assert code == 0, output
    assert f"Ruta desde el estado {ids['E1']}:" in output
    for line in (
        '1. Desplegar "Archivo" (MenuItem)',
        '2. Desplegar "Exportar" (MenuItem)',
        '3. Pulsar "Exportar a PDF" (MenuItem)',
    ):
        assert line in output


def test_query_without_matches(synthetic_db: tuple[Path, Ids]) -> None:
    code, output = run("query", "xyzzy", "--app", "editor", "--db", str(synthetic_db[0]))
    assert code == 1
    assert "Sin coincidencias" in output
    assert "editor.exe" in output


def test_query_with_an_unknown_app(synthetic_db: tuple[Path, Ids]) -> None:
    code, _ = run("query", "guardar", "--app", "zzzz", "--db", str(synthetic_db[0]))
    assert code == 1


def test_query_requires_the_app(synthetic_db: tuple[Path, Ids]) -> None:
    code, _ = run("query", "guardar", "--db", str(synthetic_db[0]))
    assert code != 0


def test_coverage_lists_every_app(synthetic_db: tuple[Path, Ids]) -> None:
    path = synthetic_db[0]
    code, output = run("coverage", "--db", str(path))
    assert code == 0, output
    assert "Editor de Prueba (editor.exe)" in output
    assert "Calculadora (calc.exe)" in output
    assert "  Cobertura de elementos: 56%" in output
    code, output = run("coverage", "--app", "editor", "--db", str(path))
    assert code == 0, output
    assert "calc.exe" not in output


def test_coverage_with_a_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "no_existe" / "geass_crawler.sqlite"
    code, output = run("coverage", "--db", str(missing))
    assert code == 1
    assert "La base todavía no existe" in output
    assert not missing.exists()
    assert not missing.parent.exists()


def test_coverage_with_no_apps(tmp_path: Path) -> None:
    from geass.crawler.store import CrawlerStore

    path = tmp_path / "vacia" / "geass_crawler.sqlite"
    CrawlerStore(path).close()
    code, output = run("coverage", "--db", str(path))
    assert code == 0, output
    assert "La base no tiene ninguna app registrada." in output
