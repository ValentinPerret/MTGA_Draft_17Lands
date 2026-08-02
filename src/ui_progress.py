# -*- coding: utf-8 -*-
import threading
from typing import Callable, Optional


class UIProgress:
    """Handles UI updates and progress tracking with thread safety."""

    def __init__(
        self,
        progress=None,
        status=None,
        ui=None,
        initial_progress: int = 0,
        update_callback: Optional[Callable[[str, object], None]] = None,
    ):
        self.progress = progress
        self.initial_progress = initial_progress
        self.status = status
        self.ui = ui
        self.update_callback = update_callback

    def _update_ui(self):
        """Update the UI safely without triggering event loop re-entrancy."""
        if self.ui and self.ui.winfo_exists():
            self.ui.update_idletasks()

    def _update_status(self, message: str):
        """Update status message safely across threads."""
        if self.update_callback:
            self.update_callback("status", message)
            return

        if self.status:
            if threading.current_thread() is threading.main_thread():
                self.status.set(message)
                self._update_ui()

    def _update_progress(self, value: float, increment: bool = True):
        """Update progress bar value safely across threads."""
        if self.update_callback:
            if increment:
                self.initial_progress += value
            else:
                self.initial_progress = value
            self.update_callback("progress", self.initial_progress)
            return

        if self.progress:

            def _apply():
                if (
                    not hasattr(self.progress, "winfo_exists")
                    or not self.progress.winfo_exists()
                ):
                    return
                if increment:
                    self.initial_progress += value
                    self.progress["value"] = self.initial_progress
                else:
                    self.progress["value"] = value
                self._update_ui()

            if threading.current_thread() is threading.main_thread():
                _apply()
