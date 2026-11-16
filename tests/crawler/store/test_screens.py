"""Tests de las capturas: tapado, deduplicación por hash perceptual y retención."""

import json
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from geass.core.config import load_crawler_config
from geass.crawler.store import CrawlerStore
from geass.crawler.store.screens import (
    BYTES_PER_MB,
    ScreenshotStore,
    capture_window,
    redact_image,
)
from geass.crawler.uia import Rect, WindowDump

START = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> Iterator[CrawlerStore]:
    with CrawlerStore(tmp_path / "crawler.sqlite", clock=clock) as opened:
        yield opened


@pytest.fixture
def screens(store: CrawlerStore, tmp_path: Path) -> ScreenshotStore:
    return ScreenshotStore(
        store, tmp_path / "screens", max_distance=4, max_age_days=30, max_total_mb=1
    )


def picture(seed: int, size: tuple[int, int] = (320, 240)) -> Image.Image:
    """Imagen sintética con formas distintas según la semilla."""
    image = Image.new("RGB", size, (245, 245, 245))
    draw = ImageDraw.Draw(image)
    for index in range(6):
        value = (seed * 7919 + index * 104729) % 1000
        left = value % (size[0] - 80)
        top = (value * 3) % (size[1] - 60)
        color = ((value * 5) % 256, (value * 11) % 256, (value * 17) % 256)
        draw.rectangle((left, top, left + 30 + value % 50, top + 20 + value % 40), fill=color)
    return image


def noisy(image: Image.Image) -> Image.Image:
    """La misma imagen con un cambio mínimo, como un cursor parpadeando."""
    copy = image.copy()
    ImageDraw.Draw(copy).line((5, 5, 5, 12), fill=(0, 0, 0))
    return copy


# Guardado y deduplicación


def test_save_writes_a_png_and_a_record(screens: ScreenshotStore, store: CrawlerStore) -> None:
    saved = screens.save(picture(1))
    assert saved.duplicate is False
    path = screens.path_of(saved.file_name)
    assert path.is_file()
    assert path.parent == screens.directory
    assert path.suffix == ".png"
    with Image.open(path) as stored:
        assert stored.size == (320, 240)
    record = saved.record
    assert (record.width, record.height) == (320, 240)
    assert record.size_bytes == path.stat().st_size
    assert record.created_at == START
    assert len(record.phash) == 16
    assert store.counts()["screenshots"] == 1


def test_identical_and_near_identical_images_are_deduplicated(
    screens: ScreenshotStore, store: CrawlerStore
) -> None:
    first = screens.save(picture(1))
    same = screens.save(picture(1))
    almost = screens.save(noisy(picture(1)))
    assert same.duplicate is True and almost.duplicate is True
    assert same.file_name == almost.file_name == first.file_name
    assert store.counts()["screenshots"] == 1
    assert len(list(screens.directory.iterdir())) == 1


def test_different_images_are_kept(screens: ScreenshotStore, store: CrawlerStore) -> None:
    names = {screens.save(picture(seed)).file_name for seed in range(1, 6)}
    assert len(names) == 5
    assert store.counts()["screenshots"] == 5
    assert len(list(screens.directory.iterdir())) == 5


def test_same_picture_with_another_size_is_not_a_duplicate(screens: ScreenshotStore) -> None:
    screens.save(picture(1))
    assert screens.save(picture(1).resize((640, 480))).duplicate is False


def test_max_distance_zero_only_merges_equal_hashes(store: CrawlerStore, tmp_path: Path) -> None:
    strict = ScreenshotStore(
        store, tmp_path / "strict", max_distance=0, max_age_days=30, max_total_mb=1
    )
    strict.save(picture(1))
    assert strict.save(picture(1)).duplicate is True
    assert strict.save(picture(2)).duplicate is False


# Retención


def test_retention_removes_old_screenshots(
    screens: ScreenshotStore, store: CrawlerStore, clock: FakeClock
) -> None:
    old = screens.save(picture(1))
    clock.advance(days=20)
    recent = screens.save(picture(2))
    clock.advance(days=15)

    removed = screens.apply_retention()
    assert removed == [old.file_name]
    assert not screens.path_of(old.file_name).exists()
    assert screens.path_of(recent.file_name).is_file()
    assert [record.file_name for record in store.list_screenshots()] == [recent.file_name]
    assert screens.apply_retention() == []


def test_retention_enforces_the_total_size_oldest_first(
    store: CrawlerStore, tmp_path: Path, clock: FakeClock
) -> None:
    screens = ScreenshotStore(
        store, tmp_path / "screens", max_distance=0, max_age_days=365, max_total_mb=1
    )
    saved = []
    for seed in range(1, 6):
        # Ruido aleatorio: no se comprime, cada PNG pesa unos 0,36 MB.
        image = Image.effect_noise((350, 350), 60 + seed).convert("RGB")
        saved.append(screens.save(image))
        clock.advance(hours=1)
    assert screens.total_bytes() > BYTES_PER_MB

    removed = screens.apply_retention()
    assert removed == [item.file_name for item in saved[: len(removed)]]
    assert 0 < len(removed) < 5
    assert screens.total_bytes() <= BYTES_PER_MB
    assert screens.path_of(saved[-1].file_name).is_file()
    assert len(list(screens.directory.iterdir())) == 5 - len(removed)


