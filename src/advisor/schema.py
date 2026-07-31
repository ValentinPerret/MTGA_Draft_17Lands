"""
src/advisor/schema.py
Data models for the Draft Advisor's recommendations.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class Recommendation(BaseModel):
    card_name: str
    base_win_rate: float
    contextual_score: float
    z_score: float
    cast_probability: float  # 0.0 to 1.0 (Karsten math)
    wheel_chance: float  # 0.0 to 100.0 (Polynomial probability)
    functional_cmc: float  # e.g., Landcycler might be 0.5
    reasoning: List[str] = Field(
        default_factory=list
    )  # e.g. ["Uncastable (Double Pip)", "Wheels 80%"]
    is_elite: bool = False
    archetype_fit: str = "Neutral"
    tags: List[str] = Field(default_factory=list)

    # Stable contextual-advisor extension. Legacy recommendations keep working
    # because every new field has a conservative default.
    card_id: Optional[int] = None
    confidence: float = 0.0
    lane_probabilities: Dict[str, float] = Field(default_factory=dict)
    expected_to_make_deck: bool = False
    replacement_card: Optional[str] = None
    score_components: Dict[str, float] = Field(default_factory=dict)
    data_caveats: List[str] = Field(default_factory=list)
    future_considerations: List[str] = Field(default_factory=list)
    engine: str = "legacy"
    model_assisted: bool = False
