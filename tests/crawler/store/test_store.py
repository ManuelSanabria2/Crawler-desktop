"""Tests de la base SQLite del crawler, con un archivo temporal."""

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from geass.core.config import load_crawler_config
from geass.crawler.signature import compute_signature, default_rules
from geass.crawler.store import (
    CrawlerStore,
    ElementInput,
    ExcludedAppRecordError,
    StoreError,
    record_dump,
)
from geass.crawler.store.migrations import MIGRATIONS, Migration
from geass.crawler.uia import Rect, WindowDump

START = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "crawler" / "geass_crawler.sqlite"


@pytest.fixture
def store(db_path: Path, clock: FakeClock) -> Iterator[CrawlerStore]:
    with CrawlerStore(db_path, clock=clock) as opened:
        yield opened


def element(path: str, **changes: object) -> ElementInput:
    data = {
        "path": path,
        "control_type": "Button",
        "name": "Aceptar",
        "automation_id": "ok",
        "rect": Rect(left=10, top=20, right=110, bottom=50),
        "patterns": ["Invoke"],
        "enabled": True,
    }
    data.update(changes)
    return ElementInput(**data)


def two_states(store: CrawlerStore) -> tuple[int, int]:
    app = store.upsert_app("app.exe")
    first = store.upsert_state(app.id, "s1", "d1", "app")
    second = store.upsert_state(app.id, "s2", "d2", "app")
    return first.id, second.id


# Esquema, versión y migraciones


def test_new_database_has_the_schema_and_version(store: CrawlerStore, db_path: Path) -> None:
    assert db_path.is_file()
    assert store.schema_version() == len(MIGRATIONS) == 1
    raw = sqlite3.connect(db_path)
    tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    raw.close()
    assert {"apps", "states", "elements", "transitions", "sessions", "schema_version"} <= tables
    assert store.counts() == {
        "apps": 0,
        "states": 0,
        "elements": 0,
        "transitions": 0,
        "sessions": 0,
        "screenshots": 0,
    }


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("apps", {"id", "executable", "display_name", "app_user_model_id"}),
        (
            "states",
            {
                "id",
                "app_id",
                "structural_signature",
                "detailed_signature",
                "title_pattern",
                "first_seen",
                "last_seen",
                "visits",
                "screenshot_path",
            },
        ),
        (
            "elements",
            {
                "id",
                "state_id",
                "path",
                "control_type",
                "name",
                "automation_id",
                "patterns",
                "enabled",
            },
        ),
        (
            "transitions",
            {
                "id",
                "source_state_id",
                "target_state_id",
                "element_id",
                "action_type",
                "source",
                "count",
                "mean_latency_s",
                "last_seen",
            },
        ),
        ("sessions", {"id", "started_at", "ended_at", "mode", "notes"}),
    ],
)
def test_tables_have_the_columns_of_section_6_4(
    store: CrawlerStore, db_path: Path, table: str, columns: set[str]
) -> None:
    raw = sqlite3.connect(db_path)
    found = {row[1] for row in raw.execute(f"PRAGMA table_info({table})")}
    raw.close()
    assert columns <= found


def test_reopening_keeps_data_and_does_not_migrate_again(db_path: Path, clock: FakeClock) -> None:
    with CrawlerStore(db_path, clock=clock) as first:
        first.upsert_app("notepad.exe", "Bloc de notas")
    with CrawlerStore(db_path, clock=clock) as second:
        assert second.schema_version() == 1
        assert second.find_app("notepad.exe").display_name == "Bloc de notas"
    raw = sqlite3.connect(db_path)
    assert raw.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    raw.close()


def test_new_migration_is_applied_to_an_existing_database(db_path: Path, clock: FakeClock) -> None:
    with CrawlerStore(db_path, clock=clock) as old:
        old.upsert_app("app.exe")
    extra = Migration(2, "columna de prueba", ("ALTER TABLE apps ADD COLUMN extra TEXT",))
    with CrawlerStore(db_path, clock=clock, migrations=(*MIGRATIONS, extra)) as new:
        assert new.schema_version() == 2
        assert new.find_app("app.exe") is not None
    raw = sqlite3.connect(db_path)
    assert "extra" in {row[1] for row in raw.execute("PRAGMA table_info(apps)")}
    versions = raw.execute("SELECT version, applied_at FROM schema_version ORDER BY version")
    assert [row[0] for row in versions] == [1, 2]
    raw.close()