def test_retention_unlinks_the_state_that_used_the_screenshot(
    screens: ScreenshotStore, store: CrawlerStore, clock: FakeClock
) -> None:
    saved = screens.save(picture(1))
    app = store.upsert_app("app.exe")
    state = store.upsert_state(app.id, "s", "d", "t", screenshot_path=saved.file_name)
    assert store.get_state(state.id).screenshot_path == saved.file_name
    clock.advance(days=31)
    screens.apply_retention()
    assert store.get_state(state.id).screenshot_path is None


def test_retention_survives_a_missing_file_and_ignores_other_files(
    screens: ScreenshotStore, store: CrawlerStore, clock: FakeClock
) -> None:
    saved = screens.save(picture(1))
    stranger = screens.directory / "no_es_mio.txt"
    stranger.write_text("ajeno", encoding="utf-8")
    screens.path_of(saved.file_name).unlink()
    clock.advance(days=31)
    assert screens.apply_retention() == [saved.file_name]
    assert store.counts()["screenshots"] == 0
    assert stranger.is_file()


def test_from_config_reads_the_store_settings(
    store: CrawlerStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_crawler_config()
    monkeypatch.setenv("GEASS_ROOT", str(tmp_path))
    screens = ScreenshotStore.from_config(store, config)
    assert screens.directory == tmp_path / "data" / "crawler" / "screens"
    assert config.store.capture_screens is False
    assert {"Edit", "Document"} <= set(config.store.redact_control_types)


# Tapado y política de captura


def window_dump(
    uia_fixture: Callable[[str], WindowDump], name: str = "notepad_short"
) -> WindowDump:
    return uia_fixture(name)


def test_redaction_blacks_out_text_areas(uia_fixture: Callable[[str], WindowDump]) -> None:
    dump = window_dump(uia_fixture)
    window = dump.root.rect
    (document,) = dump.root.find_all(control_type="Document")
    white = Image.new("RGB", (window.width, window.height), (255, 255, 255))

    redacted = redact_image(white, dump, ["Edit", "Document"])
    inside = (
        (document.rect.left + document.rect.right) // 2 - window.left,
        (document.rect.top + document.rect.bottom) // 2 - window.top,
    )
    assert redacted.getpixel(inside) == (0, 0, 0)
    assert white.getpixel(inside) == (255, 255, 255)
    button = dump.root.find_all(automation_id="SettingsButton")[0].rect
    outside = (
        (button.left + button.right) // 2 - window.left,
        (button.top + button.bottom) // 2 - window.top,
    )
    assert redacted.getpixel(outside) == (255, 255, 255)
    assert redact_image(white, dump, []).getpixel(inside) == (255, 255, 255)


def test_password_fields_are_always_blacked_out(uia_fixture: Callable[[str], WindowDump]) -> None:
    dump = window_dump(uia_fixture, "calculator_a")
    data = json.loads(dump.model_dump_json())
    target = data["root"]["children"][0]
    target["is_password"] = True
    target["rect"] = {
        "left": data["root"]["rect"]["left"] + 10,
        "top": data["root"]["rect"]["top"] + 10,
        "right": data["root"]["rect"]["left"] + 60,
        "bottom": data["root"]["rect"]["top"] + 40,
    }
    with_password = WindowDump.model_validate(data)
    window = with_password.root.rect
    white = Image.new("RGB", (window.width, window.height), (255, 255, 255))
    assert redact_image(white, with_password, []).getpixel((30, 20)) == (0, 0, 0)


def capture(dump: WindowDump, **changes: object) -> tuple[Image.Image | None, list[Rect]]:
    grabbed: list[Rect] = []

    def grab(rect: Rect) -> Image.Image:
        grabbed.append(rect)
        return Image.new("RGB", (rect.width, rect.height), (255, 255, 255))

    options: dict = {
        "enabled": True,
        "redact_control_types": ["Edit", "Document"],
        "excluded_apps": [],
        "grab": grab,
        "foreground": lambda: dump.handle,
    }
    options.update(changes)
    return capture_window(dump, **options), grabbed


def test_capture_window_grabs_the_window_rect_and_redacts(
    uia_fixture: Callable[[str], WindowDump],
) -> None:
    dump = window_dump(uia_fixture)
    image, grabbed = capture(dump)
    assert grabbed == [dump.root.rect]
    assert image.size == (dump.root.rect.width, dump.root.rect.height)
    assert (0, 0, 0) in {color for _, color in image.getcolors(maxcolors=16)}


def test_capture_is_off_unless_enabled(uia_fixture: Callable[[str], WindowDump]) -> None:
    image, grabbed = capture(window_dump(uia_fixture), enabled=False)
    assert image is None
    assert grabbed == []


def test_capture_skips_windows_that_are_not_in_the_foreground(
    uia_fixture: Callable[[str], WindowDump],
) -> None:
    image, grabbed = capture(window_dump(uia_fixture), foreground=lambda: 1)
    assert image is None
    assert grabbed == []


def test_capture_skips_excluded_and_unknown_apps(uia_fixture: Callable[[str], WindowDump]) -> None:
    dump = window_dump(uia_fixture)
    assert capture(dump, excluded_apps=["Notepad.EXE"]) == (None, [])
    data = json.loads(dump.model_dump_json())
    data["process_name"] = ""
    assert capture(WindowDump.model_validate(data)) == (None, [])


@pytest.mark.windows_ui
@pytest.mark.skipif(sys.platform != "win32", reason="solo Windows")
def test_capture_region_with_mss_returns_the_requested_size() -> None:
    from geass.crawler.store.screens import capture_region

    image = capture_region(Rect(left=0, top=0, right=64, bottom=48))
    assert image.size == (64, 48)
    assert image.mode == "RGB"
