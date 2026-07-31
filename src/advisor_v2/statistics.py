"""Sample-aware statistical adjustment for contextual card evaluation."""

from __future__ import annotations

import math
from typing import Dict, Iterable, Optional, Tuple

from src.advisor_v2.models import AdjustedStatistics


DEFAULT_FORMAT_MEAN = 54.0
DEFAULT_FORMAT_STD = 4.0


def safe_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def sample_count(stats: Dict) -> int:
    """Return the strongest available sample-count field without adding counts."""

    values = []
    for key in ("samples", "gih", "ngih", "drawn_game_count", "game_count", "ngp"):
        try:
            value = int(stats.get(key, 0) or 0)
            if value > 0:
                values.append(value)
        except (TypeError, ValueError):
            continue
    return max(values, default=0)


def shrink_metric(
    observed: Optional[float],
    samples: int,
    prior: float,
    prior_strength: int = 1200,
    maximum_weight: float = 0.97,
) -> Tuple[float, float]:
    """Shrink an observed percentage toward a relevant prior.

    A missing/non-positive observation is represented by the prior with zero
    observation confidence; it is never converted into a zero-quality value.
    """

    value = safe_float(observed)
    if value <= 0.0 or samples <= 0:
        return prior, 0.0
    weight = min(maximum_weight, samples / (samples + max(1, prior_strength)))
    return (weight * value) + ((1.0 - weight) * prior), weight


def shrink_centered_metric(
    observed: Optional[float],
    samples: int,
    prior: float = 0.0,
    prior_strength: int = 600,
    maximum_weight: float = 0.97,
) -> Tuple[float, float]:
    """Shrink a signed metric such as IWD while preserving negative evidence."""

    value = safe_float(observed)
    if value == 0.0 or samples <= 0:
        return prior, 0.0
    weight = min(maximum_weight, samples / (samples + max(1, prior_strength)))
    return (weight * value) + ((1.0 - weight) * prior), weight


def _metric(stats: Dict, key: str, aliases: Iterable[str] = ()) -> float:
    for candidate in (key, *aliases):
        value = safe_float(stats.get(candidate))
        if value != 0.0:
            return value
    return 0.0


def select_stats(card: Dict, lane_key: str) -> Tuple[Dict, str, Tuple[str, ...]]:
    deck_colors = card.get("deck_colors", {}) or {}
    lane_stats = deck_colors.get(lane_key, {}) if lane_key else {}
    global_stats = deck_colors.get("All Decks", {}) or {}

    if lane_stats and any(safe_float(v) > 0 for v in lane_stats.values()):
        return lane_stats, lane_key, ()
    if global_stats:
        caveat = (f"No {lane_key} sample; using All Decks data.",) if lane_key else ()
        return global_stats, "All Decks", caveat
    return {}, "format prior", ("No card statistics; using format prior.",)


def adjust_card_statistics(
    card: Dict,
    lane_key: str,
    format_mean: float = DEFAULT_FORMAT_MEAN,
    archetype_prior: Optional[float] = None,
    prior_strength: int = 1200,
) -> AdjustedStatistics:
    stats, source, caveats = select_stats(card, lane_key)
    samples = sample_count(stats)
    prior = archetype_prior if archetype_prior and archetype_prior > 0 else format_mean

    raw_gihwr = _metric(stats, "gihwr", ("ever_drawn_win_rate",))
    gihwr, weight = shrink_metric(
        raw_gihwr, samples, prior, prior_strength=prior_strength
    )

    raw_ohwr = _metric(stats, "ohwr", ("opening_hand_win_rate",))
    ohwr, oh_weight = shrink_metric(
        raw_ohwr, samples, prior, prior_strength=prior_strength
    )

    raw_gpwr = _metric(stats, "gpwr", ("win_rate",))
    gpwr, gp_weight = shrink_metric(
        raw_gpwr, samples, prior, prior_strength=prior_strength
    )

    raw_iwd = _metric(stats, "iwd", ("drawn_improvement_win_rate",))
    # IWD is centered around zero, so its relevant prior is zero rather than WR.
    iwd, iwd_weight = shrink_centered_metric(
        raw_iwd,
        samples,
        prior=0.0,
        prior_strength=max(600, prior_strength // 2),
    )

    missing = []
    if raw_gihwr <= 0:
        missing.append("GIH win rate unavailable")
    if raw_ohwr <= 0:
        missing.append("opening-hand win rate unavailable")
    if raw_iwd == 0:
        missing.append("IWD unavailable")
    if samples <= 0:
        missing.append("sample size unavailable")
    elif samples < 500:
        missing.append(f"small sample ({samples:,})")

    available_weights = [
        value
        for raw, value in (
            (raw_gihwr, weight),
            (raw_ohwr, oh_weight),
            (raw_gpwr, gp_weight),
            (raw_iwd, iwd_weight),
        )
        if raw != 0
    ]
    confidence = sum(available_weights) / max(1, len(available_weights))

    return AdjustedStatistics(
        gihwr=gihwr,
        ohwr=ohwr,
        iwd=iwd,
        gpwr=gpwr,
        alsa=_metric(stats, "alsa", ("avg_seen",)),
        play_rate=_metric(stats, "play_rate"),
        samples=samples,
        confidence=max(0.0, min(1.0, confidence)),
        source=source,
        caveats=tuple(dict.fromkeys((*caveats, *missing))),
    )


def composite_power(stats: AdjustedStatistics, format_mean: float) -> float:
    """Return a centered power contribution, not a causal win-rate claim."""

    gih = stats.gihwr - format_mean
    opening = stats.ohwr - format_mean
    game = stats.gpwr - format_mean
    return (gih * 0.55) + (opening * 0.15) + (game * 0.10) + (stats.iwd * 0.20)
