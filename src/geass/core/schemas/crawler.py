"""Esquema de config/crawler.yaml."""

import re
from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveFloat, PositiveInt, field_validator

from geass.core.schema import ConfigModel, Todo, todo

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
FuzzyScore = Annotated[int, Field(ge=0, le=100)]


class System(ConfigModel):
    windows_edition: Todo[Literal["home", "pro", "enterprise"]] = todo(
        "edición de Windows de este PC: home, pro o enterprise"
    )


class Apps(ConfigModel):
    excluded: Todo[list[str]] = todo(
        "apps que el crawler nunca registra: lista de ejecutables, por ejemplo keepass.exe"
    )
    priority: Todo[list[str]] = todo("apps a mapear primero: lista de ejecutables")


class Uia(ConfigModel):
    backend: Literal["uia"]
    tree_view: Literal["control", "raw"]
    max_depth: PositiveInt
    max_nodes: PositiveInt
    dump_timeout_s: PositiveFloat
    target_dump_time_s: PositiveFloat


class TextPattern(ConfigModel):
    name: str
    pattern: str
    replacement: str

    @field_validator("pattern")
    @classmethod
    def _check_regex(cls, value: str) -> str:
        return _compiled(value)


class Signature(ConfigModel):
    ignored_control_types: list[str]
    volatile_container_types: list[str]
    prune_empty_control_types: list[str]
    count_buckets: Annotated[list[NonNegativeInt], Field(min_length=1)]
    variable_text_patterns: list[TextPattern]
    title_patterns: dict[str, str]
    default_title_pattern: str
    target_repeatability: Fraction

    @field_validator("count_buckets")
    @classmethod
    def _check_buckets(cls, value: list[int]) -> list[int]:
        if value[0] != 0 or any(a >= b for a, b in zip(value, value[1:], strict=False)):
            raise ValueError("count_buckets debe empezar en 0 y ser estrictamente creciente")
        return value

    @field_validator("default_title_pattern")
    @classmethod
    def _check_default_title(cls, value: str) -> str:
        return _title_regex(value)

    @field_validator("title_patterns")
    @classmethod
    def _check_titles(cls, value: dict[str, str]) -> dict[str, str]:
        return {key.lower(): _title_regex(pattern) for key, pattern in value.items()}


def _compiled(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"expresión regular inválida: {error}") from error
    return pattern


def _title_regex(pattern: str) -> str:
    _compiled(pattern)
    if "stable" not in re.compile(pattern).groupindex:
        raise ValueError('el patrón de título debe definir el grupo "stable"')
    return pattern


class Retention(ConfigModel):
    max_age_days: PositiveInt
    max_total_mb: PositiveInt


class Store(ConfigModel):
    db_path: str
    screens_dir: str
    capture_screens: bool
    phash_max_distance: NonNegativeInt
    redact_control_types: list[str]
    retention: Retention


class Passive(ConfigModel):
    poll_interval_s: PositiveFloat
    use_uia_events: bool
    listen_mouse: bool
    stability_window_s: PositiveFloat
    stability_timeout_s: PositiveFloat
    stability_poll_s: PositiveFloat
    resync_interval_s: PositiveFloat
    resync_max_interval_s: PositiveFloat
    max_capture_delay_s: PositiveFloat
    capture_shortcuts: bool
    pause_hotkey: str
    cpu_budget_fraction: Fraction
    status_path: str
    status_interval_s: PositiveFloat


class Graph(ConfigModel):
    fuzzy_match_threshold: FuzzyScore
    coordinate_click_cost: PositiveFloat


class Export(ConfigModel):
    obsidian_vault_path: Todo[str] = todo("ruta de la bóveda de Obsidian en este PC")
    notes_folder: str
    managed_block_start: str
    managed_block_end: str
    top_actions_per_app: PositiveInt
    top_routes_per_app: PositiveInt


class Launcher(ConfigModel):
    start_menu_dirs: list[str]
    include_store_apps: bool
    fuzzy_match_threshold: FuzzyScore
    direct_actions: list[str]
    cache_path: str
    cache_max_age_s: PositiveFloat
    start_apps_timeout_s: PositiveFloat
    ambiguity_margin: FuzzyScore
    max_options: PositiveInt


class ExecutorTargets(ConfigModel):
    direct_action_success: Fraction
    navigation_success: Fraction


ResolutionStep = Literal[
    "direct_action",
    "current_window_element",
    "graph_route",
    "visual_detector",
    "coordinate_click",
]


class Executor(ConfigModel):
    resolution_order: Annotated[list[ResolutionStep], Field(min_length=1)]
    verify_transition: bool
    verify_timeout_s: PositiveFloat
    verify_poll_s: PositiveFloat
    launch_timeout_s: PositiveFloat
    max_retries: NonNegativeInt
    benchmark_path: str
    targets: ExecutorTargets


class Active(ConfigModel):
    environment: Todo[Literal["sandbox", "vm"]] = todo(
        "entorno aislado del crawler activo: sandbox (Windows Pro o Enterprise) o vm"
    )
    wsb_path: str
    output_dir: str
    max_depth: PositiveInt
    max_actions_per_app: PositiveInt
    max_time_per_app_s: PositiveFloat
    seed: int


class Vision(ConfigModel):
    dataset_dir: str
    format: Literal["coco"]
    min_area_px: PositiveInt
    classes: Annotated[list[str], Field(min_length=1)]
    split_by_app: bool
    test_apps: Todo[list[str]] = todo(
        "apps reservadas para prueba del detector, fuera del entrenamiento: lista de ejecutables"
    )
    detector: str
    score_threshold: Fraction
    target_map50: Fraction
    model_dir: str
    seed: int


class CrawlerConfig(ConfigModel):
    """Configuración del crawler de escritorio."""

    schema_version: Literal[1]
    system: System
    apps: Apps
    uia: Uia
    signature: Signature
    store: Store
    passive: Passive
    graph: Graph
    export: Export
    launcher: Launcher
    executor: Executor
    active: Active
    vision: Vision
