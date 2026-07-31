from src.advisor_v2.deck_quality import DeckQualityModel
from src.advisor_v2.deck_search import DeckSearch
from src.advisor_v2.models import LaneEstimate
from src.advisor_v2.statistics import adjust_card_statistics
from src.advisor_v2.synergy import SynergyGraph


def spell(name, wr, cmc=3, colors=None, tags=None):
    colors = colors or ["W"]
    return {
        "name": name,
        "colors": colors,
        "types": ["Creature"],
        "cmc": cmc,
        "mana_cost": "{2}{W}",
        "tags": tags or [],
        "deck_colors": {
            "All Decks": {
                "gihwr": wr,
                "ohwr": wr - 1,
                "gpwr": wr - 2,
                "samples": 10_000,
            }
        },
    }


def adjusted(cards):
    return {
        id(card): adjust_card_statistics(card, "WU", format_mean=54.0)
        for card in cards
    }


def test_candidate_replaces_weakest_card_in_full_shell():
    pool = [spell(f"Playable {i}", 57.0) for i in range(21)]
    weak = spell("Weakest card", 47.0, cmc=6)
    pool.append(weak)
    candidate = spell("Premium replacement", 63.0, cmc=2, tags=["removal"])
    lane = LaneEstimate("WU", ("W", "U"), 1.0)
    graph = SynergyGraph()
    search = DeckSearch(DeckQualityModel(54.0, graph), graph)

    baseline = search.best_shell(pool, lane, adjusted(pool))
    new_pool = [*pool, candidate]
    with_candidate = search.best_shell(new_pool, lane, adjusted(new_pool))

    assert any(card is candidate for card in with_candidate.cards)
    assert search.replacement(baseline, with_candidate, candidate) == "Weakest card"
    assert with_candidate.quality > baseline.quality


def test_unsupported_off_color_card_does_not_make_two_color_shell():
    pool = [spell(f"Playable {i}", 57.0) for i in range(22)]
    candidate = spell("Black card", 64.0, colors=["B"])
    lane = LaneEstimate("WU", ("W", "U"), 1.0)
    graph = SynergyGraph()
    search = DeckSearch(DeckQualityModel(54.0, graph), graph)
    cards = [*pool, candidate]

    shell = search.best_shell(cards, lane, adjusted(cards))
    assert not any(card is candidate for card in shell.cards)
