"""Internal, immutable models used by the contextual advisor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class AdjustedStatistics:
    gihwr: float
    ohwr: float
    iwd: float
    gpwr: float
    alsa: float
    play_rate: float
    samples: int
    confidence: float
    source: str
    caveats: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LaneEstimate:
    name: str
    colors: Tuple[str, ...]
    probability: float
    evidence: Tuple[str, ...] = ()
    splash_color: Optional[str] = None

    @property
    def primary_colors(self) -> Tuple[str, ...]:
        return self.colors[:2] if self.splash_color else self.colors


@dataclass
class DeckShell:
    lane: LaneEstimate
    cards: List[dict]
    land_count: int
    quality: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)
    archetype: str = "balanced"
    caveats: List[str] = field(default_factory=list)


@dataclass
class CandidatePathResult:
    lane: LaneEstimate
    baseline: DeckShell
    with_candidate: DeckShell
    improvement: float
    makes_deck: bool
    replacement_card: Optional[str]
    mana_risk: float
    synergy_delta: float


@dataclass(frozen=True)
class ModelCallDecision:
    should_call: bool
    reason: str
