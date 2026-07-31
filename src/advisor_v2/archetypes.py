"""Probabilistic draft-lane estimation."""

from __future__ import annotations

import itertools
import math
from collections import Counter
from typing import Dict, List, Sequence, Tuple

from src import constants
from src.advisor.mana_base import count_fixing
from src.advisor_v2.models import LaneEstimate
from src.advisor_v2.statistics import DEFAULT_FORMAT_STD, safe_float


COLOR_ORDER = tuple(constants.CARD_COLORS)
TWO_COLOR_LANES = tuple(itertools.combinations(COLOR_ORDER, 2))


def lane_key(colors: Sequence[str]) -> str:
    order = {color: index for index, color in enumerate(COLOR_ORDER)}
    return "".join(sorted(dict.fromkeys(colors), key=lambda c: order.get(c, 99)))


class LaneModel:
    """Keep probability mass over plausible color paths instead of hard-locking."""

    def __init__(
        self,
        pool: List[Dict],
        signals: Dict[str, float],
        format_mean: float,
        format_std: float = DEFAULT_FORMAT_STD,
    ):
        self.pool = pool or []
        self.signals = signals or {}
        self.mean = format_mean if format_mean > 0 else 54.0
        self.std = format_std if format_std > 0 else DEFAULT_FORMAT_STD
        self.color_counts = Counter()
        self.gold_counts = Counter()

    def estimate(self) -> List[LaneEstimate]:
        raw: List[Tuple[str, Tuple[str, ...], float, List[str], str | None]] = []
        pair_scores = {}

        for pair in TWO_COLOR_LANES:
            score, evidence = self._score_pair(pair)
            name = lane_key(pair)
            pair_scores[pair] = score
            raw.append((name, pair, score, evidence, None))

        for color in COLOR_ORDER:
            score, evidence = self._score_mono(color)
            raw.append((color, (color,), score, evidence, None))

        raw.extend(self._splash_paths(pair_scores))

        # Early drafts should retain broad optionality. More drafted cards sharpen
        # the distribution without ever collapsing unsupported lanes to zero.
        temperature = max(0.75, 2.4 - min(1.65, len(self.pool) / 18.0))
        peak = max((item[2] for item in raw), default=0.0)
        weights = [math.exp((item[2] - peak) / temperature) for item in raw]
        total = sum(weights) or 1.0

        estimates = [
            LaneEstimate(
                name=name,
                colors=colors,
                probability=weight / total,
                evidence=tuple(evidence[:5]),
                splash_color=splash,
            )
            for (name, colors, _, evidence, splash), weight in zip(raw, weights)
        ]
        return sorted(estimates, key=lambda lane: lane.probability, reverse=True)

    def _score_pair(self, pair: Tuple[str, str]) -> Tuple[float, List[str]]:
        score = 0.0
        evidence = []
        counts = Counter()
        gold = 0
        pool_size = max(1, len(self.pool))

        for index, card in enumerate(self.pool):
            colors = tuple(c for c in card.get("colors", []) if c in COLOR_ORDER)
            if "Land" in card.get("types", []):
                continue
            for color in colors:
                self.color_counts[color] += 1
            if len(colors) >= 2:
                self.gold_counts[lane_key(colors[:2])] += 1

            wr = safe_float(
                card.get("deck_colors", {}).get("All Decks", {}).get("gihwr")
            )
            quality = 1.0 + max(-0.6, min(2.5, (wr - self.mean) / self.std))
            if wr <= 0:
                quality = 0.65
            recency = 0.8 + (0.7 * ((index + 1) / pool_size))

            if not colors:
                score += 0.08 * quality
            elif all(color in pair for color in colors):
                contribution = quality * recency
                if len(set(colors)) >= 2:
                    contribution *= 1.65
                    gold += 1
                score += contribution
                for color in set(colors):
                    counts[color] += 1
            elif any(color in pair for color in colors):
                # A speculative off-pair card has some option value, but much less
                # than a card that belongs to the lane.
                score += 0.08 * quality * recency

        for color in pair:
            signal = max(0.0, safe_float(self.signals.get(color)))
            score += min(2.5, signal * 0.04)
            if signal >= 10:
                evidence.append(f"Repeated late {color} cards")

        if all(counts[color] >= 3 for color in pair):
            score += 1.2
            evidence.append(f"Playables in both {pair[0]} and {pair[1]}")
        if gold:
            score += min(2.0, gold * 0.65)
            evidence.append(f"{gold} gold incentive{'s' if gold != 1 else ''}")
        if counts:
            leader = ", ".join(f"{c}:{counts[c]}" for c in pair)
            evidence.insert(0, f"Pool support {leader}")
        if not evidence:
            evidence.append("Open speculative lane")
        return score, evidence

    def _score_mono(self, color: str) -> Tuple[float, List[str]]:
        count = 0
        quality = 0.0
        for index, card in enumerate(self.pool):
            colors = [c for c in card.get("colors", []) if c in COLOR_ORDER]
            if colors == [color] and "Land" not in card.get("types", []):
                count += 1
                wr = safe_float(
                    card.get("deck_colors", {}).get("All Decks", {}).get("gihwr")
                )
                quality += 0.7 + max(-0.3, (wr - self.mean) / self.std)
        # Mono is real only with density; otherwise two-color paths keep the prior.
        score = (quality * 0.82) - 1.2
        score += min(1.5, max(0.0, safe_float(self.signals.get(color))) * 0.03)
        evidence = [f"{count} mono-{color} playables"] if count else ["Speculative mono base"]
        return score, evidence

    def _splash_paths(self, pair_scores):
        if len(self.pool) < 8:
            return []
        fixing = count_fixing(self.pool)
        ranked_pairs = sorted(pair_scores, key=pair_scores.get, reverse=True)[:3]
        paths = []
        for pair in ranked_pairs:
            for splash in COLOR_ORDER:
                if splash in pair or fixing.get(splash, 0) < 2:
                    continue
                payoffs = []
                for card in self.pool:
                    colors = card.get("colors", [])
                    wr = safe_float(
                        card.get("deck_colors", {})
                        .get("All Decks", {})
                        .get("gihwr")
                    )
                    if splash in colors and not any(c in pair for c in colors):
                        if wr >= self.mean + self.std:
                            payoffs.append(card.get("name", "payoff"))
                if not payoffs:
                    continue
                score = pair_scores[pair] - 1.7 + min(1.5, len(payoffs) * 0.6)
                colors = (*pair, splash)
                paths.append(
                    (
                        f"{lane_key(pair)} splash {splash}",
                        colors,
                        score,
                        [
                            f"{fixing.get(splash, 0)} {splash} fixing sources",
                            f"Splash payoff: {payoffs[0]}",
                        ],
                        splash,
                    )
                )
        return paths
