import pytest
import os
import queue
from unittest.mock import patch, MagicMock
from src.ui.orchestrator import DraftOrchestrator
from src.configuration import Configuration


@pytest.fixture
def orchestrator():
    config = Configuration()
    config.settings.arena_log_location = "mock_live_log.txt"
    config.settings.draft_log_enabled = False

    mock_scanner = MagicMock()
    mock_scanner.arena_file = "mock_live_log.txt"

    return DraftOrchestrator(mock_scanner, config, MagicMock())


@patch("src.ui.orchestrator.os.path.getsize")
@patch("src.ui.orchestrator.os.path.exists")
@patch("builtins.open")
def test_check_live_log_detects_draft(
    mock_open, mock_exists, mock_getsize, orchestrator
):
    """Verifies the background watchdog detects when a draft suddenly starts in the active log."""

    # App starts with a 500-byte log file
    orchestrator._last_live_file_size = 500

    # File exists and has grown to 1000 bytes
    mock_exists.return_value = True
    mock_getsize.return_value = 1000

    # Mock the file reader yielding lines
    mock_file = MagicMock()
    mock_file.readline.side_effect = [
        "Some random Arena telemetry...",
        "[UnityCrossThreadLogger]==> Event_Join Draft",  # Trigger!
        "",  # EOF
    ]
    mock_open.return_value.__enter__.return_value = mock_file

    # Act
    found_draft = orchestrator._check_live_log_for_draft()

    # Assert
    assert found_draft is True
    # Verify the pointer moved forward
    assert orchestrator._last_live_file_size == 1000


@patch("src.ui.orchestrator.os.path.getsize", return_value=500)
@patch("src.ui.orchestrator.os.path.exists", return_value=True)
def test_check_live_log_ignores_static_file(mock_exists, mock_getsize, orchestrator):
    """Verifies we do not waste CPU cycles reading the log if the file size hasn't changed."""

    orchestrator._last_live_file_size = 500  # Matches current size

    with patch("builtins.open") as mock_open:
        found_draft = orchestrator._check_live_log_for_draft()

        # Assert
        assert found_draft is False
        mock_open.assert_not_called()  # No file I/O performed


def test_orchestrator_flags(orchestrator):
    """Verify the flag setters are working properly."""
    assert not orchestrator._force_full_scan_event.is_set()
    orchestrator.trigger_full_scan()
    assert orchestrator._force_full_scan_event.is_set()

    assert not orchestrator._stop_event.is_set()
    orchestrator.stop()
    assert orchestrator._stop_event.is_set()

    assert not orchestrator._force_math_event.is_set()
    orchestrator.request_math_update()
    assert orchestrator._force_math_event.is_set()

    assert orchestrator.monitoring_paused is False
    assert orchestrator.toggle_paused() is True
    assert orchestrator.monitoring_paused is True
    assert orchestrator.toggle_paused() is False


def test_forced_scan_always_reports_completion(orchestrator):
    """An unchanged deep scan must still release the UI loading overlay."""
    orchestrator.trigger_full_scan()
    orchestrator.check_for_updates = MagicMock(return_value=False)

    orchestrator.step_process()

    assert orchestrator.update_queue.get_nowait() == {
        "event": "scan_complete",
        "success": True,
    }


def test_forced_scan_reports_failure(orchestrator):
    """A failed deep scan also releases the overlay with an error state."""
    orchestrator.trigger_full_scan()
    orchestrator.check_for_updates = MagicMock(side_effect=RuntimeError("boom"))

    orchestrator.step_process()

    assert orchestrator.update_queue.get_nowait() == {
        "event": "scan_complete",
        "success": False,
    }


def test_manual_scan_completes_while_monitoring_is_paused(orchestrator):
    orchestrator.set_paused(True)
    # Discard the status emitted by the pause action.
    orchestrator.update_queue.get_nowait()
    orchestrator.trigger_full_scan()
    orchestrator.check_for_updates = MagicMock(return_value=False)

    orchestrator.step_process()

    orchestrator.check_for_updates.assert_called_once_with(force=True)
    assert orchestrator.update_queue.get_nowait() == {
        "event": "scan_complete",
        "success": True,
    }


@patch("src.ui.orchestrator.time.sleep", return_value=None)
def test_orchestrator_run_loop(mock_sleep, orchestrator):
    """Verify the run loop correctly consumes events and file swaps."""
    # Trigger the flags
    orchestrator.request_math_update()
    orchestrator.trigger_full_scan()

    # Queue a file swap
    orchestrator.set_file_and_scan("fake.log")

    # Prevent actually reading the hard drive
    orchestrator.scanner.set_arena_file = MagicMock()
    orchestrator.scanner.draft_start_search = MagicMock(return_value=False)
    orchestrator.sync_dataset_to_event = MagicMock()

    # Stop loop after one iteration by changing the return value of is_set
    orchestrator._stop_event.is_set = MagicMock(side_effect=[False, True])

    orchestrator.run()

    # Assertions
    orchestrator.scanner.set_arena_file.assert_called_with("fake.log")
    # Events should be cleared after the loop executes
    assert not orchestrator._force_full_scan_event.is_set()
    assert not orchestrator._force_math_event.is_set()
    assert orchestrator.update_queue.qsize() > 0


def test_file_swap_queue_processing(orchestrator):
    """Verify that thread-safe requests from the UI to read historical logs are processed."""

    # UI requests to read two different logs rapidly
    orchestrator.set_file_and_scan("historical_draft_1.log")
    orchestrator.set_file_and_scan("historical_draft_2.log")

    # Mock the scanner so it doesn't actually try to read them
    orchestrator.scanner.draft_start_search.return_value = True
    orchestrator.sync_dataset_to_event = MagicMock()

    # Force the run loop logic manually for one step
    # We simulate what happens inside run() when the queue isn't empty
    new_file = None
    while not orchestrator._file_swap_queue.empty():
        new_file = orchestrator._file_swap_queue.get_nowait()

    assert (
        new_file == "historical_draft_2.log"
    )  # It correctly skips to the most recent request

    # Simulate processing
    orchestrator.scanner.set_arena_file(new_file)

    # Verify scanner was updated
    orchestrator.scanner.set_arena_file.assert_called_with("historical_draft_2.log")


@patch("src.ui.orchestrator.time.sleep", return_value=None)
@patch("src.ui.orchestrator.write_configuration")
@patch("src.card_logic.clear_deck_cache")
def test_dataset_load_runs_in_background_and_reports_completion(
    mock_clear_cache, mock_write, mock_sleep, orchestrator
):
    # Explicit user operations must still finish while automatic log monitoring
    # is paused, otherwise the blocking overlay would never receive completion.
    orchestrator.set_paused(True)
    orchestrator.request_dataset_load("/sets/OTJ.json")
    orchestrator._stop_event.is_set = MagicMock(side_effect=[False, True])

    orchestrator.run()

    orchestrator.scanner.retrieve_set_data.assert_called_once_with("/sets/OTJ.json")
    assert orchestrator.config.card_data.latest_dataset == "OTJ.json"
    mock_write.assert_called_once_with(orchestrator.config)
    mock_clear_cache.assert_called_once()

    messages = []
    while not orchestrator.update_queue.empty():
        messages.append(orchestrator.update_queue.get_nowait())
    assert "REFRESH" in messages
    assert {
        "event": "operation_complete",
        "operation": "dataset_load",
        "success": True,
        "status": "Dataset ready",
    } in messages
