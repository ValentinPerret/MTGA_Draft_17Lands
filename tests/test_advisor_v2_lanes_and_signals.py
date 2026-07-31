from src.advisor_v2.archetypes import LaneModel
from src.advisor_v2.signals import SignalEvidenceModel


def card(name, colors, wr=58.0, tags=None):
    return {
        "name": name,
        "colors": colors,
        "types": ["Creature"],
        "tags": tags or [],
        "deck_colors": {"All Decks": {"gihwr": wr, "ata": 4.0}},
    }


def test_lane_model_keeps_probabilities_and_finds_supported_pair():
    pool = [card(f"Blue {i}", ["U"]) for i in range(6)]
    pool += [card(f"Black {i}", ["B"]) for i in range(6)]
    pool += [card("Gold payoff", ["U", "B"], 60.0)]

    lanes = LaneModel(pool, {"U": 12.0, "B": 9.0}, 54.0, 4.0).estimate()

    assert lanes[0].name == "UB"
    assert abs(sum(lane.probability for lane in lanes) - 1.0) < 1e-9
    assert len(lanes) >= 15


def test_multicolor_and_narrow_cards_are_weaker_signals():
    model = SignalEvidenceModel(54.0)
    mono = card("Mono", ["U"], 60.0)
    gold = card("Gold", ["U", "B"], 60.0)
    narrow = card("Narrow", ["U"], 60.0, tags=["build_around"])

    mono_score = model.observed_card_score(mono, pick=9)
    assert model.observed_card_score(gold, pick=9) < mono_score
    assert model.observed_card_score(narrow, pick=9) < mono_score


def test_actual_wheel_is_stronger_evidence():
    model = SignalEvidenceModel(54.0)
    premium = card("Premium", ["G"], 60.0)
    normal = model.observed_card_score(premium, pick=9)
    wheeled = model.observed_card_score(premium, pick=9, wheeled=True)
    assert wheeled > normal
