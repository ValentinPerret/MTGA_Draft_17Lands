import pytest
from unittest.mock import MagicMock, patch
from src.ui.app_controller import AppController


class TestAppController:
    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.root = MagicMock()
        app._initialized = True
        app._loading = False
        app._rebuilding_ui = False

        # Scanner & Orchestrator mocks
        scanner = MagicMock()
        scanner.lock.acquire.return_value = True
        scanner.retrieve_current_limited_event.return_value = ("OTJ", "PremierDraft")
        scanner.retrieve_current_pack_and_pick.return_value = (1, 1)

        mock_metrics = MagicMock()
        mock_metrics.get_metrics.return_value = (55.0, 4.0)
        scanner.retrieve_set_metrics.return_value = mock_metrics

        scanner.retrieve_tier_data.return_value = {}
        scanner.retrieve_taken_cards.return_value = []
        scanner.retrieve_current_pack_cards.return_value = []
        scanner.retrieve_current_missing_cards.return_value = []
        scanner.retrieve_current_picked_cards.return_value = []
        scanner.retrieve_draft_history.return_value = []

        orchestrator = MagicMock()
        orchestrator.scanner = scanner
        # Emulate empty queue
        orchestrator.update_queue.empty.return_value = True

        app.orchestrator = orchestrator
        return app

    def test_update_loop_processes_queue(self, mock_app):
        """Verify that the update loop drains the orchestrator's queue and applies UI updates."""
        controller = AppController(mock_app)

        # Inject messages into the mock queue
        import queue

        mock_queue = queue.Queue()
        mock_queue.put({"status": "Scanning Log..."})
        mock_queue.put("REFRESH")
        mock_app.orchestrator.update_queue = mock_queue

        # Temporarily disable the recursive `after` call to prevent infinite loops
        mock_app.root.after = MagicMock()

        with patch.object(controller, "refresh_ui_data") as mock_refresh:
            controller.update_loop()

            # Verify the status variable was updated
            mock_app.vars["status_text"].set.assert_any_call("Scanning Log...")

            # Verify a full UI refresh was triggered by the "REFRESH" signal
            mock_refresh.assert_called_once()

            # Verify the loop scheduled itself again
            mock_app.root.after.assert_called_once()

    def test_force_reload_triggers_deep_scan(self, mock_app):
        """Verify the Reload button aggressively wipes state and requests a full scan."""
        controller = AppController(mock_app)
        controller.force_reload()

        mock_app.vars["status_text"].set.assert_called_with("Deep Scanning Log...")
        mock_app.orchestrator.scanner.clear_draft.assert_called_with(True)
        mock_app.orchestrator.trigger_full_scan.assert_called_once()

    def test_scan_complete_hides_overlay_and_resets_status(self, mock_app):
        """A successful forced scan dismisses its blocking loading overlay."""
        controller = AppController(mock_app)

        import queue

        mock_queue = queue.Queue()
        mock_queue.put("REFRESH")
        mock_queue.put({"event": "scan_complete", "success": True})
        mock_app.orchestrator.update_queue = mock_queue
        mock_app.root.after = MagicMock()

        with patch.object(controller, "refresh_ui_data") as mock_refresh:
            controller.update_loop()

        mock_refresh.assert_called_once()
        mock_app.loading_overlay.hide.assert_called_once()
        mock_app.vars["status_text"].set.assert_called_with("Ready")

    def test_unchanged_scan_still_hides_overlay(self, mock_app):
        """Completion cannot depend on the scanner finding new draft state."""
        controller = AppController(mock_app)

        import queue

        mock_queue = queue.Queue()
        mock_queue.put({"event": "scan_complete", "success": True})
        mock_app.orchestrator.update_queue = mock_queue
        mock_app.root.after = MagicMock()

        with patch.object(controller, "refresh_ui_data") as mock_refresh:
            controller.update_loop()

        mock_refresh.assert_not_called()
        mock_app.loading_overlay.hide.assert_called_once()
        mock_app.vars["status_text"].set.assert_called_with("Ready")

    def test_failed_log_operation_releases_overlay_and_history_control(self, mock_app):
        controller = AppController(mock_app)

        import queue

        mock_queue = queue.Queue()
        mock_queue.put(
            {
                "event": "operation_complete",
                "operation": "log_scan",
                "success": False,
                "status": "Draft log failed: unreadable file",
            }
        )
        mock_app.orchestrator.update_queue = mock_queue
        mock_app.root.after = MagicMock()

        controller.update_loop()

        mock_app.loading_overlay.hide.assert_called_once()
        mock_app.top_bar.set_history_dropdown_state.assert_called_with("readonly")
        mock_app.vars["status_text"].set.assert_called_with(
            "Draft log failed: unreadable file"
        )

    @patch("src.ui.app_controller.AppUpdate")
    @patch("src.ui.app_controller.threading.Thread")
    def test_check_background_updates(self, mock_thread, mock_updater_cls, mock_app):
        """Verify background updates fire asynchronously and report to the UI."""
        controller = AppController(mock_app)

        # Mocks
        mock_instance = mock_updater_cls.return_value
        mock_instance.retrieve_file_version.return_value = ("99.99", "url")

        # Execute background check (triggers thread and notifications)
        controller.check_background_updates()

        mock_app.notifications.check_dataset.assert_called_once()
        mock_thread.assert_called_once()

    def test_on_dataset_update_queues_background_index(self, mock_app):
        """A downloaded dataset is indexed without blocking the UI thread."""
        controller = AppController(mock_app)

        mock_app.configuration.card_data.latest_dataset = "M10_Data.json"

        with patch("os.path.exists", return_value=True):
            controller.on_dataset_update()

        mock_app.orchestrator.request_dataset_load.assert_called_once()
        requested_path = mock_app.orchestrator.request_dataset_load.call_args.args[0]
        assert requested_path.endswith("M10_Data.json")

    def test_refresh_ui_data_skips_if_locked(self, mock_app):
        """Verify the UI skips rendering if the background thread holds the scanner lock."""
        controller = AppController(mock_app)

        # Simulate background thread currently writing data
        mock_app.orchestrator.scanner.lock.acquire.return_value = False

        # Capture the after() call
        mock_app.root.after = MagicMock()

        controller.refresh_ui_data()

        # It should NOT have accessed any scanner data
        mock_app.orchestrator.scanner.retrieve_current_limited_event.assert_not_called()

        # It SHOULD have rescheduled itself to try again in 100ms
        mock_app.root.after.assert_called_once_with(100, controller.refresh_ui_data)

    def test_scanner_backed_tabs_follow_practice_scanner(self, mock_app):
        """A scanner swap must update every tab that reads pool/deck state."""
        controller = AppController(mock_app)
        practice_scanner = MagicMock(name="practice_scanner")

        panels = [
            mock_app.panel_taken,
            mock_app.panel_suggest,
            mock_app.panel_custom,
            mock_app.panel_compare,
        ]
        for panel in panels:
            panel.draft = MagicMock(name="live_scanner")

        controller._bind_scanner_backed_views(practice_scanner)

        assert all(panel.draft is practice_scanner for panel in panels)
