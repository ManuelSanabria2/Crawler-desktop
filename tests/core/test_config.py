"""Tests del cargador de configuración: carga, valores TODO, perfiles y validación."""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from geass.core import config as config_module
from geass.core.config import (
    ARM_PROFILE_ENV,
    load_arm_config,
    load_board_config,
    load_config,
    load_crawler_config,
    load_safety_config,
    resolve_arm_profile,
)
from geass.core.errors import ConfigError, ConfigIncompleteError, ConfigValidationError
from geass.core.paths import config_dir
from geass.core.schema import TODO_MARKER, ConfigModel, is_todo_field
from geass.core.schemas.arm import ArmConfig
from geass.core.schemas.board import BoardConfig
from geass.core.schemas.crawler import CrawlerConfig
from geass.core.schemas.safety import SafetyConfig


@pytest.fixture(autouse=True)
def _no_profile_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ARM_PROFILE_ENV, raising=False)


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Copia de config/ que el test puede modificar."""
    target = tmp_path / "config"
    shutil.copytree(config_dir(), target)
    return target


def _edit_yaml(path: Path, edit: object) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)  # type: ignore[operator]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


# Carga


def test_load_config_loads_the_four_files() -> None:
    cfg = load_config()
    assert cfg.arm_profile == "real"
    assert isinstance(cfg.arm, ArmConfig)
    assert isinstance(cfg.board, BoardConfig)
    assert isinstance(cfg.safety, SafetyConfig)
    assert isinstance(cfg.crawler, CrawlerConfig)


def test_known_values_are_readable() -> None:
    arm = load_arm_config()
    assert arm.bridge.baudrate == 115200
    assert arm.bridge.watchdog_ms == 1000
    assert arm.neural_ik.model.hidden_layers == [256, 256, 256, 256]
    assert arm.actuators.joints[0].model == "NEMA17"
    assert load_crawler_config().passive.stability_window_s == pytest.approx(0.4)
    assert load_board_config().perception.classifier.num_classes == 13
    assert "eliminar" in load_safety_config().crawler.blocklist_patterns


def test_models_are_immutable() -> None:
    arm = load_arm_config("test")
    with pytest.raises(Exception, match="frozen"):
        arm.bridge.baudrate = 9600  # type: ignore[misc]


# Valores TODO


def test_todo_does_not_break_loading_but_access_raises() -> None:
    arm = load_arm_config()
    joint = arm.kinematics.joints[2]
    with pytest.raises(ConfigIncompleteError) as excinfo:
        _ = joint.dh.a_m
    error = excinfo.value
    assert error.key == "kinematics.joints[2].dh.a_m"
    assert "longitud del eslabón (m)" in error.meaning
    assert error.source == "arm.yaml"
    assert "kinematics.joints[2].dh.a_m" in str(error)
    assert "arm.yaml" in str(error)


def test_todo_error_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        _ = load_board_config().board.square_size_m


@pytest.mark.parametrize(
    ("loader", "getter", "key"),
    [
        (load_board_config, lambda c: c.stockfish.executable_path, "stockfish.executable_path"),
        (load_board_config, lambda c: c.pieces.king.height_m, "pieces.king.height_m"),
        (load_safety_config, lambda c: c.arm.workspace_box.min_m, "arm.workspace_box.min_m"),
        (
            load_safety_config,
            lambda c: c.crawler.authorization.allowed_windows,
            "crawler.authorization.allowed_windows",
        ),
        (load_crawler_config, lambda c: c.apps.priority, "apps.priority"),
    ],
)
def test_todo_in_every_file_reports_its_key(loader, getter, key: str) -> None:  # noqa: ANN001
    with pytest.raises(ConfigIncompleteError) as excinfo:
        getter(loader())
    assert excinfo.value.key == key
    assert excinfo.value.meaning


def test_is_pending_and_known_do_not_raise() -> None:
    limits = load_arm_config().kinematics.joints[0].limits
    assert limits.is_pending("min_rad")
    assert limits.known("min_rad") is None
    test_limits = load_arm_config("test").kinematics.joints[0].limits
    assert not test_limits.is_pending("min_rad")
    assert test_limits.known("min_rad") == pytest.approx(-2.9)


def test_pending_keys_lists_every_todo() -> None:
    cfg = load_config()
    pending = cfg.pending_keys()
    assert "kinematics.joints[0].dh.a_m" in pending["arm.yaml"]
    assert "bridge.serial_port" in pending["arm.yaml"]
    assert "board.square_size_m" in pending["board.yaml"]
    assert "arm.min_height_above_board_m" in pending["safety.yaml"]
    assert "system.windows_edition" in pending["crawler.yaml"]


def test_pending_keys_match_the_todos_in_the_yaml() -> None:
    def count(node: object) -> int:
        if isinstance(node, dict):
            return sum(count(value) for value in node.values())
        if isinstance(node, list):
            return sum(count(value) for value in node)
        return int(node == TODO_MARKER)

    cfg = load_config()
    for file_name, keys in cfg.pending_keys().items():
        raw = yaml.safe_load((config_dir() / file_name).read_text(encoding="utf-8"))
        assert len(keys) == count(raw), file_name


def test_dump_writes_todo_back() -> None:
    dumped = load_arm_config().model_dump()
    assert dumped["kinematics"]["joints"][0]["dh"]["a_m"] == TODO_MARKER
    assert dumped["bridge"]["baudrate"] == 115200


def _walk_models(model: type[BaseModel], seen: set[type]) -> None:
    if model in seen:
        return
    seen.add(model)
    for info in model.model_fields.values():
        _walk_annotation(info.annotation, seen)


def _walk_annotation(annotation: object, seen: set[type]) -> None:
    if isinstance(annotation, type) and issubclass(annotation, ConfigModel):
        _walk_models(annotation, seen)
    for arg in getattr(annotation, "__args__", ()):
        _walk_annotation(arg, seen)


def test_every_todo_field_explains_what_it_means() -> None:
    seen: set[type] = set()
    for root in (ArmConfig, BoardConfig, SafetyConfig, CrawlerConfig):
        _walk_models(root, seen)
    assert len(seen) > 40
    todo_fields = 0
    for model in seen:
        for name, info in model.model_fields.items():
            if is_todo_field(info):
                todo_fields += 1
                assert info.description, f"{model.__name__}.{name} no tiene descripción"
    assert todo_fields > 80


# Perfiles


def test_default_profile_is_real() -> None:
    assert resolve_arm_profile() == "real"
    assert load_arm_config().fictitious is False


def test_profile_by_argument() -> None:
    arm = load_arm_config("test")
    assert arm.fictitious is True
    assert arm.pending_keys() == []
    assert arm.kinematics.joints[1].dh.a_m == pytest.approx(0.20)


def test_profile_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ARM_PROFILE_ENV, "test")
    assert load_arm_config().fictitious is True
    assert load_config().arm_profile == "test"


def test_argument_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ARM_PROFILE_ENV, "test")
    assert load_arm_config("real").fictitious is False


def test_unknown_profile_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="Perfil del brazo desconocido"):
        load_arm_config("produccion")
    monkeypatch.setenv(ARM_PROFILE_ENV, "otro")
    with pytest.raises(ConfigError, match="otro"):
        load_arm_config()


def test_fictitious_file_cannot_be_the_real_profile(tmp_config: Path) -> None:
    shutil.copyfile(tmp_config / "arm.test.yaml", tmp_config / "arm.yaml")
    with pytest.raises(ConfigValidationError, match="ficticio"):
        load_arm_config("real", config_dir=tmp_config)


def test_test_profile_must_be_marked_fictitious(tmp_config: Path) -> None:
    _edit_yaml(tmp_config / "arm.test.yaml", lambda data: data.update(fictitious=False))
    with pytest.raises(ConfigValidationError, match="fictitious: true"):
        load_arm_config("test", config_dir=tmp_config)


# Validación


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No existe"):
        load_board_config(config_dir=tmp_path)


def test_unknown_key_is_rejected(tmp_config: Path) -> None:
    _edit_yaml(tmp_config / "arm.test.yaml", lambda data: data["bridge"].update(baud=9600))
    with pytest.raises(ConfigValidationError, match=r"bridge\.baud"):
        load_arm_config("test", config_dir=tmp_config)


def test_wrong_type_is_rejected_with_its_location(tmp_config: Path) -> None:
    def edit(data: dict) -> None:
        data["kinematics"]["joints"][3]["dh"]["a_m"] = "largo"

    _edit_yaml(tmp_config / "arm.test.yaml", edit)
    with pytest.raises(ConfigValidationError, match=r"kinematics\.joints\[3\]\.dh\.a_m"):
        load_arm_config("test", config_dir=tmp_config)


def test_inverted_joint_limits_are_rejected(tmp_config: Path) -> None:
    def edit(data: dict) -> None:
        data["kinematics"]["joints"][0]["limits"] = {"min_rad": 1.0, "max_rad": -1.0}

    _edit_yaml(tmp_config / "arm.test.yaml", edit)
    with pytest.raises(ConfigValidationError, match="min_rad debe ser menor"):
        load_arm_config("test", config_dir=tmp_config)


def test_wrong_joint_count_is_rejected(tmp_config: Path) -> None:
    _edit_yaml(tmp_config / "arm.test.yaml", lambda data: data["kinematics"]["joints"].pop())
    with pytest.raises(ConfigValidationError, match="6 articulaciones"):
        load_arm_config("test", config_dir=tmp_config)


def test_boolean_in_pattern_list_is_rejected(tmp_config: Path) -> None:
    # Un "yes" sin comillas en YAML se lee como booleano: debe fallar, no bloquear "True".
    def edit(data: dict) -> None:
        data["crawler"]["blocklist_patterns"].append(True)

    _edit_yaml(tmp_config / "safety.yaml", edit)
    with pytest.raises(ConfigValidationError, match="blocklist_patterns"):
        load_safety_config(config_dir=tmp_config)


@pytest.mark.parametrize(
    "key_path",
    [
        ("crawler", "active_exploration", "require_sandbox"),
        ("crawler", "active_exploration", "never_type_text"),
        ("crawler", "authorization", "require_explicit_order"),
        ("crawler", "stop_if_window_unconfirmed"),
    ],
)
def test_non_negotiable_rules_cannot_be_disabled(
    tmp_config: Path, key_path: tuple[str, ...]
) -> None:
    def edit(data: dict) -> None:
        node = data
        for key in key_path[:-1]:
            node = node[key]
        node[key_path[-1]] = False

    _edit_yaml(tmp_config / "safety.yaml", edit)
    with pytest.raises(ConfigValidationError, match=key_path[-1]):
        load_safety_config(config_dir=tmp_config)


def test_analytic_ik_must_stay_as_last_resort(tmp_config: Path) -> None:
    def edit(data: dict) -> None:
        data["arm"]["solution_selector"]["order"] = ["neural_ik"]

    _edit_yaml(tmp_config / "safety.yaml", edit)
    with pytest.raises(ConfigValidationError, match="último recurso"):
        load_safety_config(config_dir=tmp_config)


def test_invalid_yaml_is_reported(tmp_config: Path) -> None:
    (tmp_config / "board.yaml").write_text("board: [sin cerrar", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="no es YAML válido"):
        load_board_config(config_dir=tmp_config)


def test_default_config_dir_follows_project_root(
    tmp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module.paths, "config_dir", lambda: tmp_config)
    _edit_yaml(tmp_config / "board.yaml", lambda data: data["stockfish"].update(skill_level=7))
    assert load_board_config().stockfish.skill_level == 7