def test_failed_migration_rolls_back(db_path: Path, clock: FakeClock) -> None:
    broken = Migration(
        2, "rota", ("ALTER TABLE apps ADD COLUMN extra TEXT", "ALTER TABLE no_existe ADD x")
    )
    with pytest.raises(StoreError, match="migración 2"):
        CrawlerStore(db_path, clock=clock, migrations=(*MIGRATIONS, broken))
    with CrawlerStore(db_path, clock=clock) as store:
        assert store.schema_version() == 1
    raw = sqlite3.connect(db_path)
    assert "extra" not in {row[1] for row in raw.execute("PRAGMA table_info(apps)")}
    raw.close()


def test_database_newer_than_the_code_is_rejected(db_path: Path, clock: FakeClock) -> None:
    extra = Migration(2, "futura", ("CREATE TABLE futura (id INTEGER)",))
    CrawlerStore(db_path, clock=clock, migrations=(*MIGRATIONS, extra)).close()
    with pytest.raises(StoreError, match="más nuevo"):
        CrawlerStore(db_path, clock=clock)


def test_migrations_must_be_numbered_in_order(db_path: Path) -> None:
    with pytest.raises(StoreError, match="sin saltos"):
        CrawlerStore(db_path, migrations=(Migration(2, "salto", ()),))


def test_from_config_uses_db_path_of_crawler_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEASS_ROOT", str(tmp_path))
    config = load_crawler_config(config_dir=Path(__file__).resolve().parents[3] / "config")
    with CrawlerStore.from_config(config) as store:
        assert store.path == tmp_path / "data" / "crawler" / "geass_crawler.sqlite"
        assert store.path.is_file()


# Sesiones


def test_session_lifecycle(store: CrawlerStore, clock: FakeClock) -> None:
    session_id = store.start_session("passive", notes="prueba")
    opened = store.get_session(session_id)
    assert opened.mode == "passive"
    assert opened.started_at == START
    assert opened.ended_at is None
    assert opened.notes == "prueba"

    clock.advance(minutes=30)
    closed = store.end_session(session_id)
    assert closed.ended_at == START + timedelta(minutes=30)
    assert closed.notes == "prueba"
    assert store.end_session(session_id, notes="cerrada").notes == "cerrada"
    assert [session.id for session in store.list_sessions()] == [session_id]


def test_invalid_session_mode_and_unknown_session(store: CrawlerStore) -> None:
    with pytest.raises(StoreError, match="modo de sesión"):
        store.start_session("otro")  # type: ignore[arg-type]
    with pytest.raises(StoreError, match="sessions"):
        store.end_session(99)


# Apps


def test_upsert_app_registers_and_updates(store: CrawlerStore) -> None:
    created = store.upsert_app("Notepad.exe", "Bloc de notas")
    assert created.executable == "notepad.exe"
    assert created.display_name == "Bloc de notas"
    assert created.app_user_model_id is None

    updated = store.upsert_app("NOTEPAD.EXE", app_user_model_id="Microsoft.WindowsNotepad")
    assert updated.id == created.id
    assert updated.display_name == "Bloc de notas"
    assert updated.app_user_model_id == "Microsoft.WindowsNotepad"
    assert store.upsert_app("notepad.exe", "Notepad").display_name == "Notepad"
    assert len(store.list_apps()) == 1
    assert store.find_app("otra.exe") is None


def test_app_without_display_name_uses_the_executable(store: CrawlerStore) -> None:
    assert store.upsert_app("Calc.exe").display_name == "Calc.exe"
    with pytest.raises(StoreError, match="vacío"):
        store.upsert_app("  ")


# Estados


