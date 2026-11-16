"""Tests del índice de apps (casos 1 a 10 de la especificación C07).

El menú Inicio es una carpeta de ``tmp_path`` y Get-StartApps es un doble: nada toca el
sistema.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions import ActionError, AppEntry, AppIndex


def by_name(index: AppIndex) -> dict[str, AppEntry]:
    return {entry.name: entry for entry in index.entries}


def test_build_lists_the_nine_apps(index: AppIndex, all_names: list[str]) -> None:
    assert [entry.name for entry in index.entries] == all_names
    assert all("leeme" not in entry.name for entry in index.entries)


def test_duplicates_keep_the_first(index: AppIndex, menu_dirs: tuple[Path, Path]) -> None:
    entries = by_name(index)
    assert [entry.name for entry in index.entries].count("Spotify") == 1
    assert entries["Spotify"].shortcut_path == str(menu_dirs[0] / "Spotify.lnk")
    calculator = entries["Calculadora"]
    assert calculator.source == "shortcut"
    assert calculator.shortcut_path == str(menu_dirs[0] / "Herramientas" / "Calculadora.lnk")
    assert calculator.app_id is None


def test_start_apps_entries(index: AppIndex, photos_app_id: str) -> None:
    entries = by_name(index)
    settings = entries["Configuración"]
    assert settings.source == "start_apps"
    assert settings.app_id == (
        "windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel"
    )
    assert settings.shortcut_path is None
    photos = entries["Fotos"]
    assert photos.source == "start_apps"
    assert photos.app_id == photos_app_id
    assert photos.name == photos.name.strip()


def test_blocked_entries(index: AppIndex) -> None:
    blocked = {entry.name for entry in index.entries if entry.blocked}
    assert blocked == {"Desinstalar Foo"}


def test_without_store_apps(
    config: CrawlerConfig,
    safety: SafetyConfig,
    fake_system: Any,
    launcher_with: Callable[..., CrawlerConfig],
) -> None:
    index = AppIndex.build(launcher_with(config, include_store_apps=False), safety)
    assert fake_system.calls_to("read_start_apps") == []
    assert len(index.entries) == 7
    assert all(entry.source == "shortcut" for entry in index.entries)


@pytest.mark.parametrize("failure", ["error", "not_json"])
def test_start_apps_failure_keeps_the_shortcuts(
    failure: str, config: CrawlerConfig, safety: SafetyConfig, fake_system: Any
) -> None:
    if failure == "error":
        fake_system.start_apps_error = ActionError("PowerShell no respondió")
    else:
        fake_system.start_apps_output = "esto no es json"
    index = AppIndex.build(config, safety)
    assert len(index.entries) == 7
    assert all(entry.source == "shortcut" for entry in index.entries)


def test_single_start_app_as_object(
    config: CrawlerConfig, safety: SafetyConfig, fake_system: Any, photos_app_id: str
) -> None:
    fake_system.start_apps_output = json.dumps({"Name": "Fotos", "AppID": photos_app_id})
    entries = by_name(AppIndex.build(config, safety))
    assert entries["Fotos"].app_id == photos_app_id
    assert len(entries) == 8


def test_missing_start_menu_folder_is_skipped(
    config: CrawlerConfig,
    safety: SafetyConfig,
    tmp_path: Path,
    launcher_with: Callable[..., CrawlerConfig],
) -> None:
    folders = [str(tmp_path / "no_existe"), *config.launcher.start_menu_dirs]
    index = AppIndex.build(launcher_with(config, start_menu_dirs=folders), safety)
    assert len(index.entries) == 9


def test_environment_variables_are_expanded(
    config: CrawlerConfig,
    safety: SafetyConfig,
    menu_dirs: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    launcher_with: Callable[..., CrawlerConfig],
) -> None:
    monkeypatch.setenv("GEASS_TEST_MENU", str(menu_dirs[1]))
    changed = launcher_with(config, start_menu_dirs=["%GEASS_TEST_MENU%"], include_store_apps=False)
    names = [entry.name for entry in AppIndex.build(changed, safety).entries]
    assert names == ["Paint", "Spotify"]


class Clock:
    """Reloj controlable para probar la edad de la caché."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_load_uses_and_refreshes_the_cache(
    config: CrawlerConfig, safety: SafetyConfig, cache_path: Path, fake_system: Any
) -> None:
    clock = Clock()
    first = AppIndex.load(config, safety, clock=clock)
    assert cache_path.is_file()
    assert len(first.entries) == 9
    assert len(fake_system.calls_to("read_start_apps")) == 1

    second = AppIndex.load(config, safety, clock=clock)
    assert len(fake_system.calls_to("read_start_apps")) == 1
    assert second.entries == first.entries
    assert second.built_at == first.built_at

    AppIndex.load(config, safety, refresh=True, clock=clock)
    assert len(fake_system.calls_to("read_start_apps")) == 2

    clock.now += timedelta(seconds=config.launcher.cache_max_age_s + 1)
    AppIndex.load(config, safety, clock=clock)
    assert len(fake_system.calls_to("read_start_apps")) == 3


def test_explicit_cache_path(config: CrawlerConfig, safety: SafetyConfig, tmp_path: Path) -> None:
    other = tmp_path / "otra" / "indice.json"
    AppIndex.load(config, safety, cache_path=other)
    assert other.is_file()


def test_corrupt_cache_is_rebuilt(
    config: CrawlerConfig, safety: SafetyConfig, cache_path: Path, fake_system: Any
) -> None:
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text("{", encoding="utf-8")
    index = AppIndex.load(config, safety)
    assert len(index.entries) == 9
    assert len(fake_system.calls_to("read_start_apps")) == 1
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(data) == {"built_at", "entries"}
    assert len(data["entries"]) == 9
    assert datetime.fromisoformat(data["built_at"]).utcoffset() == timedelta(0)


def test_cache_recomputes_blocked(
    config: CrawlerConfig, safety: SafetyConfig, cache_path: Path
) -> None:
    AppIndex.load(config, safety)
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    for entry in data["entries"]:
        entry["blocked"] = entry["name"] == "Spotify"
    cache_path.write_text(json.dumps(data), encoding="utf-8")
    blocked = {entry.name for entry in AppIndex.load(config, safety).entries if entry.blocked}
    assert blocked == {"Desinstalar Foo"}
