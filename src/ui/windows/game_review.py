"""Post-match Limited gameplay review and longitudinal coaching UI."""

from __future__ import annotations

import os
import queue
import threading
import tkinter
from datetime import datetime
from tkinter import ttk
from typing import Dict, Optional

from src import constants
from src.game_review.analyzer import analyze_game
from src.game_review.codex_reviewer import CodexGameReviewError, CodexGameReviewer
from src.game_review.deck_advisor import build_game_indicators
from src.game_review.parser import ArenaGameLogParser, LocalCardCatalog
from src.game_review.store import GameReviewStore, match_key
from src.ui.components import AutoScrollbar
from src.ui.styles import Theme
from src.utils import bind_scroll


class GameReviewPanel(ttk.Frame):
    def __init__(
        self,
        parent,
        scanner,
        configuration,
        *,
        parser=None,
        store=None,
        reviewer=None,
    ):
        super().__init__(parent)
        self.scanner = scanner
        self.configuration = configuration
        sets_location = getattr(scanner, "sets_location", constants.SETS_FOLDER)
        if not isinstance(sets_location, (str, os.PathLike)):
            sets_location = constants.SETS_FOLDER
        self.parser = parser or ArenaGameLogParser(
            LocalCardCatalog(
                sets_location,
                getattr(scanner, "set_data", None),
            )
        )
        self.store = store or GameReviewStore()
        self.reviewer = reviewer or CodexGameReviewer()
        self.games: Dict[str, object] = {}
        self._selected_key = ""
        self._last_log_signature = None
        self._task_token = 0
        self._results = queue.Queue()
        self._poll_id = None
        self._scan_running = False
        self._review_running = False
        self._review_wrap_labels = []
        self._deck_wrap_labels = []
        self._build_ui()
        self._poll_id = self.after(100, self._poll_results)
        self._render_progress()

    def destroy(self):
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tkinter.TclError:
                pass
            self._poll_id = None
        super().destroy()

    def _build_ui(self):
        header = ttk.Frame(self, style="Card.TFrame", padding=Theme.scaled_val(10))
        header.pack(fill="x", pady=Theme.scaled_val((0, 6)))

        title_group = ttk.Frame(header, style="Card.TFrame")
        title_group.pack(side="left", fill="x", expand=True)
        ttk.Label(
            title_group,
            text="GAME REVIEW",
            font=Theme.scaled_font(15, "bold"),
            bootstyle="primary",
        ).pack(anchor="w")
        ttk.Label(
            title_group,
            text="Evidence-based coaching from your local Arena detailed logs",
            font=Theme.scaled_font(9),
            bootstyle="secondary",
        ).pack(anchor="w")

        self.btn_codex = ttk.Button(
            header,
            text="Analyze Game + Deck",
            bootstyle="success",
            command=self._analyze_selected,
            state="disabled",
        )
        self.btn_codex.pack(side="right", padx=Theme.scaled_val(5))
        self.btn_scan = ttk.Button(
            header,
            text="Scan Player.log",
            bootstyle="info-outline",
            command=lambda: self._start_scan(force=True),
        )
        self.btn_scan.pack(side="right", padx=Theme.scaled_val(5))

        status_bar = ttk.Frame(self, padding=Theme.scaled_val((8, 4)))
        status_bar.pack(fill="x")
        self.lbl_status = ttk.Label(
            status_bar,
            text="Open this tab to scan completed Limited games.",
            font=Theme.scaled_font(9),
            bootstyle="secondary",
        )
        self.lbl_status.pack(side="left", fill="x", expand=True)
        ttk.Label(
            status_bar,
            text="Local only • raw logs are never stored",
            font=Theme.scaled_font(8),
            bootstyle="secondary",
        ).pack(side="right")

        splitter = ttk.PanedWindow(self, orient=tkinter.HORIZONTAL)
        splitter.pack(fill="both", expand=True)

        match_frame = ttk.Labelframe(
            splitter, text=" MATCHES ", padding=Theme.scaled_val(6)
        )
        splitter.add(match_frame, weight=1)
        match_frame.rowconfigure(0, weight=1)
        match_frame.columnconfigure(0, weight=1)

        self.match_tree = ttk.Treeview(
            match_frame,
            columns=("date", "event", "result", "coverage"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        for column, heading, width in (
            ("date", "Played", 105),
            ("event", "Event", 112),
            ("result", "Result", 58),
            ("coverage", "Log", 58),
        ):
            self.match_tree.heading(column, text=heading)
            self.match_tree.column(column, width=Theme.scaled_val(width), minwidth=55)
        match_scroll = AutoScrollbar(
            match_frame, orient="vertical", command=self.match_tree.yview
        )
        self.match_tree.configure(yscrollcommand=match_scroll.set)
        self.match_tree.grid(row=0, column=0, sticky="nsew")
        match_scroll.grid(row=0, column=1, sticky="ns")
        self.match_tree.bind("<<TreeviewSelect>>", self._on_match_selected)

        detail_frame = ttk.Frame(splitter)
        splitter.add(detail_frame, weight=3)
        self.detail_notebook = ttk.Notebook(detail_frame)
        self.detail_notebook.pack(fill="both", expand=True)

        self.review_tab = ttk.Frame(self.detail_notebook)
        self.timeline_tab = ttk.Frame(self.detail_notebook, padding=Theme.scaled_val(8))
        self.deck_tab = ttk.Frame(self.detail_notebook)
        self.progress_tab = ttk.Frame(self.detail_notebook, padding=Theme.scaled_val(12))
        self.detail_notebook.add(self.review_tab, text=" Review ")
        self.detail_notebook.add(self.timeline_tab, text=" Timeline ")
        self.detail_notebook.add(self.deck_tab, text=" Deck Changes ")
        self.detail_notebook.add(self.progress_tab, text=" Progress ")

        self._build_review_canvas()
        self._build_timeline()
        self._build_deck_changes()
        self._build_progress()
        self._render_deck_changes(None, None, None, False)

    def _build_review_canvas(self):
        self.review_tab.rowconfigure(0, weight=1)
        self.review_tab.columnconfigure(0, weight=1)
        self.review_canvas = tkinter.Canvas(
            self.review_tab, highlightthickness=0, bg=Theme.BG_PRIMARY
        )
        review_scroll = AutoScrollbar(
            self.review_tab, orient="vertical", command=self.review_canvas.yview
        )
        self.review_canvas.configure(yscrollcommand=review_scroll.set)
        self.review_canvas.grid(row=0, column=0, sticky="nsew")
        review_scroll.grid(row=0, column=1, sticky="ns")
        self.review_content = ttk.Frame(
            self.review_canvas, padding=Theme.scaled_val(14)
        )
        self.review_window = self.review_canvas.create_window(
            (0, 0), window=self.review_content, anchor="nw"
        )
        self.review_content.bind(
            "<Configure>",
            lambda event: self.review_canvas.configure(
                scrollregion=self.review_canvas.bbox("all")
            ),
        )
        self.review_canvas.bind("<Configure>", self._resize_review_canvas)
        bind_scroll(self.review_canvas, self.review_canvas.yview_scroll)
        bind_scroll(self.review_content, self.review_canvas.yview_scroll)
        self._render_empty_review()

    def _resize_review_canvas(self, event):
        self.review_canvas.itemconfigure(self.review_window, width=event.width)
        wrap = max(280, event.width - Theme.scaled_val(70))
        for label in self._review_wrap_labels:
            if label.winfo_exists():
                label.configure(wraplength=wrap)

    def _build_timeline(self):
        self.timeline_tab.rowconfigure(1, weight=1)
        self.timeline_tab.columnconfigure(0, weight=1)
        self.lbl_timeline_hint = ttk.Label(
            self.timeline_tab,
            text="Recorded player decisions and actions. Hidden opponent information is omitted.",
            bootstyle="secondary",
        )
        self.lbl_timeline_hint.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.timeline_tree = ttk.Treeview(
            self.timeline_tab,
            columns=("turn", "phase", "action", "detail"),
            show="headings",
        )
        for column, heading, width in (
            ("turn", "Turn", 55),
            ("phase", "Phase", 100),
            ("action", "Action", 150),
            ("detail", "Detail", 420),
        ):
            self.timeline_tree.heading(column, text=heading)
            self.timeline_tree.column(column, width=Theme.scaled_val(width), minwidth=45)
        timeline_scroll = AutoScrollbar(
            self.timeline_tab, orient="vertical", command=self.timeline_tree.yview
        )
        self.timeline_tree.configure(yscrollcommand=timeline_scroll.set)
        self.timeline_tree.grid(row=1, column=0, sticky="nsew")
        timeline_scroll.grid(row=1, column=1, sticky="ns")

    def _build_deck_changes(self):
        self.deck_tab.rowconfigure(0, weight=1)
        self.deck_tab.columnconfigure(0, weight=1)
        self.deck_canvas = tkinter.Canvas(
            self.deck_tab, highlightthickness=0, bg=Theme.BG_PRIMARY
        )
        deck_scroll = AutoScrollbar(
            self.deck_tab, orient="vertical", command=self.deck_canvas.yview
        )
        self.deck_canvas.configure(yscrollcommand=deck_scroll.set)
        self.deck_canvas.grid(row=0, column=0, sticky="nsew")
        deck_scroll.grid(row=0, column=1, sticky="ns")
        self.deck_content = ttk.Frame(
            self.deck_canvas, padding=Theme.scaled_val(14)
        )
        self.deck_window = self.deck_canvas.create_window(
            (0, 0), window=self.deck_content, anchor="nw"
        )
        self.deck_content.bind(
            "<Configure>",
            lambda event: self.deck_canvas.configure(
                scrollregion=self.deck_canvas.bbox("all")
            ),
        )
        self.deck_canvas.bind("<Configure>", self._resize_deck_canvas)
        bind_scroll(self.deck_canvas, self.deck_canvas.yview_scroll)
        bind_scroll(self.deck_content, self.deck_canvas.yview_scroll)

    def _build_progress(self):
        self.progress_tab.columnconfigure((0, 1, 2), weight=1)
        self.progress_cards = {}
        for column, key, title in (
            (0, "record", "LIMITED RECORD"),
            (1, "reviewed", "CODEX REVIEWS"),
            (2, "trend", "RECENT TREND"),
        ):
            frame = ttk.Labelframe(
                self.progress_tab, text=f" {title} ", padding=Theme.scaled_val(12)
            )
            frame.grid(row=0, column=column, sticky="nsew", padx=5, pady=5)
            label = ttk.Label(
                frame,
                text="—",
                font=Theme.scaled_font(13, "bold"),
                justify="center",
                anchor="center",
                wraplength=Theme.scaled_val(220),
            )
            label.pack(fill="both", expand=True)
            self.progress_cards[key] = label

        focus = ttk.Labelframe(
            self.progress_tab,
            text=" RECURRING FOCUS AREAS ",
            padding=Theme.scaled_val(12),
        )
        focus.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=5, pady=10)
        self.lbl_focus_areas = ttk.Label(
            focus,
            text="Review games to establish your baseline.",
            font=Theme.scaled_font(10),
            justify="left",
        )
        self.lbl_focus_areas.pack(anchor="nw", fill="x")
        self.progress_tab.rowconfigure(1, weight=1)

    def refresh(self):
        self._start_scan(force=False)

    def _log_path(self) -> str:
        candidates = (
            getattr(self.scanner, "arena_file", ""),
            getattr(self.configuration.settings, "arena_log_location", ""),
            os.path.expanduser("~/Library/Logs/Wizards Of The Coast/MTGA/Player.log"),
        )
        return next(
            (
                os.fspath(path)
                for path in candidates
                if isinstance(path, (str, os.PathLike))
                and path
                and os.path.isfile(path)
            ),
            "",
        )

    def _start_scan(self, force: bool):
        if self._scan_running or self._review_running:
            return
        path = self._log_path()
        if not path:
            self.lbl_status.configure(
                text="Player.log was not found. Set its location in Preferences.",
                bootstyle="danger",
            )
            return
        signature = (path, os.path.getsize(path), os.path.getmtime(path))
        if not force and signature == self._last_log_signature:
            return
        self._last_log_signature = signature
        self._scan_running = True
        self._task_token += 1
        token = self._task_token
        self.btn_scan.configure(state="disabled", text="Scanning…")
        self.lbl_status.configure(
            text=f"Reading {signature[1] / 1_048_576:.1f} MB of detailed gameplay events…",
            bootstyle="info",
        )

        def worker():
            try:
                games = self.parser.parse(path)
                completed_games = [game for game in games if game.completed]
                parsed_keys = {
                    match_key(game.match_id, game.game_number)
                    for game in completed_games
                }
                stored_before_scan = self.store.load().games
                for game in games:
                    if game.completed:
                        key = match_key(game.match_id, game.game_number)
                        parsed_prior = [
                            build_game_indicators(other)
                            for other in completed_games
                            if other.deck_fingerprint
                            and other.deck_fingerprint == game.deck_fingerprint
                            and match_key(other.match_id, other.game_number) != key
                        ]
                        historical_prior = [
                            stored.indicators
                            for stored in stored_before_scan
                            if stored.deck_fingerprint
                            and stored.deck_fingerprint == game.deck_fingerprint
                            and stored.match_key != key
                            and stored.match_key not in parsed_keys
                        ][:8]
                        self.store.upsert_game(
                            game,
                            analyze_game(
                                game, [*parsed_prior, *historical_prior][:19]
                            ),
                        )
                self._results.put(("scan", token, games, ""))
            except Exception as error:
                self._results.put(("scan", token, [], str(error)))

        threading.Thread(target=worker, name="game-log-review-scan", daemon=True).start()

    def _poll_results(self):
        try:
            while True:
                kind, token, value, error = self._results.get_nowait()
                if token != self._task_token:
                    continue
                if kind == "scan":
                    self._apply_scan(value, error)
                elif kind == "review":
                    self._apply_codex_review(value, error)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._poll_id = self.after(100, self._poll_results)

    def _apply_scan(self, games, error: str):
        self._scan_running = False
        self.btn_scan.configure(state="normal", text="Scan Player.log")
        if error:
            self._last_log_signature = None
            self.lbl_status.configure(text=f"Scan failed: {error}", bootstyle="danger")
            return

        self.games = {match_key(game.match_id, game.game_number): game for game in games}
        for item in self.match_tree.get_children():
            self.match_tree.delete(item)

        history = self.store.load().games
        keys_added = set()
        for game in games:
            key = match_key(game.match_id, game.game_number)
            keys_added.add(key)
            self._insert_match_row(
                key,
                game.played_at,
                game.event_id,
                game.result,
                game.coverage,
            )
        for stored in history:
            if stored.match_key not in keys_added:
                self._insert_match_row(
                    stored.match_key,
                    stored.played_at,
                    stored.event_id,
                    stored.result,
                    stored.coverage,
                )

        completed = sum(game.completed for game in games)
        in_progress = sum(not game.completed for game in games)
        self.lbl_status.configure(
            text=(
                f"Found {completed} completed Limited game(s)"
                + (f" and {in_progress} in progress" if in_progress else "")
                + ". Select a match for evidence and coaching."
            ),
            bootstyle="success" if completed else "warning",
        )
        self._render_progress()
        children = self.match_tree.get_children()
        if children:
            preferred = next(
                (
                    key
                    for key in children
                    if self.games.get(key) and self.games[key].completed
                ),
                children[0],
            )
            self.match_tree.selection_set(preferred)
            self.match_tree.focus(preferred)
            self._select_key(preferred)

    def _insert_match_row(self, key, played_at, event_id, result, coverage):
        try:
            date_text = datetime.fromisoformat(played_at).strftime("%b %d, %H:%M")
        except (TypeError, ValueError):
            date_text = played_at or "Unknown"
        event_text = str(event_id).replace("PremierDraft_", "Premier ").replace(
            "QuickDraft_", "Quick "
        )
        self.match_tree.insert(
            "",
            "end",
            iid=key,
            values=(date_text, event_text[:24], result, coverage.title()),
        )

    def _on_match_selected(self, event=None):
        selection = self.match_tree.selection()
        if selection:
            self._select_key(selection[0])

    def _select_key(self, key: str):
        self._selected_key = key
        game = self.games.get(key)
        stored = self.store.get(key)
        review = (
            stored.codex_review
            if stored and stored.codex_review
            else (stored.deterministic_review if stored else None)
        )
        from_codex = bool(stored and stored.codex_review)
        self._render_review(review, game, from_codex)
        self._render_timeline(game)
        self._render_deck_changes(review, game, stored, from_codex)
        can_review = bool(
            game
            and game.completed
            and game.coverage != "summary"
            and self.configuration.model_assistance.enabled
            and self.configuration.model_assistance.allow_manual_analysis
            and not self._review_running
        )
        self.btn_codex.configure(state="normal" if can_review else "disabled")

    def _render_empty_review(self):
        self._clear_review()
        ttk.Label(
            self.review_content,
            text="Select a completed match to see evidence-backed coaching.",
            font=Theme.scaled_font(12),
            bootstyle="secondary",
        ).pack(pady=Theme.scaled_val(40))

    def _clear_review(self):
        for widget in self.review_content.winfo_children():
            widget.destroy()
        self._review_wrap_labels = []

    def _resize_deck_canvas(self, event):
        self.deck_canvas.itemconfigure(self.deck_window, width=event.width)
        wrap = max(280, event.width - Theme.scaled_val(70))
        for label in self._deck_wrap_labels:
            if label.winfo_exists():
                label.configure(wraplength=wrap)

    def _render_review(self, review, game, from_codex: bool):
        self._clear_review()
        if review is None:
            self._render_empty_review()
            return
        source = "Local Codex review" if from_codex else "Conservative log checks"
        coverage = game.coverage.title() if game else "Historical"
        ttk.Label(
            self.review_content,
            text=f"{source}  •  {coverage} evidence",
            font=Theme.scaled_font(9, "bold"),
            bootstyle="success" if from_codex else "info",
        ).pack(anchor="w", pady=(0, 6))
        summary = ttk.Label(
            self.review_content,
            text=review.summary,
            font=Theme.scaled_font(11),
            justify="left",
            wraplength=Theme.scaled_val(680),
        )
        summary.pack(anchor="w", fill="x", pady=(0, 12))
        self._review_wrap_labels.append(summary)

        if review.strengths:
            strength_frame = ttk.Labelframe(
                self.review_content,
                text=" WHAT WENT WELL ",
                padding=Theme.scaled_val(10),
            )
            strength_frame.pack(fill="x", pady=(0, 12))
            for strength in review.strengths:
                label = ttk.Label(
                    strength_frame,
                    text=f"✓ {strength}",
                    justify="left",
                    wraplength=Theme.scaled_val(650),
                    bootstyle="success",
                )
                label.pack(anchor="w", fill="x", pady=2)
                self._review_wrap_labels.append(label)

        if not review.findings:
            ttk.Label(
                self.review_content,
                text="No clear mistake detected in the observable decisions.",
                font=Theme.scaled_font(12, "bold"),
                bootstyle="success",
            ).pack(anchor="w", pady=20)
            return

        for index, finding in enumerate(review.findings, start=1):
            frame = ttk.Labelframe(
                self.review_content,
                text=f" {index}. {finding.title.upper()} ",
                padding=Theme.scaled_val(10),
            )
            frame.pack(fill="x", pady=Theme.scaled_val((0, 10)))
            metadata = (
                f"{finding.certainty.upper()}  •  {finding.severity.upper()} IMPACT"
                + (f"  •  TURN {finding.turn}" if finding.turn else "")
                + f"  •  {finding.confidence:.0%} CONFIDENCE"
            )
            ttk.Label(
                frame,
                text=metadata,
                font=Theme.scaled_font(8, "bold"),
                bootstyle=(
                    "danger"
                    if finding.severity == "high"
                    else ("warning" if finding.severity == "medium" else "info")
                ),
            ).pack(anchor="w", pady=(0, 6))
            for title, text in (
                ("Evidence", finding.evidence),
                ("Better line", finding.better_line),
                ("Practice", finding.practice_tip),
            ):
                label = ttk.Label(
                    frame,
                    text=f"{title}: {text}",
                    justify="left",
                    wraplength=Theme.scaled_val(650),
                )
                label.pack(anchor="w", fill="x", pady=2)
                self._review_wrap_labels.append(label)

    def _render_timeline(self, game):
        for item in self.timeline_tree.get_children():
            self.timeline_tree.delete(item)
        if game is None:
            self.lbl_timeline_hint.configure(
                text="Detailed timeline is unavailable for this historical match."
            )
            return
        self.lbl_timeline_hint.configure(
            text=f"{len(game.actions)} recorded actions • {len(game.decisions)} decision points • {game.coverage.title()} coverage"
        )
        for index, action in enumerate(game.actions):
            action_text = action.action
            if action.card:
                action_text = f"{action.action} — {action.card.name}"
            self.timeline_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(action.turn or "—", action.phase, action_text, action.detail),
            )

    def _render_deck_changes(self, review, game, stored, from_codex: bool):
        for widget in self.deck_content.winfo_children():
            widget.destroy()
        self._deck_wrap_labels = []

        indicators = build_game_indicators(game) if game else (
            stored.indicators if stored else None
        )
        fingerprint = game.deck_fingerprint if game else (
            stored.deck_fingerprint if stored else ""
        )
        sample_size = 1 + len(
            self.store.same_deck_games(
                fingerprint,
                exclude_key=stored.match_key if stored else "",
            )
        ) if fingerprint else 0

        ttk.Label(
            self.deck_content,
            text="DECK CHANGES",
            font=Theme.scaled_font(14, "bold"),
            bootstyle="primary",
        ).pack(anchor="w")
        if indicators and indicators.deck_size:
            ttk.Label(
                self.deck_content,
                text=(
                    f"Submitted: {indicators.deck_size} cards • "
                    f"{indicators.land_count} lands • "
                    f"{indicators.sideboard_size} sideboard • "
                    f"{sample_size} same-deck game(s)"
                ),
                font=Theme.scaled_font(9, "bold"),
                bootstyle="secondary",
            ).pack(anchor="w", pady=(2, 12))
        else:
            label = ttk.Label(
                self.deck_content,
                text="The submitted deck list was not present in this historical log segment.",
                justify="left",
                bootstyle="secondary",
            )
            label.pack(anchor="w", fill="x", pady=(8, 16))
            self._deck_wrap_labels.append(label)

        changes = list(review.deck_changes) if review else []
        if not changes:
            message = (
                "No deck change is justified by the recorded games. Keep the list for now; "
                "a gameplay mistake or a single loss is not evidence that a card should be cut."
                if from_codex
                else "No automatic deck change is justified yet. Analyze Game + Deck to compare "
                "the exact submitted list, sideboard, observable decisions, and prior games with this deck."
            )
            label = ttk.Label(
                self.deck_content,
                text=message,
                font=Theme.scaled_font(11),
                justify="left",
                wraplength=Theme.scaled_val(680),
                bootstyle="success" if from_codex else "info",
            )
            label.pack(anchor="w", fill="x", pady=Theme.scaled_val((8, 16)))
            self._deck_wrap_labels.append(label)
        else:
            for index, change in enumerate(changes, start=1):
                frame = ttk.Labelframe(
                    self.deck_content,
                    text=f" {index}. {change.priority.upper()} PRIORITY ",
                    padding=Theme.scaled_val(12),
                )
                frame.pack(fill="x", pady=Theme.scaled_val((0, 12)))
                swap = f"CUT  {change.quantity}× {change.cut_card}"
                if change.add_card:
                    swap += f"\nADD  {change.quantity}× {change.add_card}"
                ttk.Label(
                    frame,
                    text=swap,
                    font=Theme.scaled_font(12, "bold"),
                    bootstyle="warning",
                    justify="left",
                ).pack(anchor="w", pady=(0, 7))
                metadata = ttk.Label(
                    frame,
                    text=(
                        f"{change.certainty.upper()} • {change.confidence:.0%} CONFIDENCE • "
                        f"SUPPORTED BY {change.evidence_games} GAME(S)"
                    ),
                    font=Theme.scaled_font(8, "bold"),
                    bootstyle="secondary",
                )
                metadata.pack(anchor="w", pady=(0, 6))
                for title, text in (
                    ("Evidence", change.evidence),
                    ("Why", change.rationale),
                    ("Expected effect", change.expected_effect),
                ):
                    label = ttk.Label(
                        frame,
                        text=f"{title}: {text}",
                        justify="left",
                        wraplength=Theme.scaled_val(650),
                    )
                    label.pack(anchor="w", fill="x", pady=2)
                    self._deck_wrap_labels.append(label)

        note = ttk.Label(
            self.deck_content,
            text=(
                "Deck advice is intentionally conservative: it uses the submitted list and "
                "same-deck evidence, never cards you simply failed to draw."
            ),
            justify="left",
            wraplength=Theme.scaled_val(680),
            bootstyle="secondary",
        )
        note.pack(anchor="w", fill="x", pady=(4, 10))
        self._deck_wrap_labels.append(note)

    def _render_progress(self):
        progress = self.store.progress()
        win_rate = progress.wins / progress.total_games if progress.total_games else 0
        self.progress_cards["record"].configure(
            text=(
                f"{progress.wins}–{progress.losses}\n{win_rate:.0%} win rate"
                if progress.total_games
                else "No games yet"
            )
        )
        self.progress_cards["reviewed"].configure(
            text=f"{progress.reviewed_games} of {progress.total_games}"
        )
        self.progress_cards["trend"].configure(text=progress.trend)
        self.lbl_focus_areas.configure(
            text=(
                "\n".join(
                    f"{index}. {category.replace('_', ' ').title()} — {count} finding(s)"
                    for index, (category, count) in enumerate(progress.top_categories, 1)
                )
                if progress.top_categories
                else "No recurring issue is visible yet. Review more games to establish a baseline."
            )
        )

    def _analyze_selected(self):
        game = self.games.get(self._selected_key)
        if not game or self._review_running:
            return
        review_key = self._selected_key
        prior_games = self.store.same_deck_games(
            game.deck_fingerprint, exclude_key=review_key
        )
        self._review_running = True
        self._task_token += 1
        token = self._task_token
        self.btn_codex.configure(state="disabled", text="Analyzing Game + Deck…")
        self.btn_scan.configure(state="disabled")
        self.lbl_status.configure(
            text=(
                "Local Codex is comparing your choices with the recorded board states. "
                f"It is also comparing the submitted deck with {len(prior_games)} prior "
                "same-deck game(s). This can take up to two minutes; the app remains responsive…"
            ),
            bootstyle="info",
        )

        def worker():
            try:
                review = self.reviewer.review(
                    game,
                    timeout_seconds=max(
                        120.0,
                        float(self.configuration.model_assistance.request_timeout_seconds),
                    ),
                    model=self.configuration.model_assistance.model,
                    prior_games=prior_games,
                )
                self._results.put(("review", token, (review_key, review), ""))
            except CodexGameReviewError as error:
                self._results.put(("review", token, (review_key, None), str(error)))
            except Exception as error:
                self._results.put(
                    ("review", token, (review_key, None), f"unexpected error: {error}")
                )

        threading.Thread(target=worker, name="codex-game-review", daemon=True).start()

    def _apply_codex_review(self, result, error: str):
        review_key, review = result
        self._review_running = False
        self.btn_codex.configure(text="Analyze Game + Deck")
        self.btn_scan.configure(state="normal")
        if error:
            self.lbl_status.configure(
                text=f"Codex analysis unavailable: {error}", bootstyle="warning"
            )
            self._select_key(self._selected_key)
            return
        self.store.save_codex_review(review_key, review)
        self.lbl_status.configure(
            text="Codex review complete. Findings were saved to your local progress history.",
            bootstyle="success",
        )
        self._select_key(self._selected_key)
        self._render_progress()