def test_upsert_state_counts_visits(store: CrawlerStore, clock: FakeClock) -> None:
    app = store.upsert_app("app.exe")
    first = store.upsert_state(app.id, "struct", "detail-a", "app")
    assert first.visits == 1
    assert first.first_seen == first.last_seen == START
    assert first.screenshot_path is None

    clock.advance(seconds=90)
    second = store.upsert_state(app.id, "struct", "detail-b", "app nueva", screenshot_path="a.png")
    assert second.id == first.id
    assert second.visits == 2
    assert second.first_seen == START
    assert second.last_seen == START + timedelta(seconds=90)
    assert second.detailed_signature == "detail-b"
    assert second.title_pattern == "app nueva"
    assert second.screenshot_path == "a.png"

    third = store.upsert_state(app.id, "struct", "detail-b", "app nueva")
    assert third.visits == 3
    assert third.screenshot_path == "a.png"


def test_same_signature_in_another_app_is_another_state(store: CrawlerStore) -> None:
    one, two = store.upsert_app("a.exe"), store.upsert_app("b.exe")
    first = store.upsert_state(one.id, "struct", "d", "a")
    second = store.upsert_state(two.id, "struct", "d", "b")
    assert first.id != second.id
    assert len(store.list_states()) == 2
    assert [state.id for state in store.list_states(one.id)] == [first.id]
    assert store.find_state(two.id, "struct").id == second.id
    assert store.find_state(two.id, "otra") is None


def test_state_needs_an_existing_app(store: CrawlerStore) -> None:
    with pytest.raises(StoreError, match="apps"):
        store.upsert_state(42, "s", "d", "t")


# Elementos


def test_upsert_elements_stores_and_reads_back(store: CrawlerStore) -> None:
    state_id, _ = two_states(store)
    ids = store.upsert_elements(
        state_id,
        [
            element("Window[0]/Button[0]"),
            element("Window[0]/Edit[0]", control_type="Edit", name="", rect=None, patterns=[]),
        ],
    )
    assert set(ids) == {"Window[0]/Button[0]", "Window[0]/Edit[0]"}
    stored = {item.path: item for item in store.list_elements(state_id)}
    button = stored["Window[0]/Button[0]"]
    assert button.id == ids["Window[0]/Button[0]"]
    assert button.state_id == state_id
    assert button.rect == Rect(left=10, top=20, right=110, bottom=50)
    assert button.patterns == ["Invoke"]
    assert button.enabled is True
    assert stored["Window[0]/Edit[0]"].rect is None
    assert store.get_element(button.id) == button


def test_upsert_elements_updates_and_keeps_ids(store: CrawlerStore) -> None:
    state_id, other_state = two_states(store)
    first = store.upsert_elements(state_id, [element("a"), element("b")])
    second = store.upsert_elements(
        state_id, [element("a", name="Guardar", enabled=False), element("c")]
    )
    assert second["a"] == first["a"]
    assert set(second) == {"a", "c"}
    stored = {item.path: item for item in store.list_elements(state_id)}
    assert set(stored) == {"a", "b", "c"}
    assert stored["a"].name == "Guardar"
    assert stored["a"].enabled is False
    assert store.upsert_elements(other_state, [element("a")])["a"] != first["a"]


def test_elements_need_an_existing_state(store: CrawlerStore) -> None:
    with pytest.raises(StoreError, match="states"):
        store.upsert_elements(7, [element("a")])
    with pytest.raises(StoreError, match="elements"):
        store.get_element(7)


# Transiciones


def test_transition_count_and_mean_latency(store: CrawlerStore, clock: FakeClock) -> None:
    source, target = two_states(store)
    button = store.upsert_elements(source, [element("a")])["a"]

    first = store.record_transition(
        source, target, "invoke", "passive", element_id=button, latency_s=0.2
    )
    assert first.count == 1
    assert first.mean_latency_s == pytest.approx(0.2)
    assert first.last_seen == START

    clock.advance(seconds=5)
    second = store.record_transition(
        source, target, "invoke", "passive", element_id=button, latency_s=0.4
    )
    third = store.record_transition(
        source, target, "invoke", "passive", element_id=button, latency_s=0.9
    )
    assert second.id == third.id == first.id
    assert second.mean_latency_s == pytest.approx(0.3)
    assert third.count == 3
    assert third.mean_latency_s == pytest.approx(0.5)
    assert third.latency_samples == 3
    assert third.last_seen == START + timedelta(seconds=5)
    assert len(store.list_transitions()) == 1


