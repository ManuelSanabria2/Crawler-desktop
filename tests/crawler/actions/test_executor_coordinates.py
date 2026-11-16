"""Tests del ejecutor: clic por coordenadas y detector visual (casos 66 a 73 de C08a)."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.actions import (
    ActionRequest,
    DenyAllConfirmer,
    Executor,
    VisualCandidate,
)
from geass.crawler.uia.schema import Rect

PANEL = ActionRequest(kind="element", app="mspaint.exe", name="Panel")
CANVAS = ActionRequest(kind="element", app="mspaint.exe", name="Lienzo")
CANVAS_RECT = Rect(left=500, top=300, right=700, bottom=500)


def test_coordinate_click(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.paint_world()
    result = make_executor(desktop).run(PANEL)
    assert result.status == "done"
    assert result.strategy == "coordinate_click"
    assert desktop.clicks == [fakes.PANEL_CENTER]
    assert desktop.performed == []
    assert desktop.get("Color 2").props.automation_id == "StatusText"


def test_covered_target_is_not_clicked(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.paint_world()
    desktop.point_override[fakes.PANEL_CENTER] = desktop.get("Color 1")
    result = make_executor(desktop).run(PANEL)
    assert result.status == "failed"
    assert "otro elemento tapa el objetivo" in result.message
    assert desktop.clicks == []


@pytest.mark.parametrize("change", [{"rect": None}, {"offscreen": True}])
def test_no_rect_or_offscreen(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], change: dict[str, Any]
) -> None:
    desktop = fakes.paint_world()
    desktop.get("Panel").change(**change)
    result = make_executor(desktop).run(PANEL)
    assert result.status == "failed"
    assert desktop.clicks == []


def test_without_coordinate_click(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.paint_world()
    result = make_executor(desktop, config=fakes.without_step(config, "coordinate_click")).run(
        PANEL
    )
    assert result.status == "failed"
    assert "no tiene patrones de acción" in result.message
    assert desktop.clicks == []


def test_destructive_click_is_denied(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    gate_events: list[tuple[Any, ...]],
) -> None:
    desktop = fakes.paint_world("Eliminar")
    deny = DenyAllConfirmer()
    request = PANEL.model_copy(update={"name": "Eliminar"})
    result = make_executor(desktop, confirmer=deny).run(request)
    assert result.status == "denied"
    assert desktop.clicks == []
    assert len(deny.requests) == 1
    ops = [event[3] for event in gate_events if event[0] == "gate"]
    assert ops == ["coordinate_click"]
    assert result.attempts[-1].op == "coordinate_click"


def test_without_detector(fakes: SimpleNamespace, make_executor: Callable[..., Executor]) -> None:
    desktop = fakes.paint_world()
    result = make_executor(desktop).run(CANVAS)
    assert result.status == "not_found"
    assert "no hay detector visual" in result.message
    # Sin detector se sigue con la estrategia siguiente: el Panel se pulsa igual.
    result = make_executor(desktop).run(PANEL)
    assert result.status == "done"


def test_detector_confirmed_by_element_at(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor]
) -> None:
    desktop = fakes.paint_world()
    detector = fakes.FakeDetector([VisualCandidate(rect=CANVAS_RECT, label="button", score=0.9)])
    result = make_executor(desktop, detector=detector).run(CANVAS)
    assert result.status == "done"
    assert result.strategy == "visual_detector"
    assert desktop.clicks == [(600, 400)]
    assert detector.queries[0].name == "Lienzo"


def test_detector_below_threshold_or_mismatch(
    fakes: SimpleNamespace, make_executor: Callable[..., Executor], config: CrawlerConfig
) -> None:
    desktop = fakes.paint_world()
    low = config.vision.score_threshold / 2
    detector = fakes.FakeDetector([VisualCandidate(rect=CANVAS_RECT, label="button", score=low)])
    result = make_executor(desktop, detector=detector).run(CANVAS)
    assert result.status == "not_found"
    assert desktop.clicks == []

    desktop = fakes.paint_world()
    desktop.get("Lienzo").rename("Otra cosa")
    detector = fakes.FakeDetector([VisualCandidate(rect=CANVAS_RECT, label="button", score=0.9)])
    result = make_executor(desktop, detector=detector).run(CANVAS)
    assert result.status == "not_found"
    assert "el detector no coincide con lo que hay en pantalla" in result.message
    assert desktop.clicks == []
