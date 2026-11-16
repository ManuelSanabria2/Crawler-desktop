"""Tests de las órdenes del ejecutor (casos 1 a 6 de la especificación C08a)."""

from typing import Any

import pytest
from pydantic import ValidationError

from geass.crawler.actions import ActionRequest, ElementExpectation, Expectation


def test_element_request_and_describe() -> None:
    request = ActionRequest(kind="element", app="notepad.exe", name="Archivo")
    assert request.describe() == 'Usar "Archivo" en notepad.exe'
    request = ActionRequest(
        kind="element", app="notepad.exe", name="Archivo", action="expand", control_type="MenuItem"
    )
    assert request.describe() == 'Desplegar "Archivo" (MenuItem) en notepad.exe'
    request = ActionRequest(
        kind="element", app="CalculatorApp.exe", automation_id="num2Button", action="invoke"
    )
    assert request.describe() == "Pulsar [num2Button] en CalculatorApp.exe"


def test_other_describes() -> None:
    assert ActionRequest(kind="launch", app="spotify").describe() == 'Abrir "spotify"'
    assert ActionRequest(kind="explorer").describe() == "Abrir el explorador"
    assert (
        ActionRequest(kind="explorer", path="C:/Temp").describe()
        == "Abrir el explorador en C:/Temp"
    )
    assert ActionRequest(kind="media", media="mute").describe() == "Tecla multimedia mute"


@pytest.mark.parametrize(
    "fields",
    [
        {"app": "notepad", "name": "Archivo"},
        {"app": "notepad.exe"},
        {"app": "notepad.exe", "name": "  "},
        {"app": "notepad.exe", "name": "Archivo", "media": "mute"},
        {"app": "notepad.exe", "name": "Archivo", "path": "C:/"},
    ],
)
def test_invalid_element_requests(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ActionRequest(kind="element", **fields)


def test_element_app_suffix_is_case_insensitive() -> None:
    assert ActionRequest(kind="element", app="Notepad.EXE", name="Archivo").app == "Notepad.EXE"


@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        ("launch", {}),
        ("launch", {"app": "spotify", "name": "Archivo"}),
        ("launch", {"app": "spotify", "authorize": True}),
        ("launch", {"app": "spotify", "media": "mute"}),
        ("explorer", {"media": "mute"}),
        ("explorer", {"app": "explorer.exe"}),
        ("explorer", {"authorize": True}),
    ],
)
def test_invalid_launch_and_explorer(kind: str, fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ActionRequest(kind=kind, **fields)


def test_invalid_media() -> None:
    with pytest.raises(ValidationError):
        ActionRequest(kind="media")
    with pytest.raises(ValidationError):
        ActionRequest(kind="media", media="mute", expect=Expectation(process="spotify.exe"))
    with pytest.raises(ValidationError):
        ActionRequest(kind="media", media="mute", app="spotify")


def test_expectations_need_a_condition() -> None:
    with pytest.raises(ValidationError):
        Expectation()
    with pytest.raises(ValidationError):
        Expectation(title="(")
    with pytest.raises(ValidationError):
        ElementExpectation()
    assert Expectation(new_window=True).new_window
    assert Expectation(element=ElementExpectation(automation_id="CalculatorResults")).element


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (ActionRequest, {"kind": "launch", "app": "spotify"}),
        (Expectation, {"process": "spotify.exe"}),
        (ElementExpectation, {"name": "Archivo"}),
    ],
)
def test_unknown_fields_are_rejected(model: Any, fields: dict[str, Any]) -> None:
    model(**fields)
    with pytest.raises(ValidationError):
        model(**fields, foo=1)
