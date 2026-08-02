"""Conservative, multi-game construction signals for submitted Limited decks."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable, List

from src import constants
from src.game_review.models import (
    DeckChange,
    GameCard,
    GameIndicators,
    ParsedGame,
)


def _is_land(card: GameCard | None) -> bool:
    return bool(card and "Land" in card.types)


def _unique_cards(cards: Iterable[GameCard]) -> List[GameCard]:
    by_name = {}
    for card in cards:
        by_name.setdefault(card.name, card)
    return list(by_name.values())


def _missed_land_turns(game: ParsedGame) -> List[int]:
    opportunities = set()
    for decision in game.decisions:
        snapshot = decision.snapshot
        if snapshot.active_seat != game.user_seat or "Main" not in snapshot.phase:
            continue
        if any(option.action == "Play" and _is_land(option.card) for option in decision.options):
            opportunities.add(snapshot.turn)
    played = {
        action.turn
        for action in game.actions
        if action.action == "Play" and _is_land(action.card)
    }
    return sorted(turn for turn in opportunities - played if turn > 0)[:5]


def build_game_indicators(game: ParsedGame) -> GameIndicators:
    """Extract only bounded signals that are useful across games with one deck."""
    mulligans = [decision for decision in game.decisions if decision.kind == "mulligan"]
    keep = next(
        (decision for decision in reversed(mulligans) if "AcceptHand" in decision.choice),
        None,
    )
    opening_hand_lands = (
        sum(_is_land(card) for card in keep.snapshot.hand)
        if keep and keep.snapshot.hand
        else None
    )

    own_turn_states = defaultdict(list)
    castable_by_turn = defaultdict(set)
    for decision in game.decisions:
        snapshot = decision.snapshot
        if snapshot.active_seat == game.user_seat and "Main" in snapshot.phase:
            own_turn_states[snapshot.turn].append(snapshot)
            castable_by_turn[snapshot.turn].update(
                option.card.name
                for option in decision.options
                if option.card
                and not _is_land(option.card)
                and option.action in {"Cast", "Play"}
            )

    mana_screw_signal = False
    flood_signal = False
    stranded_turns = defaultdict(set)
    for own_turn_index, turn in enumerate(sorted(own_turn_states), start=1):
        states = own_turn_states[turn]
        if own_turn_index <= 4:
            max_lands_in_play = max(
                (sum(_is_land(card) for card in state.player_battlefield) for state in states),
                default=0,
            )
            land_seen_in_hand = any(
                any(_is_land(card) for card in state.hand) for state in states
            )
            if own_turn_index >= 2 and max_lands_in_play < own_turn_index and not land_seen_in_hand:
                mana_screw_signal = True

        for state in states:
            if own_turn_index >= 6 and len(state.hand) >= 4:
                hand_lands = sum(_is_land(card) for card in state.hand)
                if hand_lands >= 3 and hand_lands / len(state.hand) >= 0.65:
                    flood_signal = True
            if own_turn_index >= 3:
                for card in state.hand:
                    if (
                        not _is_land(card)
                        and card.cmc >= 4
                        and card.name
                        and not card.name.startswith("Card ")
                        and card.name not in castable_by_turn[turn]
                    ):
                        stranded_turns[card.name].add(own_turn_index)

    stranded = sorted(
        (name for name, turns in stranded_turns.items() if len(turns) >= 3),
        key=lambda name: (-len(stranded_turns[name]), name),
    )[:5]
    return GameIndicators(
        deck_size=len(game.deck_cards),
        sideboard_size=len(game.sideboard_cards),
        land_count=sum(_is_land(card) for card in game.deck_cards),
        opening_hand_lands=opening_hand_lands,
        mulligan_count=max(0, len(mulligans) - 1),
        mana_screw_signal=mana_screw_signal,
        flood_signal=flood_signal,
        missed_land_drop_turns=_missed_land_turns(game),
        stranded_cards=stranded,
    )


def _lowest_rated_spell(cards: Iterable[GameCard]) -> GameCard | None:
    candidates = [card for card in _unique_cards(cards) if not _is_land(card) and card.gihwr]
    return min(candidates, key=lambda card: (card.gihwr, -card.cmc), default=None)


def _most_common_basic(cards: Iterable[GameCard]) -> str:
    names = Counter(
        card.name
        for card in cards
        if _is_land(card)
        and ("Basic" in card.types or card.name in constants.BASIC_LANDS)
    )
    return names.most_common(1)[0][0] if names else "Basic land"


def _best_sideboard_spell(game: ParsedGame, *, cheaper_than: float | None = None) -> GameCard | None:
    main_colors = {
        color
        for card in game.deck_cards
        if not _is_land(card)
        for color in card.colors
    }
    candidates = [
        card
        for card in _unique_cards(game.sideboard_cards)
        if not _is_land(card)
        and card.gihwr
        and (not card.colors or set(card.colors).issubset(main_colors))
        and (cheaper_than is None or card.cmc < cheaper_than)
    ]
    return max(candidates, key=lambda card: (card.gihwr, -card.cmc), default=None)


def recommend_deck_changes(
    game: ParsedGame,
    prior_indicators: Iterable[GameIndicators] = (),
) -> List[DeckChange]:
    """Recommend only construction changes supported by deck or repeated-game evidence."""
    if not game.deck_cards:
        return []

    current = build_game_indicators(game)
    samples = [current, *list(prior_indicators)[:19]]
    weakest_spell = _lowest_rated_spell(game.deck_cards)
    basic_name = _most_common_basic(game.deck_cards)

    if current.deck_size > 40 and weakest_spell:
        return [
            DeckChange(
                cut_card=weakest_spell.name,
                add_card="",
                quantity=1,
                priority="high",
                certainty="likely",
                confidence=0.96,
                evidence_games=1,
                evidence=f"The submitted main deck contained {current.deck_size} cards; Limited decks are most consistent at 40.",
                rationale="Removing the lowest-performing replaceable spell improves the odds of drawing the deck's best cards.",
                expected_effect="Higher draw quality without changing the mana base.",
                source="log",
            )
        ]

    screw_games = sum(indicator.mana_screw_signal for indicator in samples)
    flood_games = sum(indicator.flood_signal for indicator in samples)
    if current.land_count <= 15 and weakest_spell:
        return [
            DeckChange(
                cut_card=weakest_spell.name,
                add_card=basic_name,
                quantity=1,
                priority="high",
                certainty="likely",
                confidence=0.9,
                evidence_games=max(1, screw_games),
                evidence=f"The submitted deck had only {current.land_count} lands among {current.deck_size} cards.",
                rationale="Most 40-card Limited decks need about 17 mana sources unless the curve is exceptionally low.",
                expected_effect="More keepable opening hands and more reliable early land drops.",
                source="log",
            )
        ]

    if screw_games >= 2 and current.land_count <= 17 and weakest_spell:
        return [
            DeckChange(
                cut_card=weakest_spell.name,
                add_card=basic_name,
                quantity=1,
                priority="medium",
                certainty="possible",
                confidence=min(0.86, 0.62 + screw_games * 0.08),
                evidence_games=screw_games,
                evidence=f"Early mana development stalled without a land in hand in {screw_games} game(s) using this submitted deck.",
                rationale="A repeated pattern is more informative than one bad draw, though colored-source requirements still matter.",
                expected_effect="A modest reduction in early mana-screw risk.",
                source="log",
            )
        ]

    if flood_games >= 2 and current.land_count >= 17:
        addition = _best_sideboard_spell(game)
        if addition:
            return [
                DeckChange(
                    cut_card=basic_name,
                    add_card=addition.name,
                    quantity=1,
                    priority="low",
                    certainty="possible",
                    confidence=min(0.8, 0.58 + flood_games * 0.07),
                    evidence_games=flood_games,
                    evidence=f"Hands were heavily land-dense late in {flood_games} game(s) using this submitted deck.",
                    rationale="Testing one fewer land is reasonable only because the pattern repeated and an on-color sideboard spell is available.",
                    expected_effect="Slightly more action in long games, with a corresponding increase in mana risk.",
                    source="log",
                )
            ]

    stranded_counts = Counter(
        name for indicator in samples for name in indicator.stranded_cards
    )
    repeated_stranded = [name for name, count in stranded_counts.most_common() if count >= 2]
    main_by_name = {card.name: card for card in game.deck_cards}
    for name in repeated_stranded:
        cut = main_by_name.get(name)
        if not cut:
            continue
        addition = _best_sideboard_spell(game, cheaper_than=cut.cmc)
        if addition and (cut.gihwr is None or addition.gihwr >= cut.gihwr - 2.0):
            evidence_games = stranded_counts[name]
            return [
                DeckChange(
                    cut_card=cut.name,
                    add_card=addition.name,
                    quantity=1,
                    priority="medium",
                    certainty="possible",
                    confidence=min(0.82, 0.6 + evidence_games * 0.07),
                    evidence_games=evidence_games,
                    evidence=f"{cut.name} remained in hand across at least three of your turns in {evidence_games} game(s) with this deck.",
                    rationale="A cheaper on-color sideboard card may make the deck deploy its hand more reliably without a large data-quality loss.",
                    expected_effect="A smoother curve and fewer turns with expensive cards stranded in hand.",
                    source="log",
                )
            ]

    return []
