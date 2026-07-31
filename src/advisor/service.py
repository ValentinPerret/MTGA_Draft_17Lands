"""Stable dispatcher for legacy and contextual recommendation engines."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.advisor.engine import DraftAdvisor
from src.advisor.schema import Recommendation


class AdvisorService:
    """Evaluate a normalized draft snapshot through the configured engine.

    Both engines return the same :class:`Recommendation` schema. Imports for the
    contextual implementation are lazy so legacy startup remains isolated and a
    user can always roll back without loading the newer search modules.
    """

    def __init__(
        self,
        set_metrics: Any,
        taken_cards: List[Dict],
        signals: Optional[Dict[str, float]] = None,
        configuration: Any = None,
        event_name: str = "",
        draft_history: Optional[List[Dict]] = None,
    ):
        self.metrics = set_metrics
        self.pool = taken_cards or []
        self.signals = signals or {}
        self.configuration = configuration
        self.event_name = event_name
        self.draft_history = draft_history or []

    @property
    def engine_name(self) -> str:
        try:
            return self.configuration.settings.advisor_engine
        except (AttributeError, TypeError):
            return "legacy"

    def evaluate_pack(
        self, pack_cards: List[Dict], current_pick: int, current_pack: int = 1
    ) -> List[Recommendation]:
        if self.engine_name == "contextual_v2":
            from src.advisor_v2.service import ContextualDraftAdvisor

            advisor = ContextualDraftAdvisor(
                self.metrics,
                self.pool,
                signals=self.signals,
                event_name=self.event_name,
                draft_history=self.draft_history,
                model_config=getattr(self.configuration, "model_assistance", None),
            )
        else:
            advisor = DraftAdvisor(self.metrics, self.pool, signals=self.signals)

        return advisor.evaluate_pack(
            pack_cards, current_pick=current_pick, current_pack=current_pack
        )
