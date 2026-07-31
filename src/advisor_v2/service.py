"""End-to-end deterministic contextual recommendation service."""

from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

from src.advisor.schema import Recommendation
from src.advisor_v2.archetypes import LaneModel, lane_key
from src.advisor_v2.confidence import recommendation_confidence
from src.advisor_v2.deck_quality import DeckQualityModel
from src.advisor_v2.deck_search import DeckSearch
from src.advisor_v2.explanations import build_reasoning, future_considerations
from src.advisor_v2.model_router import ModelReviewRouter
from src.advisor_v2.models import CandidatePathResult, LaneEstimate
from src.advisor_v2.statistics import (
    adjust_card_statistics,
    composite_power,
    safe_float,
)
from src.advisor_v2.synergy import SynergyGraph
from src.advisor.mana_base import count_fixing
from src.card_logic import get_functional_cmc


class ContextualDraftAdvisor:
    MAX_ACTIVE_LANES = 4

    def __init__(
        self,
        set_metrics: Any,
        taken_cards: List[Dict],
        signals: Optional[Dict[str, float]] = None,
        event_name: str = "",
        draft_history: Optional[List[Dict]] = None,
        model_config: Any = None,
    ):
        self.metrics = set_metrics
        self.pool = taken_cards or []
        self.signals = signals or {}
        self.event_name = event_name or ""
        self.draft_history = draft_history or []
        self.model_router = ModelReviewRouter(model_config)
        self.last_model_decision = None

        self.global_mean, self.global_std = self.metrics.get_metrics("All Decks", "gihwr")
        if self.global_mean <= 0:
            self.global_mean = 54.0
        if self.global_std <= 0:
            self.global_std = 4.0

        self.synergy = SynergyGraph(self.event_name)
        self.quality = DeckQualityModel(self.global_mean, self.synergy)
        self.search = DeckSearch(self.quality, self.synergy)
        self.lanes = LaneModel(
            self.pool, self.signals, self.global_mean, self.global_std
        ).estimate()
        self.active_lanes = self._active_lanes(self.lanes)
        self._stats_cache = {}

    def evaluate_pack(
        self, pack_cards: List[Dict], current_pick: int, current_pack: int = 1
    ) -> List[Recommendation]:
        if not pack_cards:
            return []
        pack_number = max(1, min(3, int(current_pack or 1)))
        pick_number = max(1, int(current_pick or 1))
        stage_pick = ((pack_number - 1) * 15) + pick_number

        baselines = {}
        baseline_stats = {}
        for lane in self.active_lanes:
            adjusted = self._adjusted_map(self.pool, lane)
            baseline_stats[lane.name] = adjusted
            baselines[lane.name] = self.search.best_shell(self.pool, lane, adjusted)

        raw_rank = sorted(
            pack_cards,
            key=lambda card: safe_float(
                card.get("deck_colors", {}).get("All Decks", {}).get("gihwr")
            ),
            reverse=True,
        )
        raw_top_name = raw_rank[0].get("name") if raw_rank else ""

        provisional = []
        metadata = {}
        for card in pack_cards:
            evaluation_lanes = self._candidate_lanes(card)
            candidate_weights = self._normalized_lane_weights(evaluation_lanes)
            candidate_baselines = dict(baselines)
            candidate_baseline_stats = dict(baseline_stats)
            for lane in evaluation_lanes:
                if lane.name in candidate_baselines:
                    continue
                adjusted = self._adjusted_map(self.pool, lane)
                candidate_baseline_stats[lane.name] = adjusted
                candidate_baselines[lane.name] = self.search.best_shell(
                    self.pool, lane, adjusted
                )
            recommendation, details = self._evaluate_candidate(
                card,
                pack_cards,
                pack_number,
                pick_number,
                stage_pick,
                evaluation_lanes,
                candidate_weights,
                candidate_baselines,
                candidate_baseline_stats,
            )
            provisional.append(recommendation)
            metadata[id(recommendation)] = details

        provisional.sort(key=lambda rec: rec.contextual_score, reverse=True)
        lane_probability = self.active_lanes[0].probability if self.active_lanes else 0.0
        for index, rec in enumerate(provisional):
            next_score = (
                provisional[index + 1].contextual_score
                if index + 1 < len(provisional)
                else rec.contextual_score - 5.0
            )
            margin = max(0.0, rec.contextual_score - next_score)
            details = metadata[id(rec)]
            rec.confidence = recommendation_confidence(
                score_margin=margin,
                statistical_confidence=details["stat_confidence"],
                lane_probability=lane_probability,
                raw_context_agree=(rec.card_name == raw_top_name if index == 0 else True),
                speculative_fraction=1.0 - details["make_probability"],
                archetype_data_available=details["archetype_data"],
            )

        if provisional:
            margin = (
                provisional[0].contextual_score - provisional[1].contextual_score
                if len(provisional) > 1
                else 99.0
            )
            self.last_model_decision = self.model_router.decide(
                score_margin=margin,
                confidence=provisional[0].confidence,
                state_complete=bool(pack_cards),
            )
        return provisional

    def _evaluate_candidate(
        self,
        card: Dict,
        pack_cards: List[Dict],
        pack_number: int,
        pick_number: int,
        stage_pick: int,
        evaluation_lanes: List[LaneEstimate],
        lane_weights: Dict[str, float],
        baselines: Dict,
        baseline_stats: Dict,
    ) -> Tuple[Recommendation, Dict]:
        paths = []
        stat_confidence = 0.0
        archetype_data = False
        stat_caveats = []

        for lane in evaluation_lanes:
            adjusted = dict(baseline_stats[lane.name])
            candidate_stats = self._stats_for(card, lane)
            adjusted[id(card)] = candidate_stats
            stat_confidence += lane_weights[lane.name] * candidate_stats.confidence
            archetype_data |= candidate_stats.source not in {"All Decks", "format prior"}
            stat_caveats.extend(candidate_stats.caveats)

            baseline = baselines[lane.name]
            with_candidate = self.search.best_shell([*self.pool, card], lane, adjusted)
            makes_deck = any(item is card for item in with_candidate.cards)
            replacement = self.search.replacement(baseline, with_candidate, card)
            baseline_mana = self.quality.mana_risk(
                baseline.cards, baseline.land_count, lane
            )
            candidate_mana = self.quality.mana_risk(
                with_candidate.cards, with_candidate.land_count, lane
            )
            synergy_delta = self.synergy.total_score(with_candidate.cards) - self.synergy.total_score(
                baseline.cards
            )
            paths.append(
                CandidatePathResult(
                    lane=lane,
                    baseline=baseline,
                    with_candidate=with_candidate,
                    improvement=with_candidate.quality - baseline.quality,
                    makes_deck=makes_deck,
                    replacement_card=replacement,
                    mana_risk=max(0.0, candidate_mana - baseline_mana),
                    synergy_delta=synergy_delta,
                )
            )

        expected_improvement = sum(
            lane_weights[path.lane.name] * path.improvement for path in paths
        )
        make_probability = sum(
            lane_weights[path.lane.name] for path in paths if path.makes_deck
        )
        mana_risk = sum(
            lane_weights[path.lane.name] * path.mana_risk for path in paths
        )
        synergy_delta = sum(
            lane_weights[path.lane.name] * path.synergy_delta for path in paths
        )
        global_stats = adjust_card_statistics(
            card, "All Decks", format_mean=self.global_mean
        )
        z_score = (global_stats.gihwr - self.global_mean) / self.global_std
        making_paths = [path for path in paths if path.makes_deck]
        splash_supported = any(
            path.lane.splash_color and z_score >= 1.5 for path in making_paths
        )
        expected_to_make = make_probability >= 0.5 or splash_supported
        primary_path = max(
            making_paths if expected_to_make and making_paths else paths,
            key=lambda path: lane_weights[path.lane.name],
        )

        adjusted_power = composite_power(global_stats, self.global_mean) * 2.0
        progress = min(1.0, max(0.0, (stage_pick - 1) / 44.0))
        marginal_weight = 2.2 + (progress * 4.8)
        marginal_component = expected_improvement * marginal_weight
        synergy_component = synergy_delta * (1.4 + progress)
        option_value = self._option_value(card, progress)
        signal_value = self._signal_value(card)
        narrowness = self.synergy.narrowness_risk(card, self.pool)
        replacement_risk = -(1.0 - make_probability) * (2.0 + (progress * 10.0))

        components = {
            "adjusted_power": adjusted_power,
            "marginal_deck": marginal_component,
            "synergy": synergy_component,
            "option_value": option_value,
            "signal_value": signal_value,
            "mana_risk": -mana_risk * 1.8,
            "narrowness_risk": -narrowness,
            "replacement_risk": replacement_risk,
        }
        score = 50.0 + sum(components.values())
        is_basic = card.get("name") in {
            "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"
        } or (
            "Basic" in card.get("types", []) and "Land" in card.get("types", [])
        )
        if is_basic:
            score = 0.0

        package_delta, package = self.synergy.marginal_bonus(self.pool, card)
        reasons = build_reasoning(
            card, components, primary_path, primary_path.lane, package if package_delta > 0 else ""
        )
        caveats = list(dict.fromkeys(stat_caveats + primary_path.with_candidate.caveats))
        if not self.pool:
            caveats.append("Pool is empty; this is an early-pick projection.")
        if "cube" in self.event_name.lower() or "planar" in self.event_name.lower():
            caveats.append("Cube data must match the current module/date window.")

        reported_lanes = sorted(
            {lane.name: lane for lane in [*self.lanes[:5], *evaluation_lanes]}.values(),
            key=lambda lane: lane.probability,
            reverse=True,
        )
        lanes = {lane.name: round(lane.probability, 4) for lane in reported_lanes[:5]}
        future = future_considerations(
            primary_path.lane,
            self.synergy.future_need(package) if package_delta > 0 else "",
            mana_risk,
        )
        raw_wr = safe_float(
            card.get("deck_colors", {}).get("All Decks", {}).get("gihwr")
        )
        wheel = self._wheel_chance(card, pick_number)
        card_id = self._card_id(card)

        rec = Recommendation(
            card_name=str(card.get("name", "Unknown")),
            card_id=card_id,
            base_win_rate=raw_wr,
            contextual_score=round(max(0.0, min(100.0, score)), 1),
            z_score=round(z_score, 2),
            cast_probability=round(max(0.0, 1.0 - (mana_risk / 15.0)), 3),
            wheel_chance=wheel,
            functional_cmc=get_functional_cmc(card),
            reasoning=reasons,
            is_elite=(z_score >= 1.5 and make_probability >= 0.5),
            archetype_fit=primary_path.lane.name,
            tags=card.get("tags", []),
            lane_probabilities=lanes,
            expected_to_make_deck=expected_to_make,
            replacement_card=primary_path.replacement_card,
            score_components={key: round(value, 2) for key, value in components.items()},
            data_caveats=caveats[:6],
            future_considerations=future,
            engine="contextual_v2",
            model_assisted=False,
        )
        return rec, {
            "stat_confidence": stat_confidence,
            "make_probability": make_probability,
            "archetype_data": archetype_data,
        }

    def _stats_for(self, card: Dict, lane: LaneEstimate):
        key = (id(card), lane.name)
        if key not in self._stats_cache:
            stats_key = lane_key(lane.primary_colors)
            prior, _ = self.metrics.get_metrics(stats_key, "gihwr")
            self._stats_cache[key] = adjust_card_statistics(
                card,
                stats_key,
                format_mean=self.global_mean,
                archetype_prior=prior if prior > 0 else None,
            )
        return self._stats_cache[key]

    def _adjusted_map(self, cards: List[Dict], lane: LaneEstimate):
        return {id(card): self._stats_for(card, lane) for card in cards}

    def _active_lanes(self, lanes: List[LaneEstimate]) -> List[LaneEstimate]:
        if not lanes:
            return [LaneEstimate("WU", ("W", "U"), 1.0, ("Fallback lane",))]
        if len(self.pool) < 5:
            pair_lanes = [lane for lane in lanes if len(lane.colors) == 2]
            return pair_lanes[:10]

        selected = []
        pair_seen = False
        for lane in lanes:
            if len(lane.primary_colors) >= 2:
                pair_seen = True
            selected.append(lane)
            if len(selected) >= self.MAX_ACTIVE_LANES:
                break
        if not pair_seen:
            pair = next((lane for lane in lanes if len(lane.primary_colors) >= 2), None)
            if pair:
                selected[-1] = pair
        return selected

    def _normalized_active_lane_weights(self):
        return self._normalized_lane_weights(self.active_lanes)

    def _normalized_lane_weights(self, lanes: List[LaneEstimate]):
        total = sum(lane.probability for lane in lanes) or 1.0
        return {lane.name: lane.probability / total for lane in lanes}

    def _candidate_lanes(self, card: Dict) -> List[LaneEstimate]:
        lanes = list(self.active_lanes)
        if len(self.pool) < 8 or not lanes:
            return lanes
        primary = next(
            (lane for lane in lanes if len(lane.primary_colors) == 2 and not lane.splash_color),
            None,
        )
        if primary is None:
            return lanes
        card_colors = [color for color in card.get("colors", []) if color in "WUBRG"]
        off_colors = [color for color in card_colors if color not in primary.colors]
        if len(set(off_colors)) != 1:
            return lanes
        splash = off_colors[0]
        raw_wr = safe_float(
            card.get("deck_colors", {}).get("All Decks", {}).get("gihwr")
        )
        fixing = count_fixing(self.pool)
        if raw_wr < self.global_mean + self.global_std or fixing.get(splash, 0) < 2:
            return lanes
        name = f"{lane_key(primary.colors)} splash {splash}"
        if any(lane.name == name for lane in lanes):
            return lanes
        lanes.append(
            LaneEstimate(
                name=name,
                colors=(*primary.colors, splash),
                probability=min(0.18, primary.probability * 0.30),
                evidence=(
                    f"{fixing.get(splash, 0)} {splash} fixing sources",
                    f"Candidate is a high-power {splash} payoff",
                ),
                splash_color=splash,
            )
        )
        return lanes

    def _option_value(self, card: Dict, progress: float) -> float:
        colors = card.get("colors", [])
        flexibility = 2.5 if not colors else 1.7 if len(colors) == 1 else -0.8
        return flexibility * max(0.0, 1.0 - progress)

    def _signal_value(self, card: Dict) -> float:
        colors = [color for color in card.get("colors", []) if color in self.signals]
        if not colors:
            return 0.0
        average = sum(max(0.0, safe_float(self.signals[color])) for color in colors) / len(colors)
        if len(colors) >= 2:
            average *= 0.6
        return min(3.0, average * 0.05)

    def _wheel_chance(self, card: Dict, pick: int) -> float:
        returnable = card.get("returnable_at", [])
        if returnable:
            return 100.0
        alsa = safe_float(
            card.get("deck_colors", {}).get("All Decks", {}).get("alsa")
        )
        if alsa <= 0:
            return 0.0
        distance = alsa - (pick + 8)
        return round(max(0.0, min(90.0, 50.0 + (distance * 12.0))), 1)

    def _card_id(self, card: Dict) -> Optional[int]:
        value = card.get("arena_id")
        if value is None:
            ids = card.get("arena_ids", [])
            value = ids[0] if ids else None
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None
