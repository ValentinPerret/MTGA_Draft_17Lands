"""Innistrad Planar Cube roles and nonlinear package requirements.

This module intentionally contains data rather than scoring conditionals. The generic
`SynergyGraph` consumes the same shape for every package.
"""

ROLE_PATTERNS = {
    "human": {"subtypes": ["Human"]},
    "human_payoff": {
        "tags": ["human_payoff", "tribal_humans", "lord"],
        "text": ["humans you control", "other human", "human creatures you control"],
    },
    "zombie": {"subtypes": ["Zombie"]},
    "zombie_payoff": {
        "tags": ["zombie_payoff", "tribal_zombies", "lord"],
        "text": ["zombies you control", "other zombie", "zombie creatures you control"],
    },
    "vampire": {"subtypes": ["Vampire"]},
    "vampire_payoff": {
        "tags": ["vampire_payoff", "tribal_vampires", "lord"],
        "text": ["vampires you control", "other vampire", "vampire creatures you control"],
    },
    "discard_outlet": {
        "tags": ["discard_outlet", "discard", "looter"],
        "text": ["discard a card:", "discard a card,", "you may discard a card"],
    },
    "madness_payoff": {"tags": ["madness", "madness_payoff"], "text": ["madness"]},
    "reanimation_spell": {
        "tags": ["reanimation", "reanimate"],
        "text": ["return target creature card from your graveyard to the battlefield"],
    },
    "reanimation_target": {
        "tags": ["reanimation_target", "finisher"],
        "minimum_cmc": 6,
        "requires_types": ["Creature"],
    },
    "self_mill": {
        "tags": ["self_mill", "mill", "synergy_graveyard"],
        "text": ["mill ", "put the top", "cards of your library into your graveyard"],
    },
    "graveyard_payoff": {
        "tags": ["graveyard_payoff", "synergy_graveyard", "recursion"],
        "text": ["cards in your graveyard", "from your graveyard"],
    },
    "spider_spawning": {"names": ["Spider Spawning"]},
    "cheap_spell": {"types": ["Instant", "Sorcery"], "maximum_cmc": 3},
    "spells_payoff": {
        "tags": ["spells_matter", "prowess", "magecraft"],
        "text": ["instant or sorcery spell", "noncreature spell"],
    },
    "sacrifice_outlet": {
        "tags": ["sacrifice_outlet", "sacrifice"],
        "text": ["sacrifice another", "sacrifice a creature:"],
    },
    "sacrifice_fodder": {
        "tags": ["sacrifice_fodder", "token_maker", "recursive"],
    },
    "death_payoff": {
        "tags": ["death_payoff", "aristocrats"],
        "text": ["whenever another creature dies", "whenever a creature you control dies"],
    },
    "recursive_creature": {
        "tags": ["recursive", "recursion"],
        "text": ["return this card from your graveyard", "cast this card from your graveyard"],
        "requires_types": ["Creature"],
    },
    "creature": {"types": ["Creature"]},
    "interaction": {
        "tags": ["removal", "counterspell", "combat_trick", "protection"],
    },
    "fixing": {"tags": ["fixing_ramp"], "types": ["Land"]},
}


PACKAGE_RULES = {
    "humans": {
        "requirements": {"human": 5, "human_payoff": 1},
        "diminishing_returns": {"human": 14, "human_payoff": 3},
        "maximum_bonus": 5.0,
        "future_need": "More cheap Humans or an independently playable Human payoff",
    },
    "zombies": {
        "requirements": {"zombie": 5, "zombie_payoff": 1, "recursive_creature": 1},
        "diminishing_returns": {"zombie": 14, "zombie_payoff": 3},
        "maximum_bonus": 6.0,
        "future_need": "Zombie density plus a lord, exploit outlet, or recursion",
    },
    "madness": {
        "requirements": {"discard_outlet": 3, "madness_payoff": 3},
        "diminishing_returns": {"discard_outlet": 6, "madness_payoff": 7},
        "maximum_bonus": 7.0,
        "future_need": "Balance repeatable discard outlets with playable madness cards",
    },
    "reanimator": {
        "requirements": {
            "reanimation_spell": 2,
            "discard_outlet": 2,
            "reanimation_target": 2,
        },
        "diminishing_returns": {"reanimation_spell": 4, "reanimation_target": 5},
        "maximum_bonus": 7.5,
        "future_need": "Add redundant reanimation, setup, and high-quality targets",
    },
    "spider_spawning": {
        "requirements": {
            "spider_spawning": 1,
            "self_mill": 3,
            "creature": 10,
            "graveyard_payoff": 2,
        },
        "diminishing_returns": {"self_mill": 7, "creature": 18},
        "maximum_bonus": 9.0,
        "future_need": "Several self-mill cards, high creature density, and stabilization",
    },
    "spells": {
        "requirements": {"cheap_spell": 7, "spells_payoff": 2, "interaction": 3},
        "diminishing_returns": {"cheap_spell": 13, "spells_payoff": 5},
        "maximum_bonus": 6.5,
        "future_need": "Cheap instants/sorceries plus threats that reward casting them",
    },
    "sacrifice": {
        "requirements": {"sacrifice_outlet": 2, "sacrifice_fodder": 4, "death_payoff": 2},
        "diminishing_returns": {"sacrifice_outlet": 4, "sacrifice_fodder": 9},
        "maximum_bonus": 7.0,
        "future_need": "Maintain a reliable mix of fodder, outlets, and death payoffs",
    },
    "vampires": {
        "requirements": {"vampire": 5, "vampire_payoff": 1, "discard_outlet": 1},
        "diminishing_returns": {"vampire": 14, "vampire_payoff": 3},
        "maximum_bonus": 5.5,
        "future_need": "More independently strong Vampires and blood/discard support",
    },
}
