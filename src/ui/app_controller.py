"""
src/ui/app_controller.py
The Application Controller. Manages the event loop, synchronization with the
DraftOrchestrator, background tasks, and executes the mathematical evaluation engines.
"""

import logging
import queue
import os
import sys
import threading
from typing import Dict

from src import constants
from src.configuration import write_configuration
from src.advisor.service import AdvisorService
from src.advisor_v2.codex_reviewer import (
    CodexReviewCoordinator,
    apply_codex_review,
    build_review_request,
)
from src.signals import SignalCalculator
from src.card_logic import filter_options, get_deck_metrics
from src.app_update import AppUpdate

logger = logging.getLogger(__name__)


class AppController:
    """Manages the lifecycle, data generation, and UI polling."""

    def __init__(self, app_context):
        self.app = app_context
        self.root = app_context.root
        self.config = app_context.configuration
        self.orchestrator = app_context.orchestrator
        self.previous_timestamp = 0
        self._update_task_id = None
        self._manual_model_review_requested = False
        self.codex_reviews = CodexReviewCoordinator(self.config.model_assistance)

    def start_boot_sync(self):
        """Phase 1: Immediate synchronization of critical UI components."""
        if not self.app._initialized:
            return

        try:
            self.app.vars["status_text"].set("Syncing with Arena...")
            self.app.layout_manager.restore_window_state()

            # START THE ENGINE
            self.orchestrator.start()

            try:
                self.app.top_bar.update_data_sources()
                self.app.top_bar.update_deck_filter_options()
            except Exception as e:
                logger.error(f"Dropdown sync failed: {e}", exc_info=True)

            self.refresh_ui_data()
            self.root.after(500, self.execute_deep_sync)
            self.schedule_update()

        finally:
            self.app._loading = False

    def execute_deep_sync(self):
        """Phase 2: Populate only the tool the user can currently see."""
        self.app.vars["status_text"].set("Ready")

        if not self.config.card_data.latest_dataset:
            self.app.notebook.select(self.app.panel_data)
        elif os.path.basename(self.orchestrator.scanner.arena_file).startswith(
            "DraftLog_"
        ):
            self.app.notebook.select(self.app.panel_suggest)

        self.app.layout_manager.refresh_active_panel()

        self.root.after(1500, self.check_background_updates)

    def check_background_updates(self):
        """Executes non-critical network checks (e.g., GitHub Releases)."""
        if not hasattr(self.app, "notifications") or self.app.notifications is None:
            return

        def _check_app():
            try:
                v, _ = AppUpdate().retrieve_file_version()
                if v and float(v) > float(constants.APPLICATION_VERSION):
                    self.orchestrator.update_queue.put(
                        {"event": "app_update_available", "version": v}
                    )
            except Exception as e:
                logger.error(f"App update check failed: {e}")

        threading.Thread(target=_check_app, daemon=True).start()

        try:
            self.app.notifications.check_dataset()
        except Exception as e:
            logger.error(f"Dataset update check failed: {e}")

    def update_loop(self):
        """UI Poll Loop: Checks the orchestrator's queue for updates."""
        if not self.root.winfo_exists():
            return

        try:
            is_test = "pytest" in sys.modules
            if not self.orchestrator.is_alive() or is_test:
                self.orchestrator.step_process()

            update_detected = False
            operation_completed = False
            operation_succeeded = True
            completion_status = "Ready"
            while True:
                try:
                    msg = self.orchestrator.update_queue.get_nowait()
                    if isinstance(msg, dict) and msg.get("event") in (
                        "scan_complete",
                        "operation_complete",
                    ):
                        operation_completed = True
                        operation_succeeded = bool(msg.get("success", True))
                        completion_status = msg.get(
                            "status",
                            "Ready" if operation_succeeded else "Operation failed",
                        )
                    elif (
                        isinstance(msg, dict)
                        and msg.get("event") == "app_update_available"
                    ):
                        self.app.menu_bar.notify_app_update(msg.get("version"))
                    elif isinstance(msg, dict) and "status" in msg:
                        self.app.vars["status_text"].set(msg["status"])
                        if hasattr(self.app, "loading_overlay"):
                            self.app.loading_overlay.update_status(
                                msg["status"], msg.get("detail")
                            )
                    elif msg == "REFRESH":
                        update_detected = True
                except queue.Empty:
                    break

            # Worker threads only signal through a queue. Tk calls and UI refreshes
            # always remain on this main loop.
            if self.codex_reviews.poll_completed():
                update_detected = True

            if update_detected:
                self.app.top_bar.set_history_dropdown_state("readonly")
                self.app.top_bar.update_data_sources()
                self.app.top_bar.update_deck_filter_options()
                self.refresh_ui_data()
                if is_test:
                    self.root.update()

            if operation_completed:
                self.app.top_bar.set_history_dropdown_state("readonly")
                if hasattr(self.app, "loading_overlay"):
                    self.app.loading_overlay.hide()
                self.app.vars["status_text"].set(completion_status)

            try:
                ts = os.stat(self.orchestrator.scanner.arena_file).st_mtime
                self.app.top_bar.update_status_dot(ts, self.previous_timestamp)
                self.previous_timestamp = ts
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Logic Step Error: {e}")
            if hasattr(self.app, "loading_overlay"):
                self.app.loading_overlay.hide()

        self.schedule_update()

    def schedule_update(self):
        self._update_task_id = self.root.after(100, self.update_loop)

    def force_reload(self):
        """Forces a deep scan of the active Arena Log."""
        self.app.vars["status_text"].set("Deep Scanning Log...")
        if hasattr(self.app, "loading_overlay"):
            self.app.loading_overlay.show("Reloading Application State")
            self.app.loading_overlay.update_status(
                "Deep scanning Player.log...",
                "Reading the log from the beginning and reconstructing every pack and pick.",
            )

        with self.orchestrator.scanner.lock:
            self.orchestrator.scanner.clear_draft(True)
            if (
                hasattr(self.orchestrator.scanner, "set_data")
                and self.orchestrator.scanner.set_data
            ):
                self.orchestrator.scanner.set_data.unknown_id_cache.clear()

        self.orchestrator.trigger_full_scan()

    def request_deeper_analysis(self):
        """Request one user-initiated Codex pass on the current pick."""
        if self.config.settings.advisor_engine != "contextual_v2":
            self.app.vars["status_text"].set(
                "Select the contextual advisor before requesting Codex review."
            )
            return
        if not self.config.model_assistance.enabled:
            self.app.vars["status_text"].set(
                "Enable optional Codex review in Preferences first."
            )
            return
        self._manual_model_review_requested = True
        self.app.vars["status_text"].set("Codex review requested…")
        self.refresh_ui_data()

    def on_dataset_update(self):
        latest_file = self.config.card_data.latest_dataset
        if latest_file:
            from src.constants import SETS_FOLDER

            full_path = os.path.join(SETS_FOLDER, latest_file)
            if os.path.exists(full_path):
                if hasattr(self.app, "loading_overlay"):
                    self.app.loading_overlay.show("Indexing downloaded dataset")
                    self.app.loading_overlay.update_status(
                        "Reading card ratings...",
                        "The download is complete; recommendations are being "
                        "rebuilt from the new data.",
                    )
                self.orchestrator.request_dataset_load(full_path)

    def refresh_ui_data(self):
        """Core UI Synchronization Logic. Aggregates data, runs math engines, and updates the Views."""
        if not self.app._initialized or self.app._rebuilding_ui:
            return

        # Keep one scanner for the entire render. Practice mode and live-draft
        # detection can swap ``orchestrator.scanner`` between event-loop ticks;
        # mixing both scanners in one snapshot produces empty or inconsistent
        # views and can even release the wrong lock.
        scanner = self.orchestrator.scanner
        lock_acquired = scanner.lock.acquire(blocking=False)
        if not lock_acquired:
            self.root.after(100, self.refresh_ui_data)
            return

        try:
            # SNAPSHOT STATE
            es, et = scanner.retrieve_current_limited_event()
            pk, pi = scanner.retrieve_current_pack_and_pick()
            metrics = scanner.retrieve_set_metrics()
            tier_data = scanner.retrieve_tier_data()
            taken_cards = scanner.retrieve_taken_cards()
            pack_cards = scanner.retrieve_current_pack_cards()
            missing_cards = scanner.retrieve_current_missing_cards()
            current_picked_cards = scanner.retrieve_current_picked_cards()
            history = scanner.retrieve_draft_history()
            draft_id = scanner.current_draft_id
            start_time = scanner.draft_start_time
            event_string = scanner.event_string
        finally:
            scanner.lock.release()

        self._bind_scanner_backed_views(scanner)

        # ADVISOR & SIGNAL MATH
        sig_calc = SignalCalculator(metrics)
        scores = {c: 0.0 for c in constants.CARD_COLORS}
        for entry in history:
            if entry["Pack"] == 2:
                continue
            h_pack = scanner.set_data.get_data_by_id(entry["Cards"])
            for c, v in sig_calc.calculate_pack_signals(h_pack, entry["Pick"]).items():
                scores[c] += v

        try:
            detailed_logs = scanner.detailed_logs_enabled()
        except Exception:
            detailed_logs = None

        manual_model_review = self._manual_model_review_requested
        self._manual_model_review_requested = False

        # Pass signals securely into Advisor
        advisor = AdvisorService(
            metrics,
            taken_cards,
            signals=scores,
            configuration=self.config,
            event_name=event_string,
            draft_history=history,
        )
        recommendations = advisor.evaluate_pack(
            pack_cards,
            pi,
            current_pack=pk,
            manual_model_review=manual_model_review,
            state_complete=(detailed_logs is not False and pk > 0 and pi > 0),
        )
        if detailed_logs is False:
            warning = (
                "Arena Detailed Logs are disabled; picks and the reconstructed pool may be incomplete."
            )
            for recommendation in recommendations:
                if warning not in recommendation.data_caveats:
                    recommendation.data_caveats.insert(0, warning)

        self._apply_optional_codex_review(
            advisor=advisor,
            recommendations=recommendations,
            pack_cards=pack_cards,
            taken_cards=taken_cards,
            event_string=event_string,
            pack_number=pk,
            pick_number=pi,
            draft_key=str(draft_id or f"{event_string}:{start_time}"),
            manual=manual_model_review,
        )

        # UPDATE UI STATE
        if pk > 0:
            self.app.vars["status_text"].set(f"Pack {pk} Pick {pi}")
            if hasattr(self.app.top_bar, "lbl_status"):
                self.app.top_bar.lbl_status.configure(bootstyle="success")
        else:
            self.app.vars["status_text"].set("Waiting for draft...")
            if hasattr(self.app.top_bar, "lbl_status"):
                self.app.top_bar.lbl_status.configure(bootstyle="secondary")

        colors = filter_options(
            taken_cards, self.config.settings.deck_filter, metrics, self.config
        )

        # PUSH DATA TO VIEWS
        self.app.top_bar.update_auto_detect_label(colors)

        self.app.dashboard._current_event_set = es
        self.app.dashboard._current_event_type = et
        self.app.dashboard._current_pack = pk
        self.app.dashboard._current_pick = pi

        self.app.update_session_info(event_string, draft_id, start_time)
        self.app.dashboard.update_recommendations(recommendations)
        self.app.dashboard.update_signals(scores)

        self.app.dashboard.update_pack_data(
            pack_cards,
            colors,
            metrics,
            tier_data,
            pi,
            "pack",
            recommendations,
            current_picked_cards,
        )
        self.app.dashboard.update_pack_data(
            missing_cards, colors, metrics, tier_data, pi, "missing"
        )

        deck_metrics = get_deck_metrics(taken_cards)
        self.app.dashboard.update_stats(deck_metrics.distribution_all)
        self.app.dashboard.update_deck_balance(taken_cards)
        self.app.dashboard.orchestrator = self.orchestrator
        self.app.dashboard.update_pool_summary(taken_cards, metrics, draft_id)

        if self.app.overlay_window:
            self.app.overlay_window.update_data(
                pack_cards,
                colors,
                metrics,
                tier_data,
                pi,
                recommendations,
                current_picked_cards,
                scores,
            )

        # Heavy tools are refreshed lazily. Rebuilding hidden deck simulations,
        # comparisons and visual card grids on every log tick starves Tk's event
        # loop and makes card previews appear intermittent.
        self.app.layout_manager.refresh_active_panel()

        self.app.current_pack_data = pack_cards
        self.app.current_missing_data = missing_cards

    def _bind_scanner_backed_views(self, scanner):
        """Point data tabs at the scanner used for this refresh snapshot.

        The panels are constructed with the live scanner, while practice mode
        intentionally swaps in an isolated scanner. Rebinding here keeps the
        card pool and deck tools aligned with the dashboard in both directions.
        """
        for panel_name in (
            "panel_taken",
            "panel_suggest",
            "panel_custom",
            "panel_compare",
        ):
            panel = getattr(self.app, panel_name, None)
            if panel is not None and hasattr(panel, "draft"):
                panel.draft = scanner

    def _apply_optional_codex_review(
        self,
        *,
        advisor,
        recommendations,
        pack_cards,
        taken_cards,
        event_string,
        pack_number,
        pick_number,
        draft_key,
        manual,
    ):
        config = self.config.model_assistance
        decision = advisor.last_model_decision
        if (
            self.config.settings.advisor_engine != "contextual_v2"
            or not config.enabled
            or decision is None
            or not recommendations
        ):
            return

        request = build_review_request(
            event_name=event_string,
            pack_number=pack_number,
            pick_number=pick_number,
            pack_cards=pack_cards,
            pool_cards=taken_cards,
            recommendations=recommendations,
        )
        if request is None:
            if manual:
                recommendations[0].data_caveats.append(
                    "Codex review needs stable Arena card IDs; local ranking is shown."
                )
            return

        outcome = self.codex_reviews.outcome(request.digest)
        if outcome is not None:
            if outcome.status == "success" and outcome.review is not None:
                application = apply_codex_review(recommendations, outcome.review)
                if application.accepted:
                    self.codex_reviews.mark_applied(
                        request.digest, changed_order=application.changed_order
                    )
            elif outcome.detail:
                recommendations[0].data_caveats.append(
                    f"Codex review unavailable ({outcome.detail}); local ranking is shown."
                )
            return

        if not decision.should_call:
            if manual:
                recommendations[0].data_caveats.append(
                    f"Codex review skipped: {decision.reason}."
                )
            return
        request_status = self.codex_reviews.request(
            request,
            draft_key=draft_key,
            routing_reason=decision.reason,
            manual=manual,
        )
        if request_status in {"started", "in_flight"}:
            recommendations[0].data_caveats.append(
                "Codex review is running in the background; local ranking is shown now."
            )
