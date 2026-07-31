"""Concise explanation generation from inspectable score components."""

from __future__ import annotations

from typing import Dict, List, Optional

from src.advisor_v2.models import CandidatePathResult, LaneEstimate


def build_reasoning(
    card: Dict,
    components: Dict[str, float],
    primary_path: CandidatePathResult,
    lane: LaneEstimate,
    synergy_package: str,
) -> List[str]:
    reasons = []
    if primary_path.makes_deck:
        if primary_path.replacement_card:
            reasons.append(f"Replaces {primary_path.replacement_card} in the projected deck")
        else:
            reasons.append("Expected to make the projected final deck")
    else:
        reasons.append("Unlikely to make the current projected deck")

    if components.get("marginal_deck", 0.0) >= 1.0:
        reasons.append("Improves projected final-deck quality")
    if components.get("adjusted_power", 0.0) >= 2.0:
        reasons.append("Strong sample-adjusted card performance")
    if components.get("synergy", 0.0) >= 0.5 and synergy_package:
        reasons.append(f"Strengthens the {synergy_package.replace('_', ' ')} package")
    if components.get("option_value", 0.0) >= 1.0:
        reasons.append("Preserves early-draft flexibility")
    if components.get("signal_value", 0.0) >= 0.7:
        reasons.append("Supported by repeated open-lane signals")
    if components.get("mana_risk", 0.0) <= -1.0:
        reasons.append("Adds meaningful mana or splash risk")
    if not reasons:
        reasons.append(f"Best weighted fit for {lane.name}")
    return reasons[:4]


def future_considerations(
    lane: LaneEstimate, package_need: str, mana_risk: float
) -> List[str]:
    results = []
    if package_need:
        results.append(package_need)
    if mana_risk >= 2.0:
        results.append("Additional fixing would make this path safer")
    if lane.splash_color:
        results.append(f"Avoid more than two {lane.splash_color} splash cards")
    if not results:
        results.append(f"More quality cards in {lane.name} will strengthen this plan")
    return results[:3]
