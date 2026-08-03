import hashlib
import io
import queue
import time
import tkinter
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from src.ui.components import CardToolTip
from src.ui.styles import Theme


def _image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (80, 112), "#336699").save(output, format="JPEG")
    return output.getvalue()


def test_image_worker_only_queues_pixels(tmp_path):
    """The executor thread must not invoke after(), winfo_exists(), or Tcl/Tk."""
    tooltip = object.__new__(CardToolTip)
    tooltip._image_results = queue.Queue()
    tooltip.IMAGE_CACHE_DIR = str(tmp_path)
    response = SimpleNamespace(
        content=_image_bytes(),
        raise_for_status=lambda: None,
    )

    CardToolTip._in_memory_images.clear()
    with patch("src.ui.components.requests.get", return_value=response):
        tooltip._fetch_and_apply_image(
            "https://example.test/card.jpg", 1.0, "worker-only"
        )

    kind, image = tooltip._image_results.get_nowait()
    assert kind == "image"
    assert image.size == (80, 112)


def test_tooltip_image_is_applied_by_tk_event_loop(tmp_path):
    root = tkinter.Tk()
    Theme.apply(root, "Dark")
    url = "https://example.test/cached-card.jpg"
    cache_key = hashlib.md5(url.encode("utf-8")).hexdigest()
    (tmp_path / f"{cache_key}.jpg").write_bytes(_image_bytes())

    original_cache_dir = CardToolTip.IMAGE_CACHE_DIR
    CardToolTip.IMAGE_CACHE_DIR = str(tmp_path)
    CardToolTip._in_memory_images.clear()
    try:
        card = {
            "name": "Thread-Safe Test Card",
            "types": ["Creature"],
            "image": [url],
            "deck_colors": {},
        }
        CardToolTip.create(root, card, images_enabled=True, scale=1.0)
        tooltip = CardToolTip._active_tooltip

        deadline = time.time() + 2
        while time.time() < deadline and not hasattr(tooltip, "tk_img"):
            root.update()
            time.sleep(0.01)

        assert hasattr(tooltip, "tk_img")
        assert tooltip._image_poll_id is None
        tooltip._close()
        root.update()
    finally:
        CardToolTip.IMAGE_CACHE_DIR = original_cache_dir
        CardToolTip._active_tooltip = None
        try:
            root.destroy()
        except tkinter.TclError:
            pass


def test_card_preview_persists_until_next_owner_click():
    root = tkinter.Tk()
    Theme.apply(root, "Dark")
    card = {
        "name": "Persistent Preview",
        "types": ["Creature"],
        "deck_colors": {},
    }
    try:
        CardToolTip.create(root, card, images_enabled=False, scale=1.0)
        tooltip = CardToolTip._active_tooltip
        root.update()

        root.event_generate("<Leave>")
        root.update()
        assert tooltip.winfo_exists()

        root.event_generate("<Button-1>", x=5, y=5)
        root.update()
        assert not tooltip.winfo_exists()
        assert CardToolTip._active_tooltip is None
    finally:
        CardToolTip._active_tooltip = None
        try:
            root.destroy()
        except tkinter.TclError:
            pass


def test_in_memory_image_hit_has_anchor_before_synchronous_render():
    root = tkinter.Tk()
    Theme.apply(root, "Dark")
    url = "https://example.test/in-memory-card.jpg"
    cache_key = hashlib.md5(url.encode("utf-8")).hexdigest()
    CardToolTip._in_memory_images[cache_key] = Image.new("RGB", (80, 112), "#884422")
    card = {
        "name": "Cached Preview",
        "types": ["Creature"],
        "image": [url],
        "deck_colors": {},
    }
    try:
        CardToolTip.create(root, card, images_enabled=True, scale=1.0)
        tooltip = CardToolTip._active_tooltip
        root.update()

        assert tooltip is not None
        assert tooltip.winfo_exists()
        assert hasattr(tooltip, "_mouse_x")
        assert hasattr(tooltip, "tk_img")
    finally:
        if CardToolTip._active_tooltip is not None:
            CardToolTip._active_tooltip._close()
        CardToolTip._active_tooltip = None
        CardToolTip._in_memory_images.clear()
        try:
            root.destroy()
        except tkinter.TclError:
            pass
