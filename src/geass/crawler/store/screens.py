"""Capturas de ventana: toma con mss, tapado, deduplicación por hash perceptual y retención.

Privacidad:

- Las capturas están apagadas salvo que ``store.capture_screens`` sea verdadero.
- Solo se captura la ventana en primer plano: si otra ventana la tapa, la región de pantalla
  mostraría contenido ajeno.
- No se captura ninguna ventana de una app excluida.
- Antes de guardar se tapan con un rectángulo negro los campos de contraseña y los tipos de
  control de ``store.redact_control_types``, para no guardar el valor de los campos de texto.
- Las capturas nunca salen de ``data/``.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import imagehash
from PIL import Image, ImageDraw

from geass.core.log import get_logger
from geass.core.paths import ensure_dir, resolve_project_path
from geass.core.schemas.crawler import CrawlerConfig
from geass.crawler.store.db import CrawlerStore
from geass.crawler.store.records import ScreenshotRecord
from geass.crawler.uia.schema import Rect, WindowDump

logger = get_logger(__name__)

BYTES_PER_MB = 1024 * 1024
REDACTION_COLOR = (0, 0, 0)


@dataclass(frozen=True)
class SavedScreenshot:
    """Resultado de guardar una captura. Si ``duplicate`` es verdadero no se escribió archivo."""

    record: ScreenshotRecord
    duplicate: bool

    @property
    def file_name(self) -> str:
        return self.record.file_name


def capture_region(rect: Rect) -> Image.Image:
    """Captura una región de la pantalla con mss."""
    import mss

    region = {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}
    with mss.mss() as grabber:
        shot = grabber.grab(region)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def foreground_window_handle() -> int:
    """Handle de la ventana en primer plano."""
    import win32gui

    return int(win32gui.GetForegroundWindow())


def redact_image(image: Image.Image, dump: WindowDump, control_types: Sequence[str]) -> Image.Image:
    """Devuelve una copia con los campos de contraseña y los tipos indicados tapados."""
    origin = dump.root.rect
    result = image.copy()
    if origin is None:
        return result
    draw = ImageDraw.Draw(result)
    hidden = set(control_types)
    for element in dump.root.iter_tree():
        if element.rect is None or not (element.is_password or element.control_type in hidden):
            continue
        rect = element.rect
        box = (
            max(rect.left - origin.left, 0),
            max(rect.top - origin.top, 0),
            min(rect.right - origin.left, image.width),
            min(rect.bottom - origin.top, image.height),
        )
        if box[2] > box[0] and box[3] > box[1]:
            draw.rectangle(box, fill=REDACTION_COLOR)
    return result


def capture_window(
    dump: WindowDump,
    *,
    enabled: bool,
    redact_control_types: Sequence[str],
    excluded_apps: Sequence[str],
    grab: Callable[[Rect], Image.Image] = capture_region,
    foreground: Callable[[], int] = foreground_window_handle,
) -> Image.Image | None:
    """Captura la ventana del volcado, ya tapada. Devuelve ``None`` si no se debe capturar."""
    if not enabled:
        return None
    process = dump.process_name.strip().lower()
    if not process or process in {name.strip().lower() for name in excluded_apps}:
        return None
    if dump.root.rect is None:
        return None
    if foreground() != dump.handle:
        logger.debug("No se captura %s: no está en primer plano", dump.process_name)
        return None
    return redact_image(grab(dump.root.rect), dump, redact_control_types)


class ScreenshotStore:
    """Guarda capturas en una carpeta, sin duplicados, y aplica la política de retención."""

    def __init__(
        self,
        store: CrawlerStore,
        directory: Path,
        *,
        max_distance: int,
        max_age_days: int,
        max_total_mb: int,
    ) -> None:
        self._store = store
        self.directory = directory
        self._max_distance = max_distance
        self._max_age = timedelta(days=max_age_days)
        self._max_total_bytes = max_total_mb * BYTES_PER_MB

    @classmethod
    def from_config(cls, store: CrawlerStore, config: CrawlerConfig) -> "ScreenshotStore":
        settings = config.store
        return cls(
            store,
            resolve_project_path(settings.screens_dir),
            max_distance=settings.phash_max_distance,
            max_age_days=settings.retention.max_age_days,
            max_total_mb=settings.retention.max_total_mb,
        )

    def save(self, image: Image.Image) -> SavedScreenshot:
        """Guarda la imagen como PNG, salvo que ya exista una casi igual."""
        phash = imagehash.phash(image)
        for record in self._store.list_screenshots():
            same_size = (record.width, record.height) == image.size
            if same_size and phash - imagehash.hex_to_hash(record.phash) <= self._max_distance:
                return SavedScreenshot(record=record, duplicate=True)

        ensure_dir(self.directory)
        stamp = self._store.now().strftime("%Y%m%dT%H%M%S%f")
        file_name = f"{stamp}_{phash}_{image.width}x{image.height}.png"
        path = self.directory / file_name
        image.save(path, format="PNG")
        record = self._store.add_screenshot(
            file_name, str(phash), image.width, image.height, path.stat().st_size
        )
        return SavedScreenshot(record=record, duplicate=False)

    def path_of(self, file_name: str) -> Path:
        return self.directory / file_name

    def total_bytes(self) -> int:
        return sum(record.size_bytes for record in self._store.list_screenshots())

    def apply_retention(self, now: datetime | None = None) -> list[str]:
        """Borra las capturas vencidas y luego las más antiguas hasta respetar el tamaño máximo.

        Solo borra archivos registrados en la base y dentro de la carpeta de capturas.
        Devuelve los nombres de los archivos borrados.
        """
        now = now if now is not None else self._store.now()
        records = self._store.list_screenshots()
        doomed = [record for record in records if now - record.created_at > self._max_age]
        doomed_names = {record.file_name for record in doomed}
        remaining = [record for record in records if record.file_name not in doomed_names]
        total = sum(record.size_bytes for record in remaining)
        for record in remaining:
            if total <= self._max_total_bytes:
                break
            doomed.append(record)
            total -= record.size_bytes

        removed = []
        for record in doomed:
            self._remove(record)
            removed.append(record.file_name)
        if removed:
            logger.info("Retención de capturas: %d archivos borrados", len(removed))
        return removed

    def _remove(self, record: ScreenshotRecord) -> None:
        path = self.directory / record.file_name
        if path.parent.resolve() == self.directory.resolve() and path.is_file():
            path.unlink()
        self._store.remove_screenshot(record.file_name)
