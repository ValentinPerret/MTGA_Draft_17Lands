import pytest

from src.advisor_v2.synergy import SynergyGraph


def role_card(name, *, tags=None, subtypes=None, types=None, cmc=2, text=""):
    return {
        "name": name,
        "tags": tags or [],
        "subtypes": subtypes or [],
        "types": types or ["Creature"],
        "cmc": cmc,
        "oracle_text": text,
    }


def test_madness_bonus_is_nonlinear_and_requires_both_halves():
    graph = SynergyGraph("CubeDraft_Planar_Innistrad")
    thin = [
        role_card("Outlet", tags=["discard_outlet"]),
        role_card("Payoff", tags=["madness"]),
    ]
    supported = [
        *[role_card(f"Outlet {i}", tags=["discard_outlet"]) for i in range(3)],
        *[role_card(f"Payoff {i}", tags=["madness"]) for i in range(3)],
    ]

    assert graph.package_score(thin, "madness") < 1.5
    assert graph.package_score(supported, "madness") >= 6.5


def test_one_self_mill_card_does_not_enable_spider_spawning():
    graph = SynergyGraph("CubeDraft_Planar_Innistrad")
    thin = [
        role_card("Spider Spawning"),
        role_card("Satyr Wayfinder", tags=["self_mill"]),
    ]
    assert graph.package_score(thin, "spider_spawning") < 1.0


@pytest.mark.parametrize(
    "package,cards",
    [
        (
            "humans",
            [
                *[role_card(f"Human {i}", subtypes=["Human"]) for i in range(5)],
                role_card("Human Lord", tags=["human_payoff"]),
            ],
        ),
        (
            "zombies",
            [
                *[role_card(f"Zombie {i}", subtypes=["Zombie"]) for i in range(5)],
                role_card("Zombie Lord", tags=["zombie_payoff"]),
                role_card("Recursive Zombie", tags=["recursive"]),
            ],
        ),
        (
            "reanimator",
            [
                *[role_card(f"Reanimate {i}", tags=["reanimation"]) for i in range(2)],
                *[role_card(f"Outlet {i}", tags=["discard_outlet"]) for i in range(2)],
                *[role_card(f"Target {i}", tags=["finisher"], cmc=7) for i in range(2)],
            ],
        ),
        (
            "spells",
            [
                *[
                    role_card(f"Spell {i}", types=["Instant"], cmc=2, tags=["removal"])
                    for i in range(7)
                ],
                *[role_card(f"Prowess {i}", tags=["spells_matter"]) for i in range(2)],
            ],
        ),
        (
            "sacrifice",
            [
                *[role_card(f"Outlet {i}", tags=["sacrifice_outlet"]) for i in range(2)],
                *[role_card(f"Fodder {i}", tags=["sacrifice_fodder"]) for i in range(4)],
                *[role_card(f"Death {i}", tags=["death_payoff"]) for i in range(2)],
            ],
        ),
        (
            "vampires",
            [
                *[role_card(f"Vampire {i}", subtypes=["Vampire"]) for i in range(5)],
                role_card("Vampire Lord", tags=["vampire_payoff"]),
                role_card("Blood outlet", tags=["discard_outlet"]),
            ],
        ),
    ],
)
def test_innistrad_packages_reach_supported_threshold(package, cards):
    graph = SynergyGraph("CubeDraft_Planar_Innistrad")
    assert graph.package_score(cards, package) >= 4.5