def test_transition_without_latency_counts_but_keeps_the_mean(store: CrawlerStore) -> None:
    source, target = two_states(store)
    assert store.record_transition(source, target, "invoke", "passive").mean_latency_s is None
    store.record_transition(source, target, "invoke", "passive", latency_s=1.0)
    last = store.record_transition(source, target, "invoke", "passive")
    assert last.count == 3
    assert last.latency_samples == 1
    assert last.mean_latency_s == pytest.approx(1.0)
    assert last.element_id is None


def test_transitions_differ_by_element_action_source_and_direction(store: CrawlerStore) -> None:
    source, target = two_states(store)
    ids = store.upsert_elements(source, [element("a"), element("b")])
    variants = [
        store.record_transition(source, target, "invoke", "passive", element_id=ids["a"]),
        store.record_transition(source, target, "invoke", "passive", element_id=ids["b"]),
        store.record_transition(source, target, "invoke", "passive"),
        store.record_transition(source, target, "toggle", "passive", element_id=ids["a"]),
        store.record_transition(source, target, "invoke", "active", element_id=ids["a"]),
        store.record_transition(target, source, "invoke", "passive", element_id=ids["a"]),
        store.record_transition(source, source, "invoke", "passive", element_id=ids["a"]),
    ]
    assert len({transition.id for transition in variants}) == len(variants)
    assert all(transition.count == 1 for transition in variants)
    assert len(store.list_transitions(source)) == 6
    assert len(store.list_transitions(target)) == 1


def test_invalid_transitions_are_rejected(store: CrawlerStore) -> None:
    source, target = two_states(store)
    with pytest.raises(StoreError, match="tipo de acción"):
        store.record_transition(source, target, "destroy", "passive")  # type: ignore[arg-type]
    with pytest.raises(StoreError, match="fuente"):
        store.record_transition(source, target, "invoke", "manual")  # type: ignore[arg-type]
    with pytest.raises(StoreError, match="negativa"):
        store.record_transition(source, target, "invoke", "passive", latency_s=-1.0)
    with pytest.raises(StoreError, match="states"):
        store.record_transition(source, 999, "invoke", "passive")
    with pytest.raises(StoreError, match="elements"):
        store.record_transition(source, target, "invoke", "passive", element_id=999)
    assert store.list_transitions() == []


