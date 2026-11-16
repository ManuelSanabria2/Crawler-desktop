"""Almacenamiento en SQLite y capturas."""

from geass.crawler.store.db import CrawlerStore, StoreError
from geass.crawler.store.recording import ExcludedAppRecordError, RecordedState, record_dump
from geass.crawler.store.records import (
    AppRecord,
    ElementInput,
    ElementRecord,
    ScreenshotRecord,
    SessionRecord,
    StateRecord,
    TransitionRecord,
)

__all__ = [
    "AppRecord",
    "CrawlerStore",
    "ElementInput",
    "ElementRecord",
    "ExcludedAppRecordError",
    "RecordedState",
    "ScreenshotRecord",
    "SessionRecord",
    "StateRecord",
    "StoreError",
    "TransitionRecord",
    "record_dump",
]
