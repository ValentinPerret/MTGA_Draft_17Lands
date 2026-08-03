import tkinter
import time
from unittest.mock import MagicMock

import pytest

from src.configuration import Configuration
from src.game_review.analyzer import analyze_game
from src.game_review.models import (
    CodexGameReview,
    GameCard,
    GameDecision,
    GameSnapshot,
    ParsedGame,
)
from src.game_review.store import GameReviewStore
from src.ui.styles import Theme
from src.ui.windows.game_review import GameReviewPanel


class StaticParser:
    def __init__(self, games):
        self.games = games

    def parse(self, path):
        return self.games


class StaticReviewer:
    def __init__(self):
        self.calls = 0

    def review(self, game, **kwargs):
        self.calls += 1
        return CodexGameReview(
            summary="Detailed review complete.",
            strengths=["The opening was functional."],
            focus_areas=[],
            findings=[],
            deck_changes=[],
            decision_feedback=[],
        )


@pytest.fixture
def root():
    window = tkinter.Tk()
    Theme.apply(window, "Dark")
    yield window
    window.destroy()


def test_game_review_panel_populates_match_review_and_progress(root, tmp_path):
    log_path = tmp_path / "Player.log"
    log_path.write_text("detailed log", encoding="utf-8")
    game = ParsedGame(
        match_id="match-ui",
        played_at="2026-08-02T19:05:00",
        event_id="PremierDraft_EOE_20260728",
        completed=True,
        limited=True,
        result="Win",
        turns=9,
        coverage="full",
        game_state_messages=40,
        deck_fingerprint="same-deck-test",
        deck_cards=[
            *[
                GameCard(name="Island", types=["Land", "Basic"], colors=["U"])
                for _ in range(17)
            ],
            *[
                GameCard(name="Test Creature", types=["Creature"], cmc=2, colors=["U"])
                for _ in range(23)
            ],
        ],
        sideboard_cards=[
            GameCard(name="Sideboard Card", types=["Creature"], cmc=1, colors=["U"])
            for _ in range(15)
        ],
        decisions=[
            GameDecision(
                kind="mulligan",
                choice="AcceptHand",
                snapshot=GameSnapshot(
                    turn=0,
                    phase="Opening",
                    active_seat=1,
                    hand=[
                        GameCard(name="Island", types=["Land", "Basic"]),
                        GameCard(name="Test Creature", types=["Creature"]),
                    ],
                ),
            )
        ],
    )
    scanner = MagicMock()
    scanner.arena_file = str(log_path)
    scanner.sets_location = str(tmp_path / "Sets")
    panel = GameReviewPanel(
        root,
        scanner,
        Configuration(),
        parser=StaticParser([game]),
        store=GameReviewStore(str(tmp_path / "history.json")),
    )
    panel.pack(fill="both", expand=True)

    panel.store.upsert_game(game, analyze_game(game))
    panel._apply_scan([game], "")
    root.update()

    assert len(panel.match_tree.get_children()) == 1
    assert str(panel.btn_codex.cget("state")) == "normal"
    assert len(panel.detail_notebook.tabs()) == 5
    assert str(panel.btn_review_all.cget("state")) == "normal"
    assert "1–0" in panel.progress_cards["record"].cget("text")
    assert "No clear mistake" in " ".join(
        str(widget.cget("text"))
        for widget in panel.review_content.winfo_children()
        if hasattr(widget, "cget")
    )
    assert "40 cards" in " ".join(
        str(widget.cget("text"))
        for widget in panel.deck_content.winfo_children()
        if hasattr(widget, "cget")
    )
    assert "no automatic deck change" in " ".join(
        str(widget.cget("text"))
        for widget in panel.deck_content.winfo_children()
        if hasattr(widget, "cget")
    ).lower()

    def all_text(widget):
        values = []
        if hasattr(widget, "cget"):
            try:
                values.append(str(widget.cget("text")))
            except tkinter.TclError:
                pass
        for child in widget.winfo_children():
            values.extend(all_text(child))
        return values

    assert "opening-hand choice" in " ".join(all_text(panel.feedback_content)).lower()


def test_review_all_saves_each_unreviewed_game_without_blocking(root, tmp_path):
    log_path = tmp_path / "Player.log"
    log_path.write_text("detailed log", encoding="utf-8")
    game = ParsedGame(
        match_id="batch-game",
        played_at="2026-08-02T20:00:00",
        completed=True,
        limited=True,
        result="Loss",
        turns=8,
        coverage="full",
        game_state_messages=30,
    )
    scanner = MagicMock()
    scanner.arena_file = str(log_path)
    scanner.sets_location = str(tmp_path / "Sets")
    reviewer = StaticReviewer()
    store = GameReviewStore(str(tmp_path / "history.json"))
    panel = GameReviewPanel(
        root,
        scanner,
        Configuration(),
        parser=StaticParser([game]),
        store=store,
        reviewer=reviewer,
    )
    panel.pack(fill="both", expand=True)
    store.upsert_game(game, analyze_game(game))
    panel._apply_scan([game], "")

    panel._analyze_all()
    deadline = time.monotonic() + 2
    while panel._review_running and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)

    assert panel._review_running is False
    assert reviewer.calls == 1
    assert store.progress().reviewed_games == 1
    assert str(panel.btn_review_all.cget("state")) == "disabled"
    assert "complete" in panel.lbl_status.cget("text").lower()


def test_game_review_panel_explains_missing_log(root, tmp_path):
    scanner = MagicMock()
    scanner.arena_file = str(tmp_path / "missing.log")
    configuration = Configuration()
    configuration.settings.arena_log_location = str(tmp_path / "also-missing.log")
    panel = GameReviewPanel(
        root,
        scanner,
        configuration,
        parser=StaticParser([]),
        store=GameReviewStore(str(tmp_path / "history.json")),
    )
    panel._log_path = lambda: ""

    panel._start_scan(force=True)

    assert "not found" in panel.lbl_status.cget("text")
