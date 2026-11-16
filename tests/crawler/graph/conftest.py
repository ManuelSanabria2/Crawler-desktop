"""Base sintética de los tests del grafo (especificación C05).

El constructor está en ``tests/crawler/conftest.py`` como fixture ``build_synthetic_db``,
compartido con los tests de la exportación. Los ids reales se devuelven en un mapa con
claves legibles:

- apps: ``editor`` y ``calc``;
- estados: ``E1`` a ``E5`` y ``C1``;
- elementos: ``<estado>.<nombre>``, por ejemplo ``E3.Exportar a PDF``;
- transiciones: ``T1`` a ``T6``.
"""

from collections.abc import Callable, Collection
from pathlib import Path

import pytest

from geass.crawler.graph import CrawlerGraph
from geass.crawler.store import CrawlerStore

Ids = dict[str, int]
BuildDb = Callable[[Path, Collection[str]], Ids]


@pytest.fixture
def build_db(build_synthetic_db: BuildDb) -> BuildDb:
    """Función que crea la base sintética en una ruta, omitiendo las transiciones dadas."""
    return build_synthetic_db


@pytest.fixture
def synthetic_db(tmp_path: Path, build_synthetic_db: BuildDb) -> tuple[Path, Ids]:
    """La base sintética completa y su mapa de ids."""
    path = tmp_path / "graph" / "geass_crawler.sqlite"
    return path, build_synthetic_db(path, ())


@pytest.fixture
def load_graph() -> Callable[[Path], CrawlerGraph]:
    """Función que abre una base, construye el grafo con crawler.yaml y la cierra."""

    def load(path: Path) -> CrawlerGraph:
        with CrawlerStore(path) as store:
            return CrawlerGraph.from_store(store)

    return load


@pytest.fixture
def graph(
    synthetic_db: tuple[Path, Ids], load_graph: Callable[[Path], CrawlerGraph]
) -> CrawlerGraph:
    return load_graph(synthetic_db[0])


@pytest.fixture
def ids(synthetic_db: tuple[Path, Ids]) -> Ids:
    return synthetic_db[1]


@pytest.fixture
def make_store(tmp_path: Path) -> Callable[[str], CrawlerStore]:
    """Abre una base aparte, vacía, para los tests que necesitan otros datos.

    Quien la usa la cierra (por ejemplo con ``with``).
    """

    def make(name: str) -> CrawlerStore:
        return CrawlerStore(tmp_path / name / "geass_crawler.sqlite")

    return make
