"""Tests de la cobertura por app (casos 27 a 29 de la especificación C05)."""

import pytest

from geass.crawler.graph import AppNotFoundError, CrawlerGraph


def test_coverage_of_the_editor(graph: CrawlerGraph) -> None:
    entries = graph.coverage("editor")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.app.executable == "editor.exe"
    assert entry.states == 5
    assert entry.visits == 7
    assert entry.elements == 12
    assert entry.actionable_elements == 9
    assert entry.exercised_elements == 5
    assert entry.element_coverage == pytest.approx(5 / 9)
    assert entry.transitions == 6
    assert entry.observations == 8
    assert entry.passive_transitions == 5
    assert entry.active_transitions == 1
    assert entry.executable_transitions == 5
    assert entry.transitions_without_element == 1
    assert entry.isolated_states == 1
    assert entry.dead_end_states == 1


def test_coverage_of_every_app_sorted_by_executable(graph: CrawlerGraph) -> None:
    entries = graph.coverage()
    assert [entry.app.executable for entry in entries] == ["calc.exe", "editor.exe"]
    calc = entries[0]
    assert calc.states == 1
    assert calc.actionable_elements == 2
    assert calc.exercised_elements == 0
    assert calc.element_coverage == 0.0
    assert calc.transitions == 0
    assert calc.isolated_states == 1
    assert calc.dead_end_states == 1


def test_coverage_of_an_unknown_app(graph: CrawlerGraph) -> None:
    with pytest.raises(AppNotFoundError):
        graph.coverage("zzzz")
