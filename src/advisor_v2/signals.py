"""Contextual open-lane and wheel evidence helpers."""

from __future__ import annotations

from typing import Dict

from src.advisor_v2.statistics import safe_float


class SignalEvidenceModel:
    def __init__(self, baseline_wr: float):
        self.baseline_wr = baseline_wr if baseline_wr > 0 else 54.0

    def observed_card_score(
        self,
        card: Dict,
        pick: int,
        original_pack_strength: float = 0.0,
        wheeled: bool = False,
    ) -> float:
        stats = card.get("deck_colors", {}).get("All Decks", {})
        wr = safe_float(stats.get("gihwr"))
        ata = safe_float(stats.get("ata")) or safe_float(stats.get("alsa"))
        if wr <= self.baseline_wr or ata <= 0 or pick <= ata:
            return 0.0

        quality = wr - self.baseline_wr
        lateness = min(6.0, pick - ata)
        score = quality * lateness

        colors = card.get("colors", [])
        if len(colors) >= 2:
            score *= 0.55
        tags = set(card.get("tags", []))
        if tags.intersection({"build_around", "narrow", "sideboard"}):
            score *= 0.55
        if original_pack_strength > 0:
            score *= max(0.65, min(1.15, 1.0 - (original_pack_strength * 0.03)))
        if wheeled:
            score *= 1.8
        return score

    def distribute(self, card: Dict, score: float) -> Dict[str, float]:
        colors = [value for value in card.get("colors", []) if value in "WUBRG"]
        if not colors or score <= 0:
            return {}
        share = score / len(colors)
        return {color: share for color in colors}
