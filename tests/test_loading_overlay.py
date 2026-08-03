import tkinter

import pytest

from src.ui.loading_overlay import LoadingOverlay
from src.ui.styles import Theme


@pytest.fixture
def root():
    root = tkinter.Tk()
    Theme.apply(root, "Dark")
    yield root
    root.destroy()


def test_fast_operation_does_not_flash_overlay(root):
    overlay = LoadingOverlay(root)
    overlay.show("Quick task", delay_ms=100)
    overlay.hide()
    root.update()

    assert overlay._visible is False
    assert not overlay.winfo_ismapped()


def test_visible_overlay_explains_work_and_elapsed_time(root):
    overlay = LoadingOverlay(root)
    overlay.show("Loading dataset", delay_ms=0)
    overlay.update_status("Reading ratings...", "Rebuilding recommendation indexes.")
    root.update()

    assert overlay._visible is True
    assert overlay.status_lbl.cget("text") == "Reading ratings..."
    assert "recommendation" in overlay.detail_lbl.cget("text")
    assert overlay.elapsed_lbl.cget("text").startswith("Working for")

    overlay.hide()
    assert overlay._visible is False
