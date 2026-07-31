"""Policy-only router for optional Codex review.

The local result is always complete before this policy is consulted, and a caller
may safely ignore a positive decision.
"""

from __future__ import annotations

from src.advisor_v2.models import ModelCallDecision


class ModelReviewRouter:
    def __init__(self, config=None):
        self.config = config

    def decide(
        self,
        *,
        score_margin: float,
        confidence: float,
        state_complete: bool = True,
        remaining_pick_seconds: float | None = None,
        equivalent_cached: bool = False,
        calls_used: int = 0,
        synergy_changed_order: bool = False,
        color_pivot: bool = False,
        statistical_disagreement: bool = False,
        manual: bool = False,
    ) -> ModelCallDecision:
        config = self.config
        if config is None or not getattr(config, "enabled", False):
            return ModelCallDecision(False, "model assistance disabled")
        if getattr(config, "provider", "codex") != "codex":
            return ModelCallDecision(False, "unsupported model review provider")
        if not state_complete:
            return ModelCallDecision(False, "draft state incomplete or stale")
        if equivalent_cached:
            return ModelCallDecision(False, "equivalent review is cached")
        if calls_used >= getattr(config, "automatic_call_limit_per_draft", 10) and not manual:
            return ModelCallDecision(False, "per-draft automatic review limit reached")
        if remaining_pick_seconds is not None and remaining_pick_seconds < getattr(
            config, "minimum_remaining_pick_seconds", 20
        ):
            return ModelCallDecision(False, "insufficient pick time")
        if manual and getattr(config, "allow_manual_analysis", True):
            return ModelCallDecision(True, "manual deeper analysis requested")
        if score_margin >= getattr(config, "skip_score_margin", 8.0):
            return ModelCallDecision(False, "local score margin is decisive")
        if confidence >= getattr(config, "skip_confidence", 0.85):
            return ModelCallDecision(False, "local confidence is decisive")
        if score_margin < getattr(config, "close_score_margin", 4.0):
            return ModelCallDecision(True, "top candidates are close")
        if synergy_changed_order:
            return ModelCallDecision(True, "synergy package changes ordering")
        if color_pivot:
            return ModelCallDecision(True, "pick may cause a color pivot or splash")
        if statistical_disagreement:
            return ModelCallDecision(True, "statistics and contextual fit disagree")
        return ModelCallDecision(False, "local review sufficient")
