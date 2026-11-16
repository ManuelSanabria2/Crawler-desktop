"""Tests del registro de auditoría (casos 31 a 33 de C07, y 83 y 84 de C08a)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig
from geass.crawler.actions import ActionRequest, AppIndex, AuditLog, Executor, launch

KEYS = {"at", "action", "target", "app", "result", "authorized_by"}


def record_one(audit: AuditLog, result: str = "done") -> None:
    audit.record(action="media", target="mute", app="", result=result, authorized_by="test")


def test_record_writes_json_lines(
    audit: AuditLog, audit_path: Path, read_audit: Callable[[Path], list[dict[str, Any]]]
) -> None:
    record_one(audit)
    lines = read_audit(audit_path)
    assert len(lines) == 1
    assert set(lines[0]) == KEYS
    assert lines[0]["at"].endswith(("+00:00", "Z"))

    record_one(audit, result="disabled")
    lines = read_audit(audit_path)
    assert [line["result"] for line in lines] == ["done", "disabled"]


def test_record_keeps_accents(audit_path: Path) -> None:
    audit = AuditLog(audit_path, clock=lambda: datetime(2026, 11, 14, 9, 30, tzinfo=UTC))
    audit.record(
        action="launch",
        target="configuración",
        app="Configuración",
        result="launched",
        authorized_by="test",
    )
    text = audit_path.read_text(encoding="utf-8")
    assert "Configuración" in text
    assert '"at": "2026-11-14T09:30:00+00:00"' in text


def test_record_needs_an_authorizer(audit: AuditLog, audit_path: Path) -> None:
    with pytest.raises(ValueError):
        audit.record(action="media", target="mute", app="", result="done", authorized_by="")
    assert not audit_path.exists()


def test_from_config_uses_the_safety_path(
    safety_tmp_audit: SafetyConfig,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    record_one(AuditLog.from_config(safety_tmp_audit))
    assert len(read_audit(audit_path)) == 1


def test_launch_is_audited(
    index: AppIndex,
    config: CrawlerConfig,
    safety: SafetyConfig,
    audit: AuditLog,
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    launch(
        "spotify",
        authorized_by="cli:manuel",
        index=index,
        config=config,
        safety=safety,
        audit=audit,
    )
    line = read_audit(audit_path)[-1]
    assert line["action"] == "launch"
    assert line["target"] == "spotify"
    assert line["app"] == "Spotify"
    assert line["result"] == "launched"
    assert line["authorized_by"] == "cli:manuel"


DETAIL_KEYS = {
    "strategy",
    "step",
    "try",
    "control_type",
    "automation_id",
    "gate_verdict",
    "gate_rule",
    "pattern",
    "confirmed_by",
    "verified",
}


def test_record_with_details(
    audit: AuditLog, audit_path: Path, read_audit: Callable[[Path], list[dict[str, Any]]]
) -> None:
    details = {"strategy": "graph_route", "step": 1}
    audit.record(
        action="ui:invoke",
        target="Archivo",
        app="Notepad.exe",
        result="done",
        authorized_by="test",
        details=details,
    )
    details["step"] = 2  # se guardó una copia
    record_one(audit)
    with_details, without = read_audit(audit_path)
    assert set(with_details) == KEYS | {"details"}
    assert with_details["details"] == {"strategy": "graph_route", "step": 1}
    assert set(without) == KEYS


def test_executor_lines(
    fakes: SimpleNamespace,
    make_executor: Callable[..., Executor],
    audit_path: Path,
    read_audit: Callable[[Path], list[dict[str, Any]]],
) -> None:
    desktop = fakes.settings_world()
    request = ActionRequest(
        kind="element", app="SystemSettings.exe", name="Sistema", control_type="ListItem"
    )
    assert make_executor(desktop, authorized_by="cli:manuel").run(request).status == "done"
    lines = read_audit(audit_path)
    ui = [line for line in lines if line["action"].startswith("ui:")]
    assert ui
    for line in ui:
        assert set(line["details"]) == DETAIL_KEYS
        assert line["authorized_by"] == "cli:manuel"
        assert line["app"] == "SystemSettings.exe"
        assert line["details"]["confirmed_by"] is None
    final = lines[-1]
    assert final["action"] == "execute"
    assert set(final["details"]) == {"strategy", "attempts", "performed", "message"}
    assert (final["details"]["attempts"], final["details"]["performed"]) == (1, 1)
    title = desktop.window(fakes.SETTINGS).ref.title
    assert all(title not in json.dumps(line, ensure_ascii=False) for line in lines)
