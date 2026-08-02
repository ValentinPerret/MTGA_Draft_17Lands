"""Conservative deterministic coaching rules for parsed MTGA games."""

from __future__ import annotations

from collections import defaultdict

from src.game_review.models import MatchReview, ParsedGame, ReviewFinding


def _is_land(card) -> bool:
    return bool(card and "Land" in card.types)


def _opening_hand_finding(game: ParsedGame):
    mulligans = [decision for decision in game.decisions if decision.kind == "mulligan"]
    if not mulligans:
        return None
    keep = next(
        (decision for decision in reversed(mulligans) if "AcceptHand" in decision.choice),
        None,
    )
    if keep is None or not keep.snapshot.hand:
        return None

    hand = keep.snapshot.hand
    lands = sum(1 for card in hand if _is_land(card))
    landcyclers = sum(1 for card in hand if "landcycling" in card.oracle_text.lower())
    effective_lands = lands + landcyclers
    hand_names = ", ".join(card.name for card in hand)

    if effective_lands == 0:
        return ReviewFinding(
            category="mulligan",
            severity="high",
            certainty="likely",
            confidence=0.93,
            turn=0,
            title="Kept a hand without a mana source",
            evidence=f"The kept opening hand contained no lands or known landcyclers: {hand_names}.",
            better_line="Take a mulligan unless the format has an unusual free-mana line that the log cannot represent.",
            practice_tip="Before keeping, count immediate sources, castable spells, and the first two draw-step outs.",
            source="log",
        )
    if effective_lands == 1 and len(hand) >= 6:
        return ReviewFinding(
            category="mulligan",
            severity="medium",
            certainty="possible",
            confidence=0.74,
            turn=0,
            title="Risky one-land keep",
            evidence=f"The kept {len(hand)}-card hand had one immediate/effective land: {hand_names}.",
            better_line="Prefer a mulligan unless the hand has several cheap plays and enough live draws to justify the risk.",
            practice_tip="Judge one-land hands by turn-two plays and colored-source outs, not only by overall card quality.",
            source="log",
        )
    if lands >= 6:
        return ReviewFinding(
            category="mulligan",
            severity="medium",
            certainty="likely",
            confidence=0.88,
            turn=0,
            title="Kept a heavily flooded opening hand",
            evidence=f"The kept opening hand contained {lands} lands among {len(hand)} cards.",
            better_line="Mulligan toward a hand with enough spells to affect the board before excess lands become a liability.",
            practice_tip="Most Limited keeps want a functional mix of two to four lands plus early interaction or creatures.",
            source="log",
        )
    return None


def _missed_land_drop_findings(game: ParsedGame):
    opportunities = defaultdict(list)
    for decision in game.decisions:
        snapshot = decision.snapshot
        if snapshot.active_seat != game.user_seat or "Main" not in snapshot.phase:
            continue
        playable_lands = [
            option.card
            for option in decision.options
            if option.action == "Play" and _is_land(option.card)
        ]
        if playable_lands:
            opportunities[snapshot.turn].extend(playable_lands)

    played_turns = {
        action.turn
        for action in game.actions
        if action.action == "Play" and _is_land(action.card)
    }
    findings = []
    for turn, lands in sorted(opportunities.items()):
        if turn in played_turns or turn <= 0:
            continue
        unique_names = list(dict.fromkeys(card.name for card in lands))
        findings.append(
            ReviewFinding(
                category="mana",
                severity="medium",
                certainty="possible",
                confidence=0.72,
                turn=turn,
                title=f"Possible missed land drop on turn {turn}",
                evidence=(
                    "Arena offered a legal land play during your main phase, but no land-play action "
                    f"was recorded that turn. Available: {', '.join(unique_names)}."
                ),
                better_line="Unless you were deliberately concealing information or preserving a land for a specific effect, deploy the land before ending the turn.",
                practice_tip="Use an end-of-turn check: land drop, attacks, second main, then pass.",
                source="log",
            )
        )
    return findings[:3]


def _timeout_finding(game: ParsedGame):
    if "timeout" not in game.result_reason.lower():
        return None
    return ReviewFinding(
        category="time_management",
        severity="high",
        certainty="confirmed",
        confidence=1.0,
        turn=game.turns,
        title="Lost to the game clock",
        evidence=f"Arena recorded the result reason as {game.result_reason}.",
        better_line="Identify the two or three plausible lines early, then spend the clock only on the decision that separates them.",
        practice_tip="Plan during the opponent's turn and set a personal decision deadline before the rope appears.",
        source="log",
    )


def analyze_game(game: ParsedGame) -> MatchReview:
    """Return only findings supported by observable log evidence."""
    findings = []
    opening = _opening_hand_finding(game)
    if opening:
        findings.append(opening)
    findings.extend(_missed_land_drop_findings(game))
    timeout = _timeout_finding(game)
    if timeout:
        findings.append(timeout)

    strengths = []
    if not any(finding.category == "mana" for finding in findings):
        strengths.append("No missed land drop was visible in the recorded decisions.")
    if not any(finding.category == "mulligan" for finding in findings):
        strengths.append("The opening-hand decision was not clearly problematic from the available evidence.")

    focus_areas = list(dict.fromkeys(finding.category.replace("_", " ").title() for finding in findings))
    if findings:
        summary = (
            f"The log supports {len(findings)} coaching point(s). "
            "Items marked possible depend on hidden information or card interactions the log may not fully expose."
        )
    elif game.coverage == "full":
        summary = (
            "No clear mistake was found by the conservative log checks. Use local Codex analysis "
            "for combat, target selection, and sequencing comparisons."
        )
    else:
        summary = (
            "The log contains too little decision detail for a reliable mistake call. "
            "Keep Detailed Logs enabled and scan again after a completed game."
        )

    return MatchReview(
        summary=summary,
        strengths=strengths[:5],
        focus_areas=focus_areas[:5],
        findings=findings[:10],
    )
