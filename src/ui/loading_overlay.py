"""
src/ui/loading_overlay.py
Provides a blocking, visually appealing loading screen for heavy background operations.
"""

import time
import tkinter
from tkinter import ttk
from src.ui.styles import Theme


class LoadingOverlay(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.configure(style="TFrame")
        self._show_after_id = None
        self._elapsed_after_id = None
        self._started_at = None
        self._visible = False

        self.center_box = ttk.Frame(
            self, padding=Theme.scaled_val(40), style="Card.TFrame"
        )
        self.center_box.place(relx=0.5, rely=0.45, anchor="center")

        self.title_lbl = ttk.Label(
            self.center_box,
            text="Loading Draft",
            font=Theme.scaled_font(16, "bold"),
            bootstyle="primary",
        )
        self.title_lbl.pack(pady=(0, Theme.scaled_val(10)))

        self.status_lbl = ttk.Label(
            self.center_box, text="Initializing...", font=Theme.scaled_font(11)
        )
        self.status_lbl.pack(pady=(0, Theme.scaled_val(8)))

        self.detail_lbl = ttk.Label(
            self.center_box,
            text="",
            font=Theme.scaled_font(9),
            style="Muted.TLabel",
            justify="center",
            wraplength=Theme.scaled_val(420),
        )
        self.detail_lbl.pack(pady=(0, Theme.scaled_val(12)))

        self.elapsed_lbl = ttk.Label(
            self.center_box,
            text="",
            font=Theme.scaled_font(9),
            style="Muted.TLabel",
        )
        self.elapsed_lbl.pack(pady=(0, Theme.scaled_val(12)))

        self.progress = ttk.Progressbar(
            self.center_box, mode="indeterminate", length=Theme.scaled_val(300)
        )
        self.progress.pack()

    def show(self, title, detail=None, delay_ms=250):
        """Display after a short delay so quick operations do not flash."""
        self.hide()
        self.title_lbl.config(text=title)
        self.status_lbl.config(text="Starting...")
        self.detail_lbl.config(text=detail or "")
        self.elapsed_lbl.config(text="")
        self._started_at = time.monotonic()

        if delay_ms <= 0:
            self._reveal()
        else:
            self._show_after_id = self.after(delay_ms, self._reveal)

    def _reveal(self):
        self._show_after_id = None
        self._visible = True
        self.progress.start(15)
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        self._update_elapsed()

    def _update_elapsed(self):
        self._elapsed_after_id = None
        if not self._visible or self._started_at is None:
            return
        elapsed = max(0.0, time.monotonic() - self._started_at)
        self.elapsed_lbl.config(text=f"Working for {elapsed:.1f}s")
        self._elapsed_after_id = self.after(250, self._update_elapsed)

    def hide(self):
        """Hides the overlay and stops animations."""
        if self._show_after_id is not None:
            try:
                self.after_cancel(self._show_after_id)
            except tkinter.TclError:
                pass
            self._show_after_id = None
        if self._elapsed_after_id is not None:
            try:
                self.after_cancel(self._elapsed_after_id)
            except tkinter.TclError:
                pass
            self._elapsed_after_id = None
        self._visible = False
        self._started_at = None
        self.progress.stop()
        self.place_forget()

    def update_status(self, text, detail=None):
        """Updates the sub-text of the loading screen."""
        self.status_lbl.config(text=text)
        if detail is not None:
            self.detail_lbl.config(text=detail)
