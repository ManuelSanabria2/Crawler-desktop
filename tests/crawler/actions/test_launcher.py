"""Tests de la coincidencia y el lanzamiento de apps (casos 11 a 22 de la especificación C07).

Las funciones de lanzamiento de ``system`` son dobles: nada se abre de verdad.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions import (
    ActionError,
    AppEntry,
    AppIndex,
    AuditLog,
    LaunchResult,
    launch,
    match_app,
)


@pytest.fixture
def run_launch(
    index: AppIndex, config: CrawlerConfig, safety: SafetyConfig, audit: AuditLog
) -> Callable[..., LaunchResult]:
    """``launch`` con el índice, la configuración y la auditoría de prueba."""

    def run(name: str, **kwargs: Any) -> LaunchResult:
        kwargs.setdefault("authorized_by", "test")
        return launch(name, index=index, config=config, safety=safety, audit=audit, **kwargs)

    return run


@pytest.mark.parametrize("query", ["spotify", "SPOTIFY", "spotfy"])
def test_match_spotify(index: AppIndex, config: CrawlerConfig, query: str) -> None:
    result = match_app(index, query, config)
    assert result.status == "would_launch"
    assert result.entry is not None and result.entry.name == "Spotify"
    assert result.options == []
    assert result.query == query


def test_match_without_accent(index: AppIndex, config: CrawlerConfig) -> None:
    result = match_app(index, "configuracion", config)
    assert result.status == "would_launch"
    assert result.entry is not None and result.entry.name == "Configuración"


def test_match_ambiguous(index: AppIndex, config: CrawlerConfig) -> None:
    result = match_app(index, "visual studio", config)
    assert result.status == "ambiguous"
    assert result.entry is None
    names = {option.entry.name for option in result.options}
    assert {"Visual Studio 2022", "Visual Studio Code"} <= names
    assert len(result.options) <= config.launcher.max_options


def test_exact_match_wins(index: AppIndex, config: CrawlerConfig) -> None:
    result = match_app(index, "visual studio code", config)
    assert result.status == "would_launch"
    assert result.entry is not None and result.entry.name == "Visual Studio Code"


def test_not_found_suggests(index: AppIndex, config: CrawlerConfig) -> None:
    result = match_app(index, "zzzzqqq", config)
    assert result.status == "not_found"
    assert result.entry is None
    assert len(result.options) <= config.launcher.max_options


def test_not_found_suggestions_are_the_best_scores(
    index: AppIndex, config: CrawlerConfig, launcher_with: Callable[..., CrawlerConfig]
) -> None:
    strict = launcher_with(config, fuzzy_match_threshold=100, max_options=2)
    result = match_app(index, "spotfy", strict)
    assert result.status == "not_found"
    assert len(result.options) == 2
    assert result.options[0].entry.name == "Spotify"
    assert result.options[0].score >= result.options[1].score


def test_blocked_match(index: AppIndex, config: CrawlerConfig) -> None:
    result = match_app(index, "desinstalar foo", config)
    assert result.status == "blocked"
    assert result.entry is not None and result.entry.name == "Desinstalar Foo"
    assert result.options == []


def test_empty_query(index: AppIndex, config: CrawlerConfig) -> None:
    with pytest.raises(ValueError):
        match_app(index, "   ", config)


def test_two_exact_matches_are_ambiguous(config: CrawlerConfig) -> None:
    built_at = datetime(2026, 11, 14, tzinfo=UTC)
    entries = [
        AppEntry(name=name, source="shortcut", shortcut_path=path, app_id=None, blocked=False)
        for name, path in (("Paint", "a/Paint.lnk"), ("paint", "b/paint.lnk"))
    ]
    result = match_app(AppIndex(entries, built_at=built_at), "paint", config)
    assert result.status == "ambiguous"
    assert result.entry is None


def test_launch_shortcut(
    run_launch: Callable[..., LaunchResult], fake_system: Any, menu_dirs: tuple[Path, Path]
) -> None:
    result = run_launch("spotify")
    assert result.status == "launched"
    assert result.entry is not None and result.entry.name == "Spotify"
    assert fake_system.calls_to("start_shortcut") == [(menu_dirs[0] / "Spotify.lnk",)]
    assert fake_system.calls_to("start_app_id") == []


def test_launch_app_id(
    run_launch: Callable[..., LaunchResult], fake_system: Any, photos_app_id: str
) -> None:
    result = run_launch("fotos")
    assert result.status == "launched"
    assert fake_system.calls_to("start_app_id") == [(photos_app_id,)]
    assert fake_system.calls_to("start_shortcut") == []


@pytest.mark.parametrize(
    ("query", "status"),
    [("visual studio", "ambiguous"), ("zzzzqqq", "not_found"), ("desinstalar foo", "blocked")],
)
def test_launch_does_nothing_when_in_doubt(
    run_launch: Callable[..., LaunchResult],
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
    query: str,
    status: str,
) -> None:
    result = run_launch(query)
    assert result.status == status
    assert fake_system.launch_calls() == []
    lines = read_audit(audit_path)
    assert len(lines) == 1
    assert lines[0]["result"] == status
    assert lines[0]["target"] == query
    expected_app = "Desinstalar Foo" if status == "blocked" else ""
    assert lines[0]["app"] == expected_app


def test_dry_run(
    run_launch: Callable[..., LaunchResult],
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    result = run_launch("spotify", dry_run=True)
    assert result.status == "would_launch"
    assert result.entry is not None and result.entry.name == "Spotify"
    assert fake_system.launch_calls() == []
    assert read_audit(audit_path) == []


def test_launch_error_is_audited_and_raised(
    run_launch: Callable[..., LaunchResult],
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    fake_system.errors["start_shortcut"] = ActionError("Windows no pudo abrirlo")
    with pytest.raises(ActionError):
        run_launch("spotify")
    lines = read_audit(audit_path)
    assert len(lines) == 1
    assert lines[0]["result"].startswith("error:")
    assert lines[0]["app"] == "Spotify"


def test_launch_needs_an_authorizer(
    run_launch: Callable[..., LaunchResult],
    fake_system: Any,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    with pytest.raises(ValueError):
        run_launch("spotify", authorized_by="")
    assert fake_system.launch_calls() == []
    assert read_audit(audit_path) == []
