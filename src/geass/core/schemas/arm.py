"""Esquema de config/arm.yaml y config/arm.test.yaml."""

from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveFloat, PositiveInt, model_validator

from geass.core.schema import ConfigModel, Todo, todo

Vec3 = tuple[float, float, float]
JointVector = tuple[float, float, float, float, float, float]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
PcaChannel = Annotated[int, Field(ge=0, le=15)]


class ArmInfo(ConfigModel):
    name: str
    dof: Literal[6]


class DHParams(ConfigModel):
    a_m: Todo[float] = todo("parámetro DH a: longitud del eslabón (m)")
    alpha_rad: Todo[float] = todo("parámetro DH alpha: torsión del eslabón (rad)")
    d_m: Todo[float] = todo("parámetro DH d: desplazamiento a lo largo del eje (m)")
    theta_offset_rad: Todo[float] = todo(
        "offset del ángulo articular respecto al cero mecánico (rad)"
    )


class JointLimits(ConfigModel):
    min_rad: Todo[float] = todo("límite inferior del rango de la articulación (rad)")
    max_rad: Todo[float] = todo("límite superior del rango de la articulación (rad)")

    @model_validator(mode="after")
    def _check_order(self) -> "JointLimits":
        low, high = self.known("min_rad"), self.known("max_rad")
        if low is not None and high is not None and low >= high:
            raise ValueError("min_rad debe ser menor que max_rad")
        return self


class Joint(ConfigModel):
    name: str
    dh: DHParams
    limits: JointLimits
    max_velocity_rad_s: Todo[PositiveFloat] = todo(
        "velocidad máxima que soporta el actuador de la articulación (rad/s)"
    )
    max_acceleration_rad_s2: Todo[PositiveFloat] = todo(
        "aceleración máxima que soporta el actuador de la articulación (rad/s^2)"
    )


class ToolFrame(ConfigModel):
    position_m: Todo[Vec3] = todo("posición [x, y, z] del punto de agarre respecto a la brida (m)")
    rpy_rad: Todo[Vec3] = todo(
        "orientación [roll, pitch, yaw] del punto de agarre respecto a la brida (rad)"
    )


class IkBranch(ConfigModel):
    shoulder: Literal["front", "back"]
    elbow: Literal["up", "down"]
    wrist: Literal["no_flip", "flip"]


class IkConfig(ConfigModel):
    branch: IkBranch


class Poses(ConfigModel):
    home_rad: Todo[JointVector] = todo("pose articular a la que lleva HOME: 6 ángulos (rad)")
    wait_rad: Todo[JointVector] = todo("pose articular de espera entre jugadas: 6 ángulos (rad)")


class Kinematics(ConfigModel):
    convention: Todo[str] = todo(
        "convención de los parámetros cinemáticos: dh_standard, dh_modified u otra equivalente"
    )
    joints: list[Joint]
    tool: ToolFrame
    ik: IkConfig
    poses: Poses


class StepperActuator(ConfigModel):
    joint: str
    type: Literal["stepper"]
    model: str


class ServoActuator(ConfigModel):
    joint: str
    type: Literal["servo"]
    model: Todo[str] = todo("modelo del servo que mueve el eje: MG996 o MG90")
    pca9685_channel: Todo[PcaChannel] = todo("canal del PCA9685 al que va el servo (0 a 15)")
    pulse_min_us: Todo[PositiveInt] = todo(
        "ancho de pulso en el extremo inferior del recorrido del servo (microsegundos)"
    )
    pulse_max_us: Todo[PositiveInt] = todo(
        "ancho de pulso en el extremo superior del recorrido del servo (microsegundos)"
    )
    range_rad: Todo[PositiveFloat] = todo(
        "recorrido total del servo entre el pulso mínimo y el máximo (rad)"
    )
    zero_offset_rad: Todo[float] = todo(
        "ángulo del servo que corresponde al cero de la articulación (rad)"
    )
    inverted: Todo[bool] = todo(
        "true si el sentido del servo es contrario al de la articulación (true o false)"
    )

    @model_validator(mode="after")
    def _check_pulses(self) -> "ServoActuator":
        low, high = self.known("pulse_min_us"), self.known("pulse_max_us")
        if low is not None and high is not None and low >= high:
            raise ValueError("pulse_min_us debe ser menor que pulse_max_us")
        return self


