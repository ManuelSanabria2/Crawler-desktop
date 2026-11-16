"""Información del paquete instalado de GEASS."""

import importlib.metadata

PACKAGE_NAME = "geass"


def version() -> str:
    """Devuelve la versión instalada del paquete geass, por ejemplo "0.1.0".

    La fuente de verdad es el campo ``version`` de ``pyproject.toml``, que llega a los
    metadatos del paquete al instalarlo. Si el paquete no está instalado, se propaga
    ``importlib.metadata.PackageNotFoundError``.
    """
    return importlib.metadata.version(PACKAGE_NAME)
