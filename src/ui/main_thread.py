"""Small queue-backed bridge for delivering worker results to Tk safely."""

import logging
import queue
import threading
import tkinter


logger = logging.getLogger(__name__)


class MainThreadDispatcher:
    """Run callbacks on Tk's event thread without calling Tk from workers.

    ``widget.after`` is itself a Tcl call and is not safe to invoke from a
    Python worker thread on macOS.  Workers only put ordinary Python objects in
    this queue; a poll created on the UI thread performs the actual callbacks.
    """

    def __init__(self, widget, poll_ms=50):
        self.widget = widget
        self.poll_ms = poll_ms
        self._callbacks = queue.Queue()
        self._after_id = widget.after(self.poll_ms, self._poll)
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event):
        if event.widget is self.widget:
            self.close()

    def post(self, callback, *args, **kwargs):
        """Queue a callback. This method is safe to call from any thread."""
        if threading.current_thread() is threading.main_thread():
            callback(*args, **kwargs)
            return
        self._callbacks.put((callback, args, kwargs))

    def _poll(self):
        self._after_id = None
        try:
            if not self.widget.winfo_exists():
                return
        except tkinter.TclError:
            return

        while True:
            try:
                callback, args, kwargs = self._callbacks.get_nowait()
            except queue.Empty:
                break
            try:
                callback(*args, **kwargs)
            except tkinter.TclError:
                # A result may arrive just after its destination was closed.
                continue
            except Exception:
                logger.exception("Error applying background UI result")

        try:
            self._after_id = self.widget.after(self.poll_ms, self._poll)
        except tkinter.TclError:
            self._after_id = None

    def close(self):
        """Stop polling when a long-lived owner is explicitly torn down."""
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tkinter.TclError:
                pass
            self._after_id = None
