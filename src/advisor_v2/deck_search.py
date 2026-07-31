"""Bounded projected-deck search and marginal replacement analysis."""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

from src import constants
from src.advisor_v2.deck_quality import DeckQualityModel
from src.advisor_v2.models import AdjustedStatistics, DeckShell, LaneEstimate
from src.advisor_v2.statistics import composite_power
from src.advisor_v2.synergy import SynergyGraph
from src.card_logic import get_functional_cmc


class DeckSearch:
    def __init__(
        self,
        quality_model: DeckQualityModel,
        synergy: SynergyGraph,
        beam_width: int = 10,
    ):
        self.quality_model = quality_model
        self.synergy = synergy
        self.beam_width = max(4, beam_width)

    def best_shell(
        self,
        pool: List[Dict],
        lane: LaneEstimate,
        adjusted: Dict[int, AdjustedStatistics],
    ) -> DeckShell:
        spells = [
            card
            for card in pool
            if "Land" not in card.get("types", []) and self._eligible(card, lane)
        ]
        lands = [
            card
            for card in pool
            if "Land" in card.get("types", []) and self._useful_land(card, lane)
        ]

        independent = {
            id(card): self._independent_utility(card, adjusted.get(id(card)))
            for card in spells
        }
        ordered = sorted(spells, key=lambda card: independent[id(card)], reverse=True)

        package, package_score = self.synergy.dominant_package(spells)
        avg_cmc = sum(get_functional_cmc(card) for card in spells) / max(1, len(spells))
        if package == "spells" and package_score >= 2.0:
            targets = (22, 23)
        elif avg_cmc <= 2.45 and len(spells) >= 24:
            targets = (23, 24)
        else:
            targets = (22, 23, 24)

        if len(ordered) < min(targets):
            targets = (len(ordered),)

        candidates = []
        for target in targets:
            selected = self._beam_select(ordered, target, independent, lane)
            land_count = 40 - len(selected)
            selected_lands = self._select_lands(lands, lane, land_count)
            shell_cards = [*selected, *selected_lands]
            quality, breakdown, caveats = self.quality_model.evaluate(
                shell_cards, land_count, lane, adjusted
            )
            candidates.append(
                DeckShell(
                    lane=lane,
                    cards=shell_cards,
                    land_count=land_count,
                    quality=quality,
                    breakdown=breakdown,
                    archetype=package,
                    caveats=caveats,
                )
            )

        return max(candidates, key=lambda shell: shell.quality)

    def replacement(
        self, baseline: DeckShell, candidate_shell: DeckShell, candidate: Dict
    ) -> Optional[str]:
        if not any(card is candidate for card in candidate_shell.cards):
            return None
        before = Counter(card.get("name", "Unknown") for card in baseline.cards)
        after = Counter(card.get("name", "Unknown") for card in candidate_shell.cards)
        removed = list((before - after).elements())
        if removed:
            return removed[0]
        if "Land" in candidate.get("types", []):
            return "basic land slot"
        return None

    def _beam_select(
        self,
        ordered: List[Dict],
        target: int,
        independent: Dict[int, float],
        lane: LaneEstimate,
    ) -> List[Dict]:
        if target <= 0:
            return []
        # state: (estimated utility, selected cards, splash count)
        states: List[Tuple[float, List[Dict], int]] = [(0.0, [], 0)]
        total = len(ordered)
        for index, card in enumerate(ordered):
            remaining = total - index - 1
            next_states = []
            for score, selected, splash_count in states:
                if len(selected) + remaining >= target:
                    next_states.append((score, selected, splash_count))
                if len(selected) >= target:
                    continue
                is_splash = bool(
                    lane.splash_color and lane.splash_color in card.get("colors", [])
                )
                if is_splash and splash_count >= 2:
                    continue
                new_cards = [*selected, card]
                utility = score + independent[id(card)]
                utility += self._composition_delta(selected, card)
                next_states.append(
                    (utility, new_cards, splash_count + int(is_splash))
                )

            next_states.sort(
                key=lambda state: (
                    state[0] + self._partial_bonus(state[1]),
                    len(state[1]),
                ),
                reverse=True,
            )
            states = next_states[: self.beam_width]

        complete = [state for state in states if len(state[1]) == target]
        if not complete:
            return ordered[:target]
        return max(
            complete,
            key=lambda state: state[0] + (self.synergy.total_score(state[1]) * 1.5),
        )[1]

    def _independent_utility(
        self, card: Dict, stats: Optional[AdjustedStatistics]
    ) -> float:
        value = composite_power(stats, self.quality_model.format_mean) if stats else 0.0
        cmc = get_functional_cmc(card)
        roles = self.synergy.roles_for(card)
        if cmc <= 2 and ("creature" in roles or "interaction" in roles):
            value += 0.8
        if "interaction" in roles:
            value += 0.45
        if "fixing" in roles:
            value += 0.2
        if cmc >= 7:
            value -= 0.7
        return value

    def _composition_delta(self, selected: List[Dict], candidate: Dict) -> float:
        cmc = get_functional_cmc(candidate)
        roles = self.synergy.roles_for(candidate)
        early = sum(1 for card in selected if get_functional_cmc(card) <= 2)
        top = sum(1 for card in selected if get_functional_cmc(card) >= 5)
        interaction = sum(
            1 for card in selected if "interaction" in self.synergy.roles_for(card)
        )
        delta = 0.0
        if cmc <= 2 and early < 7:
            delta += 1.1
        if cmc >= 5 and top >= 5:
            delta -= 1.4
        if "interaction" in roles and interaction < 4:
            delta += 0.8
        return delta

    def _partial_bonus(self, selected: List[Dict]) -> float:
        if not selected:
            return 0.0
        roles = self.synergy.role_counts(selected)
        early = sum(1 for card in selected if get_functional_cmc(card) <= 2)
        top = sum(1 for card in selected if get_functional_cmc(card) >= 5)
        return (
            min(5, early) * 0.15
            + min(4, roles["interaction"]) * 0.18
            - max(0, top - 5) * 0.35
        )

    def _eligible(self, card: Dict, lane: LaneEstimate) -> bool:
        colors = [color for color in card.get("colors", []) if color in constants.CARD_COLORS]
        if not colors:
            return True
        if all(color in lane.primary_colors for color in colors):
            return True
        if lane.splash_color and all(color in lane.colors for color in colors):
            splash_pips = 0
            for symbol in re.findall(r"\{([^}]+)\}", str(card.get("mana_cost", ""))):
                if lane.splash_color in symbol.split("/"):
                    splash_pips += 1
            return splash_pips <= 1
        return False

    def _useful_land(self, card: Dict, lane: LaneEstimate) -> bool:
        colors = [color for color in card.get("colors", []) if color in constants.CARD_COLORS]
        text = str(card.get("oracle_text", card.get("text", ""))).lower()
        universal = "any color" in text or "basic land" in text
        return universal or not colors or any(color in lane.colors for color in colors)

    def _select_lands(
        self, lands: List[Dict], lane: LaneEstimate, land_count: int
    ) -> List[Dict]:
        def value(card):
            colors = set(card.get("colors", []))
            coverage = len(colors.intersection(lane.colors))
            text = str(card.get("oracle_text", card.get("text", ""))).lower()
            universal = int("any color" in text or "basic land" in text)
            return (universal, coverage)

        return sorted(lands, key=value, reverse=True)[:land_count]
