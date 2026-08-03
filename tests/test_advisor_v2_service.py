import time
from unittest.mock import MagicMock

from src.advisor.service import AdvisorService
from src.advisor_v2.service import ContextualDraftAdvisor
from src.configuration import Configuration


def metrics():
    result = MagicMock()

    def get_metrics(color, field):
        if field == "gihwr":
            return (54.0, 4.0)
        return (0.0, 0.0)

    result.get_metrics.side_effect = get_metrics
    return result


def card(name, wr, colors, cmc=3, tags=None, types=None, text=""):
    stats = {
        "gihwr": wr,
        "ohwr": wr - 1,
        "gpwr": wr - 2,
        "iwd": max(0.5, wr - 54.0),
        "alsa": 4.0,
        "samples": 12_000,
    }
    return {
        "name": name,
        "arena_ids": [abs(hash(name)) % 100_000 + 1],
        "colors": colors,
        "types": types or ["Creature"],
        "cmc": cmc,
        "mana_cost": "" if not colors else f"{{{max(0, cmc - 1)}}}{{{colors[0]}}}",
        "tags": tags or [],
        "oracle_text": text,
        "deck_colors": {"All Decks": stats, "WU": stats, "UB": stats},
    }


def test_p1p1_mostly_follows_flexible_raw_power():
    pack = [
        card("Best mono card", 64.0, ["G"]),
        card("Gold card", 60.0, ["W", "U"]),
        card("Filler", 54.0, ["W"]),
    ]
    recs = ContextualDraftAdvisor(metrics(), []).evaluate_pack(pack, 1, 1)

    assert recs[0].card_name == "Best mono card"
    assert recs[0].engine == "contextual_v2"
    assert recs[0].confidence > 0
    assert recs[0].lane_probabilities


def test_pack_three_card_that_enters_deck_beats_off_color_raw_rate():
    pool = [card(f"WU four drop {i}", 57.0, ["W", "U"], cmc=4) for i in range(24)]
    pack = [
        card("Off-color bomb", 66.0, ["B"], cmc=5),
        card("Needed early play", 58.0, ["W"], cmc=2, tags=["removal"]),
    ]
    recs = ContextualDraftAdvisor(metrics(), pool).evaluate_pack(pack, 8, 3)

    assert recs[0].card_name == "Needed early play"
    assert recs[0].expected_to_make_deck
    assert recs[0].replacement_card is not None


def test_bomb_can_create_splash_path_when_fixing_supports_it():
    pool = [card(f"White {i}", 57.0, ["W"], cmc=3) for i in range(10)]
    pool += [card(f"Blue {i}", 57.0, ["U"], cmc=3) for i in range(10)]
    pool += [
        card(
            f"Fixer {i}",
            54.0,
            [],
            cmc=0,
            tags=["fixing_ramp"],
            types=["Land"],
            text="Add one mana of any color",
        )
        for i in range(2)
    ]
    pack = [
        card("Splash bomb", 67.0, ["B"], cmc=6),
        card("Solid on-color", 59.0, ["W"], cmc=3),
    ]

    recs = ContextualDraftAdvisor(metrics(), pool).evaluate_pack(pack, 4, 2)
    splash = next(rec for rec in recs if rec.card_name == "Splash bomb")

    assert any("splash B" in lane for lane in splash.lane_probabilities)
    assert splash.expected_to_make_deck


def test_configuration_dispatches_contextual_engine():
    config = Configuration()
    config.settings.advisor_engine = "contextual_v2"
    recs = AdvisorService(
        metrics(), [], configuration=config
    ).evaluate_pack([card("Card", 58.0, ["R"])], 1, 1)
    assert recs[0].engine == "contextual_v2"


def test_full_pack_evaluation_meets_local_latency_target():
    pool = [
        card(f"Pool {i}", 53.0 + (i % 8), ["W"] if i % 2 else ["U"], cmc=(i % 6) + 1)
        for i in range(30)
    ]
    pack = [
        card(f"Candidate {i}", 52.0 + (i % 10), ["W"] if i % 3 else ["U"], cmc=(i % 6) + 1)
        for i in range(15)
    ]
    advisor = ContextualDraftAdvisor(metrics(), pool)
    started = time.perf_counter()
    recs = advisor.evaluate_pack(pack, 5, 2)
    elapsed = time.perf_counter() - started

    assert len(recs) == 15
    assert elapsed < 1.0
