"""Tests del recorrido del árbol y de dump_window con un backend falso. No necesitan Windows."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from geass.core.errors import ConfigIncompleteError
from geass.crawler.uia.dump import (
    ExcludedAppError,
    UiaError,
    WindowNotFoundError,
    dump_window,
    limits_from_config,
)
from geass.crawler.uia.reader import DumpLimits, NodeProps, UiaNodeError, walk_tree
from geass.crawler.uia.schema import Element, Rect, WindowDump

LIMITS = DumpLimits(max_depth=10, max_nodes=1000, timeout_s=5.0)


@dataclass
class FakeNode:
    control_type: str
    name: str = ""
    automation_id: str = ""
    kids: list["FakeNode"] = field(default_factory=list)
    is_password: bool = False
    patterns: tuple[str, ...] = ()
    fail_read: bool = False
    fail_children: bool = False
    read_calls: int = 0

    def read(self) -> NodeProps:
        self.read_calls += 1
        if self.fail_read:
            raise UiaNodeError("el elemento desapareció")
        return NodeProps(
            control_type=self.control_type,
            name=self.name,
            automation_id=self.automation_id,
            class_name="FakeClass",
            rect=Rect(left=0, top=0, right=100, bottom=20),
            enabled=True,
            offscreen=False,
            is_password=self.is_password,
            patterns=self.patterns,
        )

    def children(self) -> Sequence["FakeNode"]:
        if self.fail_children:
            raise UiaNodeError("error COM al pedir los hijos")
        return self.kids


def sample_tree() -> FakeNode:
    return FakeNode(
        "Window",
        name="App",
        kids=[
            FakeNode(
                "MenuBar",
                kids=[
                    FakeNode("MenuItem", name="Archivo", patterns=("ExpandCollapse",)),
                    FakeNode("MenuItem", name="Editar", patterns=("ExpandCollapse",)),
                ],
            ),
            FakeNode("Edit", name="Usuario", automation_id="user", patterns=("Value",)),
            FakeNode("Edit", name="Clave", automation_id="pass", is_password=True),
            FakeNode("Button", name="Entrar", automation_id="ok", patterns=("Invoke",)),
        ],
    )


def chain(length: int) -> FakeNode:
    node = FakeNode("Pane", name=f"nivel{length - 1}")
    for level in range(length - 2, -1, -1):
        node = FakeNode("Pane", name=f"nivel{level}", kids=[node])
    return node


# Recorrido


def test_complete_walk_builds_the_tree() -> None:
    result = walk_tree(sample_tree(), LIMITS)
    assert not result.partial
    assert result.partial_reasons == []
    assert result.error_count == 0
    assert result.node_count == 7
    assert result.max_depth_reached == 2
    root = result.root
    assert root.control_type == "Window"
    assert [child.control_type for child in root.children] == ["MenuBar", "Edit", "Edit", "Button"]
    assert len(list(root.iter_tree())) == 7


def test_paths_come_from_the_root_and_are_unique() -> None:
    root = walk_tree(sample_tree(), LIMITS).root
    paths = [element.path for element in root.iter_tree()]
    assert paths == [
        "Window[0]",
        "Window[0]/MenuBar[0]",
        "Window[0]/MenuBar[0]/MenuItem[0]",
        "Window[0]/MenuBar[0]/MenuItem[1]",
        "Window[0]/Edit[0]",
        "Window[0]/Edit[1]",
        "Window[0]/Button[0]",
    ]
    assert len(set(paths)) == len(paths)


def test_properties_are_copied() -> None:
    root = walk_tree(sample_tree(), LIMITS).root
    (button,) = root.find_all(automation_id="ok")
    assert button.name == "Entrar"
    assert button.control_type == "Button"
    assert button.class_name == "FakeClass"
    assert button.rect == Rect(left=0, top=0, right=100, bottom=20)
    assert button.rect.width == 100 and button.rect.height == 20
    assert button.enabled and not button.offscreen
    assert button.patterns == ["Invoke"]
    assert len(root.find_all(control_type="MenuItem")) == 2
    assert root.find_all(control_type="Edit", name="Clave")[0].automation_id == "pass"


def test_max_depth_marks_partial() -> None:
    result = walk_tree(chain(6), DumpLimits(max_depth=3, max_nodes=1000, timeout_s=5.0))
    assert result.partial
    assert result.partial_reasons == ["max_depth"]
    assert result.node_count == 4
    assert result.max_depth_reached == 3


def test_tree_exactly_at_max_depth_is_complete() -> None:
    result = walk_tree(chain(4), DumpLimits(max_depth=3, max_nodes=1000, timeout_s=5.0))
    assert not result.partial
    assert result.node_count == 4


def test_max_nodes_marks_partial() -> None:
    wide = FakeNode("Window", kids=[FakeNode("Button", name=str(i)) for i in range(50)])
    result = walk_tree(wide, DumpLimits(max_depth=10, max_nodes=10, timeout_s=5.0))
    assert result.partial_reasons == ["max_nodes"]
    assert result.node_count == 10
    assert len(list(result.root.iter_tree())) == 10


def test_tree_exactly_at_max_nodes_is_complete() -> None:
    result = walk_tree(sample_tree(), DumpLimits(max_depth=10, max_nodes=7, timeout_s=5.0))
    assert not result.partial
    assert result.node_count == 7


def test_timeout_marks_partial() -> None:
    ticks = iter(range(1000))
    result = walk_tree(
        chain(20),
        DumpLimits(max_depth=100, max_nodes=1000, timeout_s=5.0),
        clock=lambda: float(next(ticks)),
    )
    assert result.partial_reasons == ["timeout"]
    assert 1 < result.node_count < 20
    assert result.elapsed_s > 5.0


def test_element_that_disappears_is_skipped_and_marks_partial() -> None:
    tree = sample_tree()
    tree.kids[1].fail_read = True
    result = walk_tree(tree, LIMITS)
    assert result.partial_reasons == ["element_error"]
    assert result.error_count == 1
    assert result.node_count == 6
    assert result.root.find_all(automation_id="user") == []
    assert len(result.root.find_all(automation_id="ok")) == 1


def test_com_error_on_children_keeps_the_rest() -> None:
    tree = sample_tree()
    tree.kids[0].fail_children = True
    result = walk_tree(tree, LIMITS)
    assert result.partial_reasons == ["element_error"]
    assert result.error_count == 1
    (menu_bar,) = result.root.find_all(control_type="MenuBar")
    assert menu_bar.children == []
    assert len(result.root.find_all(control_type="Button")) == 1


def test_several_reasons_are_all_reported() -> None:
    tree = chain(6)
    tree.kids.append(FakeNode("Button", fail_read=True))
    result = walk_tree(tree, DumpLimits(max_depth=3, max_nodes=1000, timeout_s=5.0))
    assert set(result.partial_reasons) == {"max_depth", "element_error"}


def test_unreadable_root_raises() -> None:
    with pytest.raises(UiaNodeError):
        walk_tree(FakeNode("Window", fail_read=True), LIMITS)


def test_deep_tree_does_not_hit_recursion_limit() -> None:
    result = walk_tree(chain(3000), DumpLimits(max_depth=5000, max_nodes=5000, timeout_s=60.0))
    assert result.node_count == 3000
    assert not result.partial


# Privacidad


def test_schema_has_no_field_for_text_values() -> None:
    forbidden = {"value", "text", "password", "content", "legacy_value"}
    assert not forbidden & set(Element.model_fields)
    assert not forbidden & set(NodeProps.__dataclass_fields__)


def test_password_field_is_recorded_only_as_existing() -> None:
    root = walk_tree(sample_tree(), LIMITS).root
    (password,) = [element for element in root.iter_tree() if element.is_password]
    assert password.control_type == "Edit"
    assert password.automation_id == "pass"
    dumped = json.loads(root.model_dump_json())
    assert "value" not in json.dumps(dumped)


def test_real_backend_never_requests_value_properties() -> None:
    source = Path(__file__).resolve().parents[3] / "src/geass/crawler/uia/backend_win.py"
    code = source.read_text(encoding="utf-8")
    for forbidden in (
        "CurrentValue",
        "CachedValue",
        "ValueValue",
        "GetCurrentPattern",
        "GetCachedPattern",
        "DocumentRange",
        "GetText",
        "LegacyIAccessibleValue",
        "iface_value",
        "window_text",
    ):
        assert forbidden not in code, forbidden


# dump_window con backend falso


@dataclass
class FakeInfo:
    handle: int
    title: str = "Ventana de prueba"
    class_name: str = "FakeWindow"
    process_id: int = 4242
    process_name: str = "Fake.exe"


@dataclass
class FakeBackend:
    tree: FakeNode = field(default_factory=sample_tree)
    foreground: int = 100
    process_name: str = "Fake.exe"
    valid_handles: tuple[int, ...] = (100, 200)
    root_requests: int = 0

    def foreground_handle(self) -> int:
        return self.foreground

    def is_window(self, handle: int) -> bool:
        return handle in self.valid_handles

    def window_info(self, handle: int) -> FakeInfo:
        return FakeInfo(handle=handle, process_name=self.process_name)

    def root_node(self, handle: int) -> FakeNode:
        self.root_requests += 1
        return self.tree


def test_dump_active_window() -> None:
    result = dump_window(limits=LIMITS, excluded_apps=[], backend=FakeBackend())
    assert isinstance(result, WindowDump)
    assert result.handle == 100
    assert result.process_name == "Fake.exe"
    assert result.process_id == 4242
    assert result.title == "Ventana de prueba"
    assert result.node_count == 7
    assert not result.partial
    assert result.captured_at.tzinfo is not None
    assert result.elapsed_s >= 0


def test_dump_by_handle() -> None:
    result = dump_window(200, limits=LIMITS, excluded_apps=[], backend=FakeBackend())
    assert result.handle == 200


def test_dump_marks_partial() -> None:
    limits = DumpLimits(max_depth=1, max_nodes=1000, timeout_s=5.0)
    result = dump_window(limits=limits, excluded_apps=[], backend=FakeBackend())
    assert result.partial is True
    assert result.partial_reasons == ["max_depth"]
    assert result.node_count == 5


def test_dump_round_trips_through_json() -> None:
    result = dump_window(limits=LIMITS, excluded_apps=[], backend=FakeBackend())
    assert WindowDump.model_validate_json(result.model_dump_json()) == result


def test_no_foreground_window() -> None:
    with pytest.raises(WindowNotFoundError, match="primer plano"):
        dump_window(limits=LIMITS, excluded_apps=[], backend=FakeBackend(foreground=0))


def test_invalid_handle() -> None:
    with pytest.raises(WindowNotFoundError, match="999"):
        dump_window(999, limits=LIMITS, excluded_apps=[], backend=FakeBackend())


def test_excluded_app_is_never_read() -> None:
    backend = FakeBackend(process_name="KeePass.exe")
    with pytest.raises(ExcludedAppError, match="KeePass.exe"):
        dump_window(limits=LIMITS, excluded_apps=["keepass.exe"], backend=backend)
    assert backend.root_requests == 0
    assert backend.tree.read_calls == 0


def test_unknown_process_is_not_read_when_there_are_exclusions() -> None:
    backend = FakeBackend(process_name="")
    with pytest.raises(ExcludedAppError, match="precaución"):
        dump_window(limits=LIMITS, excluded_apps=["keepass.exe"], backend=backend)
    assert backend.root_requests == 0


def test_unreadable_window_raises_uia_error() -> None:
    backend = FakeBackend(tree=FakeNode("Window", fail_read=True))
    with pytest.raises(UiaError, match="No se pudo leer"):
        dump_window(limits=LIMITS, excluded_apps=[], backend=backend)


def test_pending_exclusion_list_blocks_the_dump(tmp_path: Path) -> None:
    # Si apps.excluded está como TODO, sin lista de exclusión no se lee nada.
    import yaml

    from geass.core.config import load_crawler_config
    from geass.core.paths import config_dir

    data = yaml.safe_load((config_dir() / "crawler.yaml").read_text(encoding="utf-8"))
    data["apps"]["excluded"] = "TODO"
    (tmp_path / "crawler.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    config = load_crawler_config(config_dir=tmp_path)

    backend = FakeBackend()
    with pytest.raises(ConfigIncompleteError) as excinfo:
        dump_window(limits=LIMITS, backend=backend, config=config)
    assert excinfo.value.key == "apps.excluded"
    assert backend.root_requests == 0


def test_exclusion_list_from_crawler_yaml_is_applied() -> None:
    from geass.core.config import load_crawler_config

    excluded = load_crawler_config().apps.excluded
    assert {"keepass.exe", "chrome.exe", "whatsapp.exe"} <= set(excluded)
    backend = FakeBackend(process_name="Chrome.exe")
    with pytest.raises(ExcludedAppError, match="Chrome.exe"):
        dump_window(limits=LIMITS, backend=backend)
    assert backend.root_requests == 0
    assert dump_window(limits=LIMITS, backend=FakeBackend()).node_count == 7


def test_limits_come_from_crawler_yaml() -> None:
    from geass.core.config import load_crawler_config

    config = load_crawler_config()
    limits = limits_from_config(config)
    assert limits == DumpLimits(
        max_depth=config.uia.max_depth,
        max_nodes=config.uia.max_nodes,
        timeout_s=config.uia.dump_timeout_s,
    )
    result = dump_window(excluded_apps=[], backend=FakeBackend(), config=config)
    assert result.node_count == 7


# CLI


def test_cli_dump_writes_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import geass.crawler.uia as uia_package
    from geass.crawler.__main__ import app

    calls: list[int | None] = []

    def fake_dump(handle: int | None = None) -> WindowDump:
        calls.append(handle)
        return dump_window(handle, limits=LIMITS, excluded_apps=[], backend=FakeBackend())

    monkeypatch.setattr(uia_package, "dump_window", fake_dump)
    out = tmp_path / "volcado.json"
    result = CliRunner().invoke(app, ["dump", "--active", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert calls == [None]
    loaded = WindowDump.model_validate_json(out.read_text(encoding="utf-8"))
    assert loaded.node_count == 7

    result = CliRunner().invoke(app, ["dump", "--handle", "200"])
    assert result.exit_code == 0
    assert calls == [None, 200]
    assert '"control_type": "Window"' in result.output


def test_cli_dump_requires_a_target() -> None:
    from geass.crawler.__main__ import app

    assert CliRunner().invoke(app, ["dump"]).exit_code == 2
    assert CliRunner().invoke(app, ["dump", "--active", "--handle", "5"]).exit_code == 2


def test_cli_dump_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import geass.crawler.uia as uia_package
    from geass.crawler.__main__ import app

    def failing(handle: int | None = None) -> WindowDump:
        raise ExcludedAppError("La app KeePass.exe está excluida en crawler.yaml.")

    monkeypatch.setattr(uia_package, "dump_window", failing)
    result = CliRunner().invoke(app, ["dump", "--active"])
    assert result.exit_code == 1
    assert "excluida" in result.output
