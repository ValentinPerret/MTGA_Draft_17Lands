"""Privacy-minimized persistent history for longitudinal gameplay coaching."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from src.configuration import get_config_path
from src.game_review.models import (
    CodexGameReview,
    MatchReview,
    ParsedGame,
    ProgressSummary,
    ReviewHistory,
    StoredGameReview,
)


def match_key(match_id: str, game_number: int = 1) -> str:
    raw = f"{match_id}:{game_number}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:24]


class GameReviewStore:
    def __init__(self, path: Optional[str] = None):
        default = Path(get_config_path()).parent / "GameReviews" / "history.json"
        self.path = Path(path) if path else default
        self._lock = threading.RLock()

    def load(self) -> ReviewHistory:
        with self._lock:
            if not self.path.exists():
                return ReviewHistory()
            try:
                return ReviewHistory.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError, ValidationError):
                return ReviewHistory()

    def upsert_game(self, game: ParsedGame, review: MatchReview) -> StoredGameReview:
        key = match_key(game.match_id, game.game_number)
        with self._lock:
            history = self.load()
            existing = next((item for item in history.games if item.match_key == key), None)
            stored = StoredGameReview(
                match_key=key,
                played_at=game.played_at,
                event_id=game.event_id,
                result=game.result,
                result_reason=game.result_reason,
                turns=game.turns,
                coverage=game.coverage,
                decision_count=len(game.decisions),
                deterministic_review=review,
                codex_review=existing.codex_review if existing else None,
            )
            history.games = [item for item in history.games if item.match_key != key]
            history.games.append(stored)
            history.games.sort(key=lambda value: value.played_at, reverse=True)
            history.games = history.games[:250]
            self._write(history)
            return stored

    def save_codex_review(self, key: str, review: CodexGameReview) -> bool:
        with self._lock:
            history = self.load()
            for index, game in enumerate(history.games):
                if game.match_key == key:
                    history.games[index] = game.model_copy(update={"codex_review": review})
                    self._write(history)
                    return True
        return False

    def get(self, key: str) -> Optional[StoredGameReview]:
        return next((game for game in self.load().games if game.match_key == key), None)

    def progress(self) -> ProgressSummary:
        games = self.load().games
        wins = sum(game.result == "Win" for game in games)
        losses = sum(game.result == "Loss" for game in games)
        reviewed = [game for game in games if game.codex_review is not None]
        category_counts = Counter()
        for game in games:
            review = game.codex_review or game.deterministic_review
            category_counts.update(finding.category for finding in review.findings)

        recent_avg = previous_avg = None
        trend = "Not enough reviewed games for a trend yet."
        if len(reviewed) >= 3:
            recent = reviewed[:5]
            previous = reviewed[5:10]
            recent_avg = sum(len(game.codex_review.findings) for game in recent) / len(recent)
            if previous:
                previous_avg = sum(len(game.codex_review.findings) for game in previous) / len(previous)
                delta = recent_avg - previous_avg
                if delta <= -0.35:
                    trend = "Improving: fewer coaching findings per reviewed game."
                elif delta >= 0.35:
                    trend = "Recent games show more coaching opportunities than the prior sample."
                else:
                    trend = "Stable: the finding rate is roughly unchanged."
            else:
                trend = "Baseline established; review more games to measure change."

        return ProgressSummary(
            total_games=len(games),
            wins=wins,
            losses=losses,
            reviewed_games=len(reviewed),
            top_categories=category_counts.most_common(5),
            recent_findings_per_game=recent_avg,
            previous_findings_per_game=previous_avg,
            trend=trend,
        )

    def _write(self, history: ReviewHistory):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix="game-review-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(history.model_dump(mode="json"), handle, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
