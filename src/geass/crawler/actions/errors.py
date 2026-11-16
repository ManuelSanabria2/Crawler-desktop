"""Errores de las acciones directas y del lanzador de apps."""

from geass.core.errors import GeassError


class ActionError(GeassError):
    """Una acción directa no se pudo hacer."""


class ActionDisabledError(ActionError):
    """La acción no está en ``launcher.direct_actions`` de crawler.yaml."""


class ActionBlockedError(ActionError):
    """La app coincide con la lista de bloqueo de safety.yaml."""
