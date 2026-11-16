"""Esquema de config/board.yaml."""

from typing import Annotated, Literal

from pydantic import Field, NonNegativeInt, PositiveFloat, PositiveInt

from geass.core.schema import ConfigModel, Todo, todo

Vec3 = tuple[float, float, float]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class Board(ConfigModel):
    squares_per_side: Literal[8]
    square_size_m: Todo[PositiveFloat] = todo("lado de una casilla (m)")
    thickness_m: Todo[PositiveFloat] = todo("grosor del tablero (m)")
    center_position_m: Todo[Vec3] = todo(
        "posición [x, y, z] del centro del tablero respecto a la base del brazo (m)"
    )
    yaw_rad: Todo[float] = todo("giro del tablero alrededor de z respecto a la base (rad)")
    robot_side: Todo[Literal["white", "black"]] = todo(
        "color cuyas piezas quedan del lado del brazo: white o black"
    )


class PieceDimensions(ConfigModel):
    height_m: Todo[PositiveFloat] = todo("alto de la pieza (m)")
    diameter_m: Todo[PositiveFloat] = todo("diámetro máximo de la pieza (m)")


class Pieces(ConfigModel):
    king: PieceDimensions
    queen: PieceDimensions
    rook: PieceDimensions
    bishop: PieceDimensions
    knight: PieceDimensions
    pawn: PieceDimensions


class MotionHeights(ConfigModel):
    approach_m: Todo[PositiveFloat] = todo(
        "altura de aproximación sobre la casilla, medida desde el tablero (m)"
    )
    safe_travel_m: Todo[PositiveFloat] = todo(
        "altura de traslado que libra la pieza más alta, medida desde el tablero (m)"
    )
    grasp_height_fraction: Todo[Fraction] = todo(
        "fracción del alto de la pieza a la que cierra la garra (0 a 1)"
    )


class Graveyard(ConfigModel):
    origin_position_m: Todo[Vec3] = todo(
        "posición [x, y, z] de la primera posición del cementerio respecto a la base (m)"
    )
    yaw_rad: Todo[float] = todo("orientación de la cuadrícula del cementerio (rad)")
    rows: Todo[PositiveInt] = todo("filas de la cuadrícula del cementerio (entero)")
    cols: Todo[PositiveInt] = todo("columnas de la cuadrícula del cementerio (entero)")
    spacing_m: Todo[PositiveFloat] = todo("separación entre posiciones del cementerio (m)")


class CameraIntrinsics(ConfigModel):
    camera_matrix: Todo[list[list[float]]] = todo(
        "matriz intrínseca 3x3 de la cámara, de la calibración con OpenCV (píxeles)"
    )
    distortion_coeffs: Todo[list[float]] = todo(
        "coeficientes de distorsión de la cámara, de la calibración con OpenCV"
    )


class Camera(ConfigModel):
    model: Todo[str] = todo("modelo de la cámara")
    device_index: Todo[NonNegativeInt] = todo("índice de la cámara en OpenCV en este PC (entero)")
    resolution_px: Todo[tuple[PositiveInt, PositiveInt]] = todo(
        "resolución [ancho, alto] de la cámara (píxeles)"
    )
    fps: Todo[PositiveFloat] = todo("cuadros por segundo de la captura (1/s)")
    mount_position_m: Todo[Vec3] = todo(
        "posición [x, y, z] de la cámara respecto a la base del brazo (m)"
    )
    mount_rpy_rad: Todo[Vec3] = todo(
        "orientación [roll, pitch, yaw] de la cámara respecto a la base del brazo (rad)"
    )
    intrinsics: CameraIntrinsics


class ArucoCornerIds(ConfigModel):
    a1: Todo[NonNegativeInt] = todo("id del marcador ArUco de la esquina a1 (entero)")
    h1: Todo[NonNegativeInt] = todo("id del marcador ArUco de la esquina h1 (entero)")
    h8: Todo[NonNegativeInt] = todo("id del marcador ArUco de la esquina h8 (entero)")
    a8: Todo[NonNegativeInt] = todo("id del marcador ArUco de la esquina a8 (entero)")


class Aruco(ConfigModel):
    dictionary: Todo[str] = todo(
        "diccionario ArUco de los marcadores del tablero, por ejemplo DICT_4X4_50"
    )
    marker_size_m: Todo[PositiveFloat] = todo("lado de cada marcador del tablero (m)")
    corner_ids: ArucoCornerIds
    corner_offset_m: Todo[float] = todo(
        "distancia del centro de cada marcador a la esquina del tablero (m)"
    )


class SquareClassifier(ConfigModel):
    num_classes: Literal[13]
    architectures: Annotated[list[str], Field(min_length=1)]
    model_dir: str


class DomainRandomization(ConfigModel):
    lighting: bool
    textures: bool
    noise: bool
    camera_jitter: bool


class SyntheticData(ConfigModel):
    dir: str
    num_positions: PositiveInt
    domain_randomization: DomainRandomization


class RealData(ConfigModel):
    dir: str


class PerceptionTargets(ConfigModel):
    square_accuracy: Fraction
    move_detection_rate: Fraction


class Perception(ConfigModel):
    seed: int
    warped_size_px: PositiveInt
    square_crop_px: PositiveInt
    classifier: SquareClassifier
    synthetic_data: SyntheticData
    real_data: RealData
    targets: PerceptionTargets


class Stockfish(ConfigModel):
    executable_path: Todo[str] = todo("ruta del ejecutable de Stockfish en este PC")
    skill_level: int = Field(ge=0, le=20)
    move_time_s: PositiveFloat


class BoardConfig(ConfigModel):
    """Configuración del tablero, la percepción y el motor de ajedrez."""

    schema_version: Literal[1]
    board: Board
    pieces: Pieces
    motion_heights: MotionHeights
    graveyard: Graveyard
    camera: Camera
    aruco: Aruco
    perception: Perception
    stockfish: Stockfish
