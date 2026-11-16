"""Esquema de config/safety.yaml.

Las reglas no negociables de CLAUDE.md se modelan como ``Literal[True]``: el YAML puede
declararlas, pero no desactivarlas.
"""

from typing import Annotated, Literal

from pydantic import Field, PositiveFloat, model_validator

from geass.core.schema import ConfigModel, Todo, todo

Vec3 = tuple[float, float, float]
SpeedFactor = Annotated[float, Field(gt=0.0, le=1.0)]
PatternList = Annotated[list[str], Field(min_length=1)]


class FkValidation(ConfigModel):
    position_tolerance_m: PositiveFloat
    orientation_tolerance_rad: PositiveFloat


class WorkspaceBox(ConfigModel):
    min_m: Todo[Vec3] = todo(
        "esquina mínima [x, y, z] de la caja de trabajo permitida, respecto a la base (m)"
    )
    max_m: Todo[Vec3] = todo(
        "esquina máxima [x, y, z] de la caja de trabajo permitida, respecto a la base (m)"
    )

    @model_validator(mode="after")
    def _check_order(self) -> "WorkspaceBox":
        low, high = self.known("min_m"), self.known("max_m")
        known = low is not None and high is not None
        if known and any(a >= b for a, b in zip(low, high, strict=True)):
            raise ValueError("cada componente de min_m debe ser menor que la de max_m")
        return self


class ArmSpeed(ConfigModel):
    reduced_speed_factor: SpeedFactor
    use_reduced_speed: bool
    sim_speed_factor: SpeedFactor


class CollisionCheck(ConfigModel):
    enabled: bool


class SolutionSelector(ConfigModel):
    order: Annotated[list[Literal["neural_ik", "analytic_ik"]], Field(min_length=1)]
    on_failure: Literal["abort"]

    @model_validator(mode="after")
    def _check_fallback(self) -> "SolutionSelector":
        if self.order[-1] != "analytic_ik":
            raise ValueError("la IK analítica debe ser el último recurso del selector")
        if len(set(self.order)) != len(self.order):
            raise ValueError("order no puede repetir soluciones")
        return self


class ArmSafety(ConfigModel):
    fk_validation: FkValidation
    workspace_box: WorkspaceBox
    min_height_above_board_m: Todo[PositiveFloat] = todo(
        "altura mínima sobre el tablero fuera de las fases de agarre y soltado (m)"
    )
    speed: ArmSpeed
    collision_check: CollisionCheck
    solution_selector: SolutionSelector


class AuthorizationWindow(ConfigModel):
    executable: str
    title_pattern: str


class Authorization(ConfigModel):
    button_patterns: PatternList
    allowed_windows: Todo[list[AuthorizationWindow]] = todo(
        "ventanas donde se permiten botones de autorización bajo orden explícita: "
        "lista de {executable, title_pattern}"
    )
    require_explicit_order: Literal[True]


class SensitiveFields(ConfigModel):
    name_patterns: PatternList


class ActiveExploration(ConfigModel):
    require_sandbox: Literal[True]
    allowed_control_types: PatternList
    allowed_button_names: list[str]
    dialog_dismiss: PatternList
    never_type_text: Literal[True]


class AuditLog(ConfigModel):
    path: str


class CrawlerSafety(ConfigModel):
    blocklist_patterns: PatternList
    confirmation_dialog_accept_patterns: PatternList
    require_confirmation_patterns: PatternList
    authorization: Authorization
    never_automate: PatternList
    sensitive_fields: SensitiveFields
    active_exploration: ActiveExploration
    stop_if_window_unconfirmed: Literal[True]
    audit_log: AuditLog


class SafetyConfig(ConfigModel):
    """Umbrales de seguridad del brazo y listas de bloqueo del crawler."""

    schema_version: Literal[1]
    arm: ArmSafety
    crawler: CrawlerSafety
