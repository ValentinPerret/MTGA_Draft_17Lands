import pytest
import threading
from unittest.mock import MagicMock, patch
from src.ui_progress import UIProgress


def test_ui_progress_init():
    progress = UIProgress(progress=MagicMock(), status=MagicMock(), ui=MagicMock())
    assert progress.initial_progress == 0


def test_update_status_main_thread():
    status_mock = MagicMock()
    ui_mock = MagicMock()
    progress = UIProgress(status=status_mock, ui=ui_mock)

    with patch("src.ui_progress.threading.current_thread") as mock_thread:
        mock_thread.return_value = threading.main_thread()
        progress._update_status("Loading...")
        status_mock.set.assert_called_with("Loading...")
        ui_mock.update_idletasks.assert_called()


def test_update_status_background_thread():
    status_mock = MagicMock()
    ui_mock = MagicMock()
    update_callback = MagicMock()
    progress = UIProgress(
        status=status_mock, ui=ui_mock, update_callback=update_callback
    )

    with patch("src.ui_progress.threading.current_thread") as mock_thread:
        # Mocking to simulate a background thread
        mock_thread.return_value = MagicMock()
        progress._update_status("Loading...")
        update_callback.assert_called_once_with("status", "Loading...")
        ui_mock.after.assert_not_called()
        status_mock.set.assert_not_called()


def test_update_progress_main_thread():
    progress_mock = MagicMock()
    ui_mock = MagicMock()
    progress_obj = UIProgress(progress=progress_mock, ui=ui_mock)

    with patch("src.ui_progress.threading.current_thread") as mock_thread:
        mock_thread.return_value = threading.main_thread()
        progress_obj._update_progress(10.0, increment=True)
        assert progress_obj.initial_progress == 10.0

        progress_obj._update_progress(50.0, increment=False)
        assert progress_mock.__setitem__.call_args[0][1] == 50.0


def test_update_progress_callback_reports_absolute_value_without_tk_calls():
    progress_mock = MagicMock()
    ui_mock = MagicMock()
    update_callback = MagicMock()
    progress_obj = UIProgress(
        progress=progress_mock, ui=ui_mock, update_callback=update_callback
    )

    progress_obj._update_progress(10.0, increment=True)
    progress_obj._update_progress(5.0, increment=True)

    assert update_callback.call_args_list == [
        (("progress", 10.0),),
        (("progress", 15.0),),
    ]
    ui_mock.after.assert_not_called()
    progress_mock.__setitem__.assert_not_called()
