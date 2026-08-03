from src.advisor_v2.statistics import adjust_card_statistics, shrink_metric


def test_sample_aware_shrinkage_increases_with_sample_size():
    small, small_weight = shrink_metric(62.0, 100, 54.0)
    large, large_weight = shrink_metric(62.0, 20_000, 54.0)

    assert 54.0 < small < large < 62.0
    assert small_weight < large_weight <= 0.97


def test_missing_statistics_use_prior_instead_of_zero():
    adjusted = adjust_card_statistics(
        {"name": "Unknown", "deck_colors": {}}, "WU", format_mean=55.0
    )

    assert adjusted.gihwr == 55.0
    assert adjusted.confidence == 0.0
    assert any("No card statistics" in caveat for caveat in adjusted.caveats)


def test_archetype_falls_back_to_all_decks_with_visible_caveat():
    card = {
        "deck_colors": {
            "All Decks": {"gihwr": 58.0, "ohwr": 57.0, "samples": 5_000}
        }
    }
    adjusted = adjust_card_statistics(card, "UB", format_mean=54.0)

    assert adjusted.source == "All Decks"
    assert adjusted.gihwr > 54.0
    assert any("No UB sample" in caveat for caveat in adjusted.caveats)


def test_negative_iwd_remains_negative_after_shrinkage():
    card = {
        "deck_colors": {
            "All Decks": {"gihwr": 54.0, "iwd": -3.0, "samples": 5_000}
        }
    }
    adjusted = adjust_card_statistics(card, "All Decks", format_mean=54.0)
    assert -3.0 < adjusted.iwd < 0.0
