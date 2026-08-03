import threading
import tkinter

from src.ui.main_thread import MainThreadDispatcher


def test_worker_result_is_applied_by_tk_event_thread():
    root = tkinter.Tk()
    try:
        dispatcher = MainThreadDispatcher(root, poll_ms=5)
        applied_on = []

        worker = threading.Thread(
            target=lambda: dispatcher.post(
                lambda: applied_on.append(threading.current_thread())
            )
        )
        worker.start()
        worker.join()

        assert applied_on == []
        root.after(20, root.quit)
        root.mainloop()

        assert applied_on == [threading.main_thread()]
    finally:
        root.destroy()
