"""Typed contracts shared by game-log parsing, analysis, persistence, and UI."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


FindingCategory = Literal[
    "mulligan",
    "sequencing",
    "mana",
    "combat",
    "interaction",
    "card_advantage",
    "timing",
    "concession",
    "time_management",
    "other",
]


class GameCard(BaseModel):
    instance_id: int = 0
    card_id: int = 0
    name: str = "Unknown card"
    mana_cost: str = ""
    cmc: float = 0.0
    colors: List[str] = Field(default_factory=list)
    types: List[str] = Field(default_factory=list)
    oracle_text: str = ""
    gihwr: Optional[float] = None
    power: Optional[int] = None
    toughness: Optional[int] = None
    tapped: bool = False


class GameOption(BaseModel):
    action: str
    card: Optional[GameCard] = None
    detail: str = ""


class GameSnapshot(BaseModel):
    turn: int = 0
    phase: str = "Unknown"
    step: str = ""
    active_seat: int = 0
    player_life: Optional[int] = None
    opponent_life: Optional[int] = None
    hand: List[GameCard] = Field(default_factory=list)
    player_battlefield: List[GameCard] = Field(default_factory=list)
    opponent_battlefield: List[GameCard] = Field(default_factory=list)


class GameDecision(BaseModel):
    request_id: int = 0
    state_id: int = 0
    kind: str
    choice: str = ""
    options: List[GameOption] = Field(default_factory=list)
    snapshot: GameSnapshot = Field(default_factory=GameSnapshot)


class GameAction(BaseModel):
    turn: int = 0
    phase: str = "Unknown"
    action: str
    card: Optional[GameCard] = None
    detail: str = ""


class ParsedGame(BaseModel):
    match_id: str
    played_at: str = ""
    event_id: str = "Limited"
    game_number: int = 1
    user_seat: int = 1
    user_team: int = 1
    completed: bool = False
    limited: bool = False
    result: str = "In progress"
    result_reason: str = ""
    turns: int = 0
    coverage: Literal["full", "partial", "summary"] = "summary"
    game_state_messages: int = 0
    decisions: List[GameDecision] = Field(default_factory=list)
    actions: List[GameAction] = Field(default_factory=list)
    deck_cards: List[GameCard] = Field(default_factory=list)
    sideboard_cards: List[GameCard] = Field(default_factory=list)
    deck_fingerprint: str = ""
    final_player_life: Optional[int] = None
    final_opponent_life: Optional[int] = None


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: FindingCategory
    severity: Literal["low", "medium", "high"]
    certainty: Literal["confirmed", "likely", "possible"]
    confidence: float = Field(ge=0.0, le=1.0)
    turn: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=1, max_length=420)
    better_line: str = Field(min_length=1, max_length=420)
    practice_tip: str = Field(min_length=1, max_length=320)
    source: Literal["log", "codex"]


class DeckChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cut_card: str = Field(max_length=120)
    add_card: str = Field(max_length=120)
    quantity: int = Field(ge=1, le=4)
    priority: Literal["low", "medium", "high"]
    certainty: Literal["likely", "possible"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_games: int = Field(ge=1, le=20)
    evidence: str = Field(min_length=1, max_length=420)
    rationale: str = Field(min_length=1, max_length=420)
    expected_effect: str = Field(min_length=1, max_length=320)
    source: Literal["log", "codex"]


class GameIndicators(BaseModel):
    """Small, privacy-safe signals retained for same-deck trend analysis."""

    deck_size: int = 0
    sideboard_size: int = 0
    land_count: int = 0
    opening_hand_lands: Optional[int] = None
    mulligan_count: int = 0
    mana_screw_signal: bool = False
    flood_signal: bool = False
    missed_land_drop_turns: List[int] = Field(default_factory=list)
    stranded_cards: List[str] = Field(default_factory=list, max_length=5)


class MatchReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=700)
    strengths: List[str] = Field(default_factory=list, max_length=5)
    focus_areas: List[str] = Field(default_factory=list, max_length=5)
    findings: List[ReviewFinding] = Field(default_factory=list, max_length=10)
    deck_changes: List[DeckChange] = Field(default_factory=list, max_length=5)


class CodexGameReview(BaseModel):
    """Strict structured response returned by the local Codex executable."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=700)
    strengths: List[str] = Field(max_length=5)
    focus_areas: List[str] = Field(max_length=5)
    findings: List[ReviewFinding] = Field(max_length=8)
    # Default preserves locally stored reviews created before deck coaching existed.
    # The emitted Codex JSON schema still marks this field as required.
    deck_changes: List[DeckChange] = Field(default_factory=list, max_length=5)


class StoredGameReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_key: str
    played_at: str = ""
    event_id: str = "Limited"
    result: str = "Unknown"
    result_reason: str = ""
    turns: int = 0
    coverage: str = "summary"
    decision_count: int = 0
    deck_fingerprint: str = ""
    indicators: GameIndicators = Field(default_factory=GameIndicators)
    deterministic_review: MatchReview
    codex_review: Optional[CodexGameReview] = None


class ReviewHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    games: List[StoredGameReview] = Field(default_factory=list)


class ProgressSummary(BaseModel):
    total_games: int = 0
    wins: int = 0
    losses: int = 0
    reviewed_games: int = 0
    top_categories: List[tuple[str, int]] = Field(default_factory=list)
    recent_findings_per_game: Optional[float] = None
    previous_findings_per_game: Optional[float] = None
    trend: str = "Not enough reviewed games for a trend yet."
