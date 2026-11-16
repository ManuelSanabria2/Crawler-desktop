"""Lectura del árbol de UI Automation y su esquema."""

from geass.crawler.uia.dump import (
    ExcludedAppError,
    UiaError,
    WindowNotFoundError,
    dump_window,
    limits_from_config,
)
from geass.crawler.uia.reader import DumpLimits, NodeProps, UiaNodeError, walk_tree
from geass.crawler.uia.schema import Element, Rect, WindowDump

__all__ = [
    "DumpLimits",
    "Element",
    "ExcludedAppError",
    "NodeProps",
    "Rect",
    "UiaError",
    "UiaNodeError",
    "WindowDump",
    "WindowNotFoundError",
    "dump_window",
    "limits_from_config",
    "walk_tree",
]
