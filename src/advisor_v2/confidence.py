"""Confidence calculation kept separate from card score."""

from __future__ import annotations

import math


def recommendation_confidence(
    score_margin: float,
    statistical_confidence: float,
    lane_probability: float,
    raw_context_agree: bool,
    speculative_fraction: float,
    archetype_data_available: bool,
) -> float:
    margin_confidence = 1.0 - math.exp(-max(0.0, score_margin) / 7.0)
    agreement = 1.0 if raw_context_agree else 0.35
    archetype = 1.0 if archetype_data_available else 0.45
    result = (
        (margin_confidence * 0.30)
        + (max(0.0, min(1.0, statistical_confidence)) * 0.25)
        + (max(0.0, min(1.0, lane_probability)) * 0.20)
        + (agreement * 0.10)
        + (archetype * 0.15)
    )
    result *= 1.0 - (max(0.0, min(1.0, speculative_fraction)) * 0.35)
    return max(0.05, min(0.98, result))