JointActuator = Annotated[StepperActuator | ServoActuator, Field(discriminator="type")]


class LimitSwitch(ConfigModel):
    present: Todo[bool] = todo("si hay final de carrera para el homing de la base (true o false)")
    active_low: Todo[bool] = todo(
        "si el final de carrera da nivel bajo al activarse (true o false)"
    )
    homing_velocity_rad_s: Todo[PositiveFloat] = todo(
        "velocidad de la base durante el homing (rad/s)"
    )
    home_angle_rad: Todo[float] = todo(
        "ángulo de la base en el final de carrera o en la marca física (rad)"
    )


class BaseStepper(ConfigModel):
    driver: Todo[str] = todo("modelo del driver del NEMA17, por ejemplo TMC2209 o A4988")
    steps_per_rev: Todo[PositiveInt] = todo("pasos completos por vuelta del motor (pasos)")
    microsteps: Todo[PositiveInt] = todo("micropasos configurados en el driver (entero)")
    gear_ratio: Todo[PositiveFloat] = todo(
        "relación de transmisión de la base: vueltas del motor por vuelta de la base"
    )
    inverted: Todo[bool] = todo(
        "true si DIR en alto gira la base en sentido negativo (true o false)"
    )
    limit_switch: LimitSwitch


class Gripper(ConfigModel):
    fingers: PositiveInt
    servo_count: Todo[PositiveInt] = todo("cuántos servos usa la garra (entero)")
    servo_model: Todo[str] = todo("modelo de los servos de la garra: MG996 o MG90")
    pca9685_channels: Todo[list[PcaChannel]] = todo(
        "canales del PCA9685 de los servos de la garra (lista de 0 a 15)"
    )
    open_pulse_us: Todo[PositiveInt] = todo("ancho de pulso con la garra abierta (microsegundos)")
    close_pulse_us: Todo[PositiveInt] = todo(
        "ancho de pulso con la garra cerrada sobre una pieza (microsegundos)"
    )
    actuation_time_s: Todo[PositiveFloat] = todo("tiempo que tarda la garra en abrir o cerrar (s)")


class Actuators(ConfigModel):
    joints: list[JointActuator]
    base_stepper: BaseStepper
    gripper: Gripper


class Pca9685(ConfigModel):
    i2c_address: Todo[NonNegativeInt] = todo("dirección I2C del PCA9685 (entero, por ejemplo 0x40)")
    sda_pin: Todo[NonNegativeInt] = todo("pin GPIO del ESP32 para SDA del bus I2C")
    scl_pin: Todo[NonNegativeInt] = todo("pin GPIO del ESP32 para SCL del bus I2C")
    pwm_frequency_hz: Todo[PositiveFloat] = todo("frecuencia PWM de los servos (Hz)")


class StepperPins(ConfigModel):
    step_pin: Todo[NonNegativeInt] = todo("pin GPIO del ESP32 para STEP del driver")
    dir_pin: Todo[NonNegativeInt] = todo("pin GPIO del ESP32 para DIR del driver")
    enable_pin: Todo[NonNegativeInt] = todo("pin GPIO del ESP32 para EN del driver")
    limit_switch_pin: Todo[NonNegativeInt] = todo(
        "pin GPIO del ESP32 para el final de carrera de la base"
    )


class Electronics(ConfigModel):
    pca9685: Pca9685
    stepper_pins: StepperPins


