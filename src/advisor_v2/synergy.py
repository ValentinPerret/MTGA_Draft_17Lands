"""Generic nonlinear synergy graph backed by declarative role data."""

from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List, Set, Tuple

from src.advisor_v2.rulepacks.innistrad_planar import PACKAGE_RULES, ROLE_PATTERNS
from src.advisor_v2.statistics import safe_float


class SynergyGraph:
    def __init__(self, event_name: str = ""):
        self.event_name = event_name or ""
        self.role_patterns = ROLE_PATTERNS
        self.package_rules = PACKAGE_RULES
        self._role_cache: Dict[int, Set[str]] = {}

    def roles_for(self, card: Dict) -> Set[str]:
        cache_key = id(card)
        if cache_key in self._role_cache:
            return self._role_cache[cache_key]

        tags = {str(tag).lower() for tag in card.get("tags", [])}
        types = {str(value).lower() for value in card.get("types", [])}
        subtypes = {str(value).lower() for value in card.get("subtypes", [])}
        text = str(card.get("oracle_text", card.get("text", ""))).lower()
        name = str(card.get("name", ""))
        cmc = safe_float(card.get("cmc"))
        roles = set()

        for role, pattern in self.role_patterns.items():
            matched = False
            if any(value.lower() in tags for value in pattern.get("tags", [])):
                matched = True
            if any(value.lower() in types for value in pattern.get("types", [])):
                matched = True
            if any(value.lower() in subtypes for value in pattern.get("subtypes", [])):
                matched = True
            if any(value.lower() in text for value in pattern.get("text", [])):
                matched = True
            if name in pattern.get("names", []):
                matched = True
            if pattern.get("requires_types") and not all(
                value.lower() in types for value in pattern["requires_types"]
            ):
                matched = False
            if "minimum_cmc" in pattern and cmc < pattern["minimum_cmc"]:
                matched = False
            if "maximum_cmc" in pattern and cmc > pattern["maximum_cmc"]:
                matched = False
            if matched:
                roles.add(role)

        self._role_cache[cache_key] = roles
        return roles

    def role_counts(self, cards: Iterable[Dict]) -> Counter:
        counts = Counter()
        for card in cards:
            copies = max(1, int(card.get("count", 1) or 1))
            for role in self.roles_for(card):
                counts[role] += copies
        return counts

    def package_score(self, cards: Iterable[Dict], package: str) -> float:
        rule = self.package_rules[package]
        counts = self.role_counts(cards)
        requirements = rule["requirements"]

        # A package is only as reliable as its scarcest necessary component.
        # Squaring the fulfillment creates the desired build-around threshold:
        # one incidental enabler gives little value, while a supported package
        # rises quickly once every component is present.
        fulfillment = [
            min(1.0, counts[role] / max(1, minimum))
            for role, minimum in requirements.items()
        ]
        reliability = min(fulfillment, default=0.0)
        breadth = sum(fulfillment) / max(1, len(fulfillment))
        score = rule["maximum_bonus"] * (reliability**2) * (0.65 + 0.35 * breadth)

        # Small rewards for surplus components stop at explicit diminishing caps.
        surplus = 0.0
        for role, cap in rule.get("diminishing_returns", {}).items():
            minimum = requirements.get(role, 0)
            if counts[role] > minimum:
                surplus += min(counts[role], cap) - minimum
        return score + min(1.0, surplus * 0.08)

    def package_scores(self, cards: Iterable[Dict]) -> Dict[str, float]:
        card_list = list(cards)
        return {
            package: self.package_score(card_list, package)
            for package in self.package_rules
        }

    def total_score(self, cards: Iterable[Dict]) -> float:
        scores = sorted(self.package_scores(cards).values(), reverse=True)
        if not scores:
            return 0.0
        # A deck can support a primary and secondary package, but should not stack
        # every incidental tribal label at full value.
        return scores[0] + (scores[1] * 0.35 if len(scores) > 1 else 0.0)

    def marginal_bonus(self, pool: List[Dict], candidate: Dict) -> Tuple[float, str]:
        before = self.package_scores(pool)
        after = self.package_scores([*pool, candidate])
        deltas = {name: after[name] - before[name] for name in before}
        package = max(deltas, key=deltas.get, default="")
        return (deltas.get(package, 0.0), package)

    def narrowness_risk(self, candidate: Dict, pool: List[Dict]) -> float:
        candidate_roles = self.roles_for(candidate)
        risk = 0.0
        for package, rule in self.package_rules.items():
            relevant = candidate_roles.intersection(rule["requirements"])
            if not relevant:
                continue
            supported = self.package_score(pool, package) / rule["maximum_bonus"]
            payoff_roles = {
                role
                for role in relevant
                if "payoff" in role
                or role in {"spider_spawning", "reanimation_spell", "spells_payoff"}
            }
            if payoff_roles:
                risk = max(risk, max(0.0, 1.0 - supported) * 4.0)
        return risk

    def dominant_package(self, cards: Iterable[Dict]) -> Tuple[str, float]:
        scores = self.package_scores(cards)
        if not scores:
            return "balanced", 0.0
        name = max(scores, key=scores.get)
        return name, scores[name]

    def future_need(self, package: str) -> str:
        return self.package_rules.get(package, {}).get("future_need", "")
