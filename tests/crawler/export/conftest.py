"""Fixtures de los tests de la exportación (especificación C06).

La bóveda es siempre una carpeta de ``tmp_path``: ningún test lee la clave real
``export.obsidian_vault_path`` ni escribe fuera de ``tmp_path``.
"""

from collections.abc import Callable, Collection
from pathlib import Path

import pytest

from geass.core.config import load_crawler_config
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.graph import CrawlerGraph
from geass.crawler.store import CrawlerStore

Ids = dict[str, int]
BuildDb = Callable[[Path, Collection[str]], Ids]


@pytest.fixture
def export_db(tmp_path: Path, build_synthetic_db: BuildDb) -> tuple[Path, Ids]:
    """La base sintética de C05, completa, y su mapa de ids."""
    path = tmp_path / "db" / "geass_crawler.sqlite"
    return path, build_synthetic_db(path, ())


@pytest.fixture
def db_path(export_db: tuple[Path, Ids]) -> Path:
    return export_db[0]


@pytest.fixture
def db_ids(export_db: tuple[Path, Ids]) -> Ids:
    return export_db[1]


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Bóveda temporal vacía."""
    path = tmp_path / "boveda"
    path.mkdir()
    return path


@pytest.fixture
def notes_dir(vault: Path) -> Path:
    """Carpeta de notas de crawler.yaml dentro de la bóveda temporal. No se crea."""
    return vault / "GEASS" / "Apps"


@pytest.fixture
def config() -> CrawlerConfig:
    """La configuración real de crawler.yaml."""
    return load_crawler_config()


@pytest.fixture
def load_graph(config: CrawlerConfig) -> Callable[[Path], CrawlerGraph]:
    """Función que abre una base, construye el grafo y la cierra."""

    def load(path: Path) -> CrawlerGraph:
        with CrawlerStore(path) as store:
            return CrawlerGraph.from_store(store, config)

    return load


@pytest.fixture
def export_graph(db_path: Path, load_graph: Callable[[Path], CrawlerGraph]) -> CrawlerGraph:
    """Grafo de la base sintética."""
    return load_graph(db_path)


@pytest.fixture
def add_transition(db_path: Path, db_ids: Ids) -> Callable[[], None]:
    """Función que añade a la base una transición nueva: E2 -> E1 con "Imprimir"."""

    def add() -> None:
        with CrawlerStore(db_path) as store:
            store.record_transition(
                db_ids["E2"], db_ids["E1"], "invoke", "passive", element_id=db_ids["E2.Imprimir"]
            )

    return add