class Bridge(ConfigModel):
    transport: Literal["serial", "mock"]
    serial_port: Todo[str] = todo("puerto serial del ESP32 en este PC, por ejemplo COM5")
    baudrate: PositiveInt
    watchdog_ms: PositiveInt
    ping_interval_ms: PositiveInt
    ack_timeout_ms: PositiveInt
    done_timeout_margin_s: PositiveFloat
    idempotent_retries: NonNegativeInt
    seq_max_steps: PositiveInt

    @model_validator(mode="after")
    def _check_ping(self) -> "Bridge":
        if self.ping_interval_ms >= self.watchdog_ms:
            raise ValueError("ping_interval_ms debe ser menor que watchdog_ms")
        return self


class SimRender(ConfigModel):
    width_px: PositiveInt
    height_px: PositiveInt


class Sim(ConfigModel):
    timestep_s: PositiveFloat
    grasp_mode: Literal["friction", "weld"]
    mesh_dir: str
    use_meshes: bool
    render: SimRender


class DatasetSplits(ConfigModel):
    train: Fraction
    val: Fraction
    test: Fraction

    @model_validator(mode="after")
    def _check_sum(self) -> "DatasetSplits":
        if abs(self.train + self.val + self.test - 1.0) > 1e-9:
            raise ValueError("train, val y test deben sumar 1")
        return self


class IkDataset(ConfigModel):
    dir: str
    format: Literal["parquet"]
    num_samples: PositiveInt
    workspace_oversampling_fraction: Fraction
    splits: DatasetSplits


class IkModel(ConfigModel):
    input_dim: Literal[9]
    output_dim: Literal[12]
    hidden_layers: Annotated[list[PositiveInt], Field(min_length=1)]
    activation: str


class IkLossWeights(ConfigModel):
    sin_cos: float = Field(ge=0.0)
    fk_position: float = Field(ge=0.0)
    fk_orientation: float = Field(ge=0.0)


class IkTraining(ConfigModel):
    device: Literal["cpu", "cuda"]
    batch_size: PositiveInt
    learning_rate: PositiveFloat
    max_epochs: PositiveInt
    early_stopping_patience: PositiveInt


class IkTargets(ConfigModel):
    position_median_m: PositiveFloat
    position_p95_m: PositiveFloat
    orientation_p95_rad: PositiveFloat


class NeuralIk(ConfigModel):
    seed: int
    dataset: IkDataset
    model: IkModel
    loss_weights: IkLossWeights
    training: IkTraining
    targets: IkTargets
    model_dir: str


class GripperMarker(ConfigModel):
    dictionary: Todo[str] = todo(
        "diccionario ArUco del marcador de la garra, por ejemplo DICT_4X4_50"
    )
    id: Todo[NonNegativeInt] = todo("id del marcador ArUco de la garra (entero)")
    size_m: Todo[PositiveFloat] = todo("lado del marcador impreso de la garra (m)")
    offset_position_m: Todo[Vec3] = todo(
        "posición [x, y, z] del marcador respecto al punto de agarre (m)"
    )
    offset_rpy_rad: Todo[Vec3] = todo(
        "orientación [roll, pitch, yaw] del marcador respecto a la garra (rad)"
    )


class LearnedCalibration(ConfigModel):
    gripper_marker: GripperMarker
    seed: int
    data_dir: str
    model_dir: str
    target_error_reduction: Fraction


class ArmConfig(ConfigModel):
    """Configuración completa del brazo."""

    schema_version: Literal[1]
    fictitious: bool = False
    arm: ArmInfo
    kinematics: Kinematics
    actuators: Actuators
    electronics: Electronics
    bridge: Bridge
    sim: Sim
    neural_ik: NeuralIk
    learned_calibration: LearnedCalibration

    @model_validator(mode="after")
    def _check_joints(self) -> "ArmConfig":
        names = [joint.name for joint in self.kinematics.joints]
        if len(names) != self.arm.dof:
            raise ValueError(f"kinematics.joints debe tener {self.arm.dof} articulaciones")
        if len(set(names)) != len(names):
            raise ValueError("kinematics.joints tiene nombres repetidos")
        actuated = [actuator.joint for actuator in self.actuators.joints]
        if actuated != names:
            raise ValueError(
                "actuators.joints debe listar las mismas articulaciones que kinematics.joints, "
                "en el mismo orden"
            )
        return self
