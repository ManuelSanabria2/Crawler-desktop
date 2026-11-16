"""Errores compartidos de GEASS."""


class GeassError(Exception):
    """Base de todos los errores propios de GEASS."""


class ConfigError(GeassError):
    """Problema con la configuración: archivo ausente, perfil desconocido o YAML inválido."""


class ConfigValidationError(ConfigError):
    """El YAML existe pero no cumple el esquema."""


class ConfigIncompleteError(ConfigError):
    """Se intentó usar un dato que sigue como TODO en la configuración.

    Atributos:
        key: ruta de la clave dentro del YAML, por ejemplo ``kinematics.joints[0].dh.a_m``.
        meaning: qué es el dato y en qué unidad va.
        source: nombre del archivo de configuración, si se conoce.
    """

    def __init__(self, key: str, meaning: str, source: str = "") -> None:
        self.key = key
        self.meaning = meaning
        self.source = source
        where = f" en config/{source}" if source else ""
        super().__init__(
            f"Falta el dato '{key}'{where}: {meaning}. "
            "Sigue como TODO; hay que pedírselo a Manuel y completarlo en el YAML."
        )
