"""Regression coverage for routed mouse-wheel and trackpad scrolling."""

import sys
import tkinter
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from tkinter import ttk

from src.ui.components import ModernTreeview, ScrolledFrame
from src.utils import _ScrollRouter, bind_scroll


@pytest.fixture
def root():
    window = tkinter.Tk()
    window.geometry("360x220")
    yield window
    window.destroy()


def test_trackpad_delta_is_normalized_on_macos():
    with patch.object(sys, "platform", "darwin"):
        assert _ScrollRouter._units(SimpleNamespace(delta=-93)) == 1
        assert _ScrollRouter._units(SimpleNamespace(delta=41)) == -1
        assert _ScrollRouter._units(SimpleNamespace(delta=0)) == 0


def test_registration_is_idempotent(root):
    frame = ttk.Frame(root)
    calls = []

    def scroll(units, mode):
        calls.append((units, mode))

    router = bind_scroll(frame, scroll)
    for _ in range(5):
        assert bind_scroll(frame, scroll) is router

    assert len(router.registrations) == 1
    assert frame.bindtags().count(router.bindtag) == 1

    with patch.object(sys, "platform", "darwin"):
        router._route(SimpleNamespace(widget=frame, delta=-12))
    assert calls == [(1, "units")]


def test_wheel_routes_from_dynamically_created_descendant(root):
    canvas = tkinter.Canvas(root, width=240, height=100, yscrollincrement=10)
    canvas.pack()
    content = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=content, anchor="nw")

    bind_scroll(content, canvas.yview_scroll)

    # These children intentionally appear after registration, matching cards
    # and advisor controls that are populated after the view is first drawn.
    labels = []
    for index in range(30):
        label = ttk.Label(content, text=f"Row {index}")
        label.pack()
        labels.append(label)

    root.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))
    before = canvas.yview()[0]

    with patch.object(sys, "platform", "darwin"):
        root._mtga_scroll_router._route(
            SimpleNamespace(widget=labels[-1], delta=-27)
        )

    assert canvas.yview()[0] > before


def test_scrolled_frame_routes_horizontal_wheel_from_card(root):
    frame = ScrolledFrame(root, width=260, height=100)
    frame.pack(fill="both", expand=True)
    card = ttk.Frame(frame.scrollable_frame, width=900, height=60)
    card.pack()
    card.pack_propagate(False)

    root.update_idletasks()
    frame.canvas.configure(scrollregion=frame.canvas.bbox("all"))
    before = frame.canvas.xview()[0]

    with patch.object(sys, "platform", "darwin"):
        root._mtga_scroll_router._route(SimpleNamespace(widget=card, delta=-8))

    assert frame.canvas.xview()[0] > before


def test_treeview_router_precedes_native_class_binding(root):
    tree = ModernTreeview(root, columns=("name",))
    tags = tree.bindtags()
    router_tag = root._mtga_scroll_router.bindtag

    assert tags.index(router_tag) < tags.index("Treeview")