def test_concurrent_transitions_are_all_counted(store: CrawlerStore) -> None:
    source, target = two_states(store)
    per_thread, threads = 25, 8

    def work() -> None:
        for _ in range(per_thread):
            store.record_transition(source, target, "invoke", "passive", latency_s=0.1)

    workers = [threading.Thread(target=work) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    (transition,) = store.list_transitions()
    assert transition.count == per_thread * threads
    assert transition.mean_latency_s == pytest.approx(0.1)


def test_deleting_an_app_cascades(store: CrawlerStore, db_path: Path) -> None:
    source, target = two_states(store)
    button = store.upsert_elements(source, [element("a")])["a"]
    store.record_transition(source, target, "invoke", "passive", element_id=button)
    store.close()
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("DELETE FROM apps")
    raw.commit()
    for table in ("states", "elements", "transitions"):
        assert raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    raw.close()


# Guardar un volcado completo


@pytest.fixture
def no_exclusions() -> list[str]:
    return []


def test_record_dump_stores_app_state_and_elements(
    store: CrawlerStore,
    uia_fixture: Callable[[str], WindowDump],
) -> None:
    dump = uia_fixture("calculator_a")
    recorded = record_dump(store, dump, excluded_apps=[])
    signature = compute_signature(dump)

    assert recorded.is_new is True
    assert store.get_app(recorded.app_id).executable == "calculatorapp.exe"
    assert recorded.state.structural_signature == signature.structural
    assert recorded.state.detailed_signature == signature.detailed
    assert recorded.state.title_pattern == "calculadora"
    assert recorded.state.visits == 1

    stored = store.list_elements(recorded.state.id)
    assert len(stored) == signature.token_count == len(recorded.element_ids)
    by_id = {item.automation_id: item for item in stored if item.automation_id}
    seven = by_id["num7Button"]
    assert seven.control_type == "Button"
    assert "Invoke" in seven.patterns

    window = dump.root.rect
    original = dump.root.find_all(automation_id="num7Button")[0].rect
    assert seven.rect == Rect(
        left=original.left - window.left,
        top=original.top - window.top,
        right=original.right - window.left,
        bottom=original.bottom - window.top,
    )
    root = next(item for item in stored if item.path == "Window[0]")
    assert (root.rect.left, root.rect.top) == (0, 0)


def test_record_dump_twice_is_one_state_with_two_visits(
    store: CrawlerStore, uia_fixture: Callable[[str], WindowDump]
) -> None:
    first = record_dump(store, uia_fixture("calculator_a"), excluded_apps=[])
    second = record_dump(store, uia_fixture("calculator_b"), excluded_apps=[])
    assert second.is_new is False
    assert second.state.id == first.state.id
    assert second.state.visits == 2
    assert second.element_ids == first.element_ids
    assert store.counts()["states"] == 1
    assert store.counts()["apps"] == 1


def test_record_dump_two_views_of_the_same_app(
    store: CrawlerStore, uia_fixture: Callable[[str], WindowDump]
) -> None:
    mouse = record_dump(store, uia_fixture("settings_mouse"), excluded_apps=[])
    colors = record_dump(store, uia_fixture("settings_colors"), excluded_apps=[])
    assert mouse.app_id == colors.app_id
    assert mouse.state.id != colors.state.id
    transition = store.record_transition(
        mouse.state.id, colors.state.id, "invoke", "passive", latency_s=0.35
    )
    assert transition.count == 1
    assert store.counts()["states"] == 2


def test_record_dump_does_not_store_volatile_contents_or_file_names(
    store: CrawlerStore, db_path: Path, uia_fixture: Callable[[str], WindowDump]
) -> None:
    dump = uia_fixture("notepad_long")
    assert "informe 2026-11-11 v2.txt" in dump.title
    recorded = record_dump(store, dump, excluded_apps=[])
    stored = store.list_elements(recorded.state.id)

    assert recorded.state.title_pattern == "bloc de notas"
    assert not [item for item in stored if item.control_type in ("TabItem", "ListItem")]
    assert not [item for item in stored if item.control_type in ("ScrollBar", "Thumb")]
    (document,) = [item for item in stored if item.control_type == "Document"]
    assert document.name == "Editor de texto"

    store.close()
    content = db_path.read_bytes()
    content += (
        db_path.with_name(db_path.name + "-wal").read_bytes()
        if (db_path.with_name(db_path.name + "-wal").exists())
        else b""
    )
    assert b"informe 2026" not in content
    assert b"nota.txt" not in content
    assert b"archivo_ajeno" not in content


def test_record_dump_refuses_excluded_apps(
    store: CrawlerStore, uia_fixture: Callable[[str], WindowDump]
) -> None:
    dump = uia_fixture("notepad_short")
    with pytest.raises(ExcludedAppRecordError, match="excluida"):
        record_dump(store, dump, excluded_apps=["NOTEPAD.EXE"])
    assert store.counts()["apps"] == 0

    data = json.loads(dump.model_dump_json())
    data["process_name"] = ""
    with pytest.raises(ExcludedAppRecordError, match="proceso"):
        record_dump(store, WindowDump.model_validate(data), excluded_apps=[])
    assert store.counts() == dict.fromkeys(store.counts(), 0)


def test_record_dump_uses_the_exclusion_list_of_crawler_yaml(
    store: CrawlerStore, uia_fixture: Callable[[str], WindowDump]
) -> None:
    data = json.loads(uia_fixture("calculator_a").model_dump_json())
    data["process_name"] = "chrome.exe"
    with pytest.raises(ExcludedAppRecordError):
        record_dump(store, WindowDump.model_validate(data))
    assert record_dump(store, uia_fixture("calculator_a"), rules=default_rules()).is_new
