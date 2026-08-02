import tkinter
from unittest.mock import MagicMock

import pytest

from src.configuration import Configuration
from src.game_review.analyzer import analyze_game
from src.game_review.models import ParsedGame
from src.game_review.store import GameReviewStore
from src.ui.styles import Theme
from src.ui.windows.game_review import GameReviewPanel


class StaticParser:
    def __init__(self, games):
        self.games = games

    def parse(self, path):
        return self.games


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
    assert len(panel.detail_notebook.tabs()) == 3
    assert "1–0" in panel.progress_cards["record"].cget("text")
    assert "No clear mistake" in " ".join(
        str(widget.cget("text"))
        for widget in panel.review_content.winfo_children()
        if hasattr(widget, "cget")
    )


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
