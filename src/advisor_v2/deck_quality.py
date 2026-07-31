"""Projected final-deck quality model (a comparative score, not win rate)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Tuple

from src import constants
from src.advisor.mana_base import count_fixing
from src.advisor_v2.models import AdjustedStatistics, LaneEstimate
from src.advisor_v2.statistics import composite_power, safe_float
from src.advisor_v2.synergy import SynergyGraph
from src.card_logic import get_functional_cmc


class DeckQualityModel:
    def __init__(self, format_mean: float, synergy: SynergyGraph):
        self.format_mean = format_mean if format_mean > 0 else 54.0
        self.synergy = synergy

    def evaluate(
        self,
        cards: List[Dict],
        land_count: int,
        lane: LaneEstimate,
        adjusted: Dict[int, AdjustedStatistics],
    ) -> Tuple[float, Dict[str, float], List[str]]:
        spells = [card for card in cards if "Land" not in card.get("types", [])]
        roles = self.synergy.role_counts(spells)
        caveats = []

        powers = [
            composite_power(adjusted[id(card)], self.format_mean)
            for card in spells
            if id(card) in adjusted
        ]
        avg_power = sum(powers) / max(1, len(powers))
        power_score = 50.0 + (avg_power * 2.6)

        early = sum(
            1
            for card in spells
            if get_functional_cmc(card) <= 2
            and (
                "Creature" in card.get("types", [])
                or "interaction" in self.synergy.roles_for(card)
            )
        )
        top_end = sum(1 for card in spells if get_functional_cmc(card) >= 5)
        creatures = roles["creature"]
        board_presence = creatures + roles["sacrifice_fodder"] + roles["spells_payoff"]
        interaction = roles["interaction"]
        card_advantage = sum(
            1
            for card in spells
            if set(card.get("tags", [])).intersection(
                {"card_advantage", "recursion", "cantrip", "looter"}
            )
        )

        package, package_score = self.synergy.dominant_package(spells)
        spell_heavy = package == "spells" and package_score >= 2.0
        target_board = 8 if spell_heavy else 13

        curve_score = min(7.0, early * 0.9) - max(0, top_end - 5) * 1.2
        board_score = min(6.0, (board_presence / max(1, target_board)) * 6.0)
        interaction_score = min(5.0, interaction * 1.25)
        advantage_score = min(3.0, card_advantage * 0.9)
        synergy_score = min(9.0, self.synergy.total_score(spells))
        mana_risk = self.mana_risk(cards, land_count, lane)
        narrow_cards = sum(
            1
            for card in spells
            if set(self.synergy.roles_for(card)).intersection(
                {"spider_spawning", "reanimation_spell", "human_payoff", "zombie_payoff", "vampire_payoff"}
            )
        )
        narrow_penalty = max(0.0, narrow_cards - 3) * 0.6
        completeness_penalty = max(0, 22 - len(spells)) * 0.45

        if len(spells) < 22:
            caveats.append(f"Projected shell has only {len(spells)} known nonlands.")
        if mana_risk >= 4.0:
            caveats.append("Projected mana is strained for this lane.")
        if early < 5 and len(spells) >= 18:
            caveats.append("Projected curve lacks early plays.")

        breakdown = {
            "adjusted_power": power_score,
            "curve": curve_score,
            "board_presence": board_score,
            "interaction": interaction_score,
            "card_advantage": advantage_score,
            "synergy": synergy_score,
            "mana_risk": -mana_risk,
            "narrowness": -narrow_penalty,
            "incompleteness": -completeness_penalty,
        }
        quality = (
            power_score
            + curve_score
            + board_score
            + interaction_score
            + advantage_score
            + synergy_score
            - mana_risk
            - narrow_penalty
            - completeness_penalty
        )
        return max(0.0, min(100.0, quality)), breakdown, caveats

    def mana_risk(
        self, cards: List[Dict], land_count: int, lane: LaneEstimate
    ) -> float:
        spells = [card for card in cards if "Land" not in card.get("types", [])]
        nonbasics = [card for card in cards if "Land" in card.get("types", [])]
        pips = Counter()
        early_pips = Counter()
        for card in spells:
            cmc = get_functional_cmc(card)
            for symbol in re.findall(r"\{([^}]+)\}", str(card.get("mana_cost", ""))):
                options = [value for value in symbol.split("/") if value in constants.CARD_COLORS]
                if not options:
                    continue
                # Hybrid can be paid by either supported color; charge the first
                # lane-supported option rather than requiring both.
                chosen = next((value for value in options if value in lane.colors), options[0])
                pips[chosen] += 1
                if cmc <= 3:
                    early_pips[chosen] += 1

        active = [color for color in lane.colors if pips[color] > 0]
        if not active:
            return 0.0
        basics_available = max(0, land_count - len(nonbasics))
        total_pips = sum(pips[color] for color in active) or 1
        fixing = count_fixing(nonbasics)
        risk = 0.0

        for color in active:
            proportional = basics_available * (pips[color] / total_pips)
            sources = proportional + fixing.get(color, 0)
            target = 8.0 if early_pips[color] >= 3 else 6.0
            if lane.splash_color == color:
                target = 4.0
            shortage = max(0.0, target - sources)
            risk += shortage * (0.65 if color != lane.splash_color else 0.9)

        if lane.splash_color:
            splash_spells = sum(
                1 for card in spells if lane.splash_color in card.get("colors", [])
            )
            if splash_spells > 2:
                risk += (splash_spells - 2) * 2.0
        return min(15.0, risk)
