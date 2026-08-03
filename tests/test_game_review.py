import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.game_review.analyzer import analyze_game
from src.game_review.codex_reviewer import CodexGameReviewer, build_game_review_payload
from src.game_review.deck_advisor import recommend_deck_changes
from src.game_review.models import GameDecision, GameIndicators, GameSnapshot
from src.game_review.parser import ArenaGameLogParser, LocalCardCatalog
from src.game_review.store import GameReviewStore, match_key


def _gre(*messages):
    return {"greToClientEvent": {"greToClientMessages": list(messages)}}


def _write_limited_game_log(tmp_path, *, play_land=True):
    sets = tmp_path / "Sets"
    sets.mkdir()
    (sets / "EOE_QuickDraft_All_Data.json").write_text(
        json.dumps(
            {
                "card_ratings": {
                    "10": {
                        "name": "Island",
                        "types": ["Land", "Basic"],
                        "mana_cost": "",
                        "cmc": 0,
                        "colors": ["U"],
                        "deck_colors": {"All Decks": {"gihwr": 50.0}},
                    },
                    "20": {
                        "name": "Careful Researcher",
                        "types": ["Creature"],
                        "mana_cost": "{1}{U}",
                        "cmc": 2,
                        "colors": ["U"],
                        "oracle_text": "When this enters, surveil 1.",
                        "deck_colors": {"All Decks": {"gihwr": 54.0}},
                    },
                    "30": {
                        "name": "Sideboard Adept",
                        "types": ["Creature"],
                        "mana_cost": "{U}",
                        "cmc": 1,
                        "colors": ["U"],
                        "oracle_text": "When this enters, draw then discard.",
                        "deck_colors": {"All Decks": {"gihwr": 57.0}},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    room = {
        "matchGameRoomStateChangedEvent": {
            "gameRoomInfo": {
                "gameRoomConfig": {
                    "matchId": "private-match-id",
                    "reservedPlayers": [
                        {
                            "platformId": "SteamMac",
                            "systemSeatId": 1,
                            "teamId": 1,
                            "eventId": "PremierDraft_EOE_20260728",
                        },
                        {"platformId": "iPhone", "systemSeatId": 2, "teamId": 2},
                    ],
                }
            }
        }
    }
    connect = _gre(
        {
            "type": "GREMessageType_ConnectResp",
            "systemSeatIds": [1],
            "msgId": 1,
            "connectResp": {
                "deckMessage": {
                    "deckCards": [10] * 17 + [20] * 23,
                    "sideboardCards": [30] * 15,
                }
            },
        }
    )
    opening = _gre(
        {
            "type": "GREMessageType_GameStateMessage",
            "systemSeatIds": [1],
            "msgId": 2,
            "gameStateMessage": {
                "gameInfo": {
                    "matchID": "private-match-id",
                    "gameNumber": 1,
                    "stage": "GameStage_Start",
                    "superFormat": "SuperFormat_Limited",
                },
                "players": [
                    {"systemSeatNumber": 1, "teamId": 1, "lifeTotal": 20},
                    {"systemSeatNumber": 2, "teamId": 2, "lifeTotal": 20},
                ],
                "turnInfo": {"turnNumber": 0, "activePlayer": 1},
                "zones": [
                    {
                        "zoneId": 31,
                        "type": "ZoneType_Hand",
                        "ownerSeatId": 1,
                        "objectInstanceIds": [101, 102, 103, 104, 105, 106, 107],
                    }
                ],
                "gameObjects": [
                    {
                        "instanceId": 101,
                        "grpId": 10,
                        "cardTypes": ["CardType_Land"],
                    },
                    *[
                        {
                            "instanceId": instance,
                            "grpId": 20,
                            "cardTypes": ["CardType_Creature"],
                        }
                        for instance in range(102, 108)
                    ],
                ],
            },
        },
        {
            "type": "GREMessageType_MulliganReq",
            "systemSeatIds": [1],
            "msgId": 12,
            "gameStateId": 2,
            "mulliganReq": {"mulliganType": "MulliganType_London"},
        },
    )
    keep = {
        "payload": {
            "type": "ClientMessageType_MulliganResp",
            "gameStateId": 2,
            "respId": 12,
            "mulliganResp": {"decision": "MulliganOption_AcceptHand"},
        }
    }
    main_phase = _gre(
        {
            "type": "GREMessageType_GameStateMessage",
            "systemSeatIds": [1],
            "msgId": 19,
            "gameStateMessage": {
                "turnInfo": {
                    "turnNumber": 1,
                    "phase": "Phase_Main1",
                    "activePlayer": 1,
                }
            },
        },
        {
            "type": "GREMessageType_ActionsAvailableReq",
            "systemSeatIds": [1],
            "msgId": 20,
            "gameStateId": 3,
            "actionsAvailableReq": {
                "actions": [
                    {"actionType": "ActionType_Play", "instanceId": 101, "grpId": 10},
                    {"actionType": "ActionType_Pass"},
                ]
            },
        },
    )
    chosen_action = (
        {"actionType": "ActionType_Play", "instanceId": 101, "grpId": 10}
        if play_land
        else {"actionType": "ActionType_Pass"}
    )
    response = {
        "payload": {
            "type": "ClientMessageType_PerformActionResp",
            "gameStateId": 3,
            "respId": 20,
            "performActionResp": {"actions": [chosen_action]},
        }
    }
    game_over = _gre(
        {
            "type": "GREMessageType_GameStateMessage",
            "systemSeatIds": [1],
            "msgId": 30,
            "gameStateMessage": {
                "gameInfo": {
                    "matchID": "private-match-id",
                    "gameNumber": 1,
                    "stage": "GameStage_GameOver",
                    "superFormat": "SuperFormat_Limited",
                    "results": [
                        {
                            "scope": "MatchScope_Game",
                            "winningTeamId": 1,
                            "reason": "ResultReason_Game",
                        }
                    ],
                },
                "turnInfo": {"turnNumber": 7},
            },
        }
    )

    records = [room, connect, opening, keep, main_phase, response, game_over]
    log_path = tmp_path / "Player.log"
    lines = []
    for index, record in enumerate(records):
        lines.append(
            f"[UnityCrossThreadLogger]8/2/2026 7:0{index}:00 PM: gameplay event\n"
        )
        lines.append(json.dumps(record, indent=2) + "\n")
    log_path.write_text("".join(lines), encoding="utf-8")
    return log_path, sets


def _parse_fixture(tmp_path, *, play_land=True):
    log_path, sets = _write_limited_game_log(tmp_path, play_land=play_land)
    parser = ArenaGameLogParser(LocalCardCatalog(str(sets)))
    games = parser.parse(str(log_path))
    assert len(games) == 1
    return games[0]


def test_parser_reconstructs_completed_limited_game(tmp_path):
    game = _parse_fixture(tmp_path)

    assert game.completed is True
    assert game.limited is True
    assert game.result == "Win"
    assert game.event_id == "PremierDraft_EOE_20260728"
    assert game.turns == 7
    assert [decision.kind for decision in game.decisions] == ["mulligan", "action"]
    assert game.actions[0].action == "Play"
    assert game.actions[0].card.name == "Island"
    assert game.decisions[0].snapshot.hand[1].name == "Careful Researcher"
    assert len(game.deck_cards) == 40
    assert len(game.sideboard_cards) == 15
    assert sum("Land" in card.types for card in game.deck_cards) == 17
    assert game.sideboard_cards[0].name == "Sideboard Adept"
    assert len(game.deck_fingerprint) == 20


def test_analyzer_marks_risky_keep_and_possible_missed_land(tmp_path):
    game = _parse_fixture(tmp_path, play_land=False)
    review = analyze_game(game)

    categories = {finding.category for finding in review.findings}
    assert categories == {"mulligan", "mana"}
    assert any(finding.certainty == "possible" for finding in review.findings)
    assert all(finding.evidence for finding in review.findings)
    assert all(finding.better_line for finding in review.findings)


def test_store_hashes_match_identity_and_tracks_progress(tmp_path):
    game = _parse_fixture(tmp_path)
    store = GameReviewStore(str(tmp_path / "history.json"))
    stored = store.upsert_game(game, analyze_game(game))

    assert stored.match_key == match_key("private-match-id", 1)
    raw_history = (tmp_path / "history.json").read_text(encoding="utf-8")
    assert "private-match-id" not in raw_history
    assert store.progress().total_games == 1
    assert store.progress().wins == 1
    assert stored.deck_fingerprint == game.deck_fingerprint
    assert stored.indicators.deck_size == 40
    assert len(store.same_deck_games(game.deck_fingerprint)) == 1


def test_codex_payload_is_minimized_and_has_local_card_context(tmp_path):
    game = _parse_fixture(tmp_path)
    payload = build_game_review_payload(game)
    serialized = json.dumps(payload)

    assert "private-match-id" not in serialized
    assert "Player.log" not in serialized
    assert "Careful Researcher" in serialized
    assert "surveil 1" in serialized
    assert payload["decisions"][0]["decision_type"] == "mulligan"
    assert sum(card["count"] for card in payload["submitted_deck"]) == 40
    assert sum(card["count"] for card in payload["sideboard"]) == 15
    assert payload["same_deck_games_seen"] == 1


def test_repeated_same_deck_mana_screw_can_recommend_one_more_land(tmp_path):
    game = _parse_fixture(tmp_path)
    island = game.deck_cards[0]
    spell = next(card for card in game.deck_cards if "Land" not in card.types)
    game = game.model_copy(
        update={
            "decisions": [
                GameDecision(
                    kind="action",
                    snapshot=GameSnapshot(
                        turn=1,
                        phase="Main1",
                        active_seat=game.user_seat,
                        hand=[spell],
                        player_battlefield=[island],
                    ),
                ),
                GameDecision(
                    kind="action",
                    snapshot=GameSnapshot(
                        turn=3,
                        phase="Main1",
                        active_seat=game.user_seat,
                        hand=[spell],
                        player_battlefield=[island],
                    ),
                ),
            ]
        }
    )

    changes = recommend_deck_changes(
        game, [GameIndicators(mana_screw_signal=True, land_count=17, deck_size=40)]
    )

    assert len(changes) == 1
    assert changes[0].cut_card == "Careful Researcher"
    assert changes[0].add_card == "Island"
    assert changes[0].evidence_games == 2


def test_single_loss_does_not_trigger_result_oriented_deck_change(tmp_path):
    game = _parse_fixture(tmp_path).model_copy(update={"result": "Loss"})

    assert recommend_deck_changes(game) == []


def test_codex_reviewer_uses_strict_local_runner_and_validates_result(tmp_path):
    game = _parse_fixture(tmp_path).model_copy(update={"coverage": "full"})
    binary = tmp_path / "codex"
    binary.write_text("runner", encoding="utf-8")
    binary.chmod(0o755)

    result = {
        "summary": "The recorded line was mostly disciplined.",
        "strengths": ["The first land drop was made on time."],
        "focus_areas": ["Sequencing"],
        "findings": [
            {
                "category": "sequencing",
                "severity": "low",
                "certainty": "possible",
                "confidence": 0.7,
                "turn": 1,
                "title": "Consider the order of main-phase actions",
                "evidence": "Turn 1 recorded a legal land play and the chosen land play.",
                "better_line": "Check whether another action changes which land should be deployed.",
                "practice_tip": "Map the full turn before committing the first action.",
                "source": "codex",
            }
        ],
        "deck_changes": [
            {
                "cut_card": "Careful Researcher",
                "add_card": "Sideboard Adept",
                "quantity": 1,
                "priority": "low",
                "certainty": "possible",
                "confidence": 0.68,
                "evidence_games": 1,
                "evidence": "The exact submitted list contains a lower-curve sideboard option.",
                "rationale": "The swap can be tested without changing the deck's colors.",
                "expected_effect": "A slightly lower curve at a small power tradeoff.",
                "source": "codex",
            }
        ],
        "decision_feedback": [
            {
                "decision_index": 1,
                "turn": 99,
                "phase": "Untrusted phase",
                "observed_choice": "Untrusted paraphrase",
                "assessment": "reasonable",
                "confidence": 0.76,
                "headline": "Made the available land drop",
                "analysis": "Playing the Island developed mana for later turns.",
                "better_line": "Keep the recorded line because no stronger sequencing is visible.",
                "principle": "Develop mana before passing unless concealment has concrete value.",
                "source": "codex",
            }
        ],
    }
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["prompt"] = kwargs["input"]
        captured["env"] = kwargs["env"]
        schema_path = Path(command[command.index("--output-schema") + 1])
        captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(result), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch("src.game_review.codex_reviewer.subprocess.run", side_effect=fake_run):
        review = CodexGameReviewer(str(binary)).review(game, timeout_seconds=5)

    command = captured["command"]
    assert "--ephemeral" in command
    assert ["--sandbox", "read-only"] == command[
        command.index("--sandbox") : command.index("--sandbox") + 2
    ]
    assert "Player.log" not in captured["prompt"]
    assert "private-match-id" not in captured["prompt"]
    assert review.findings[0].source == "codex"
    assert review.deck_changes[0].add_card == "Sideboard Adept"
    assert review.decision_feedback[0].turn == 1
    assert review.decision_feedback[0].phase == "Main1"
    assert review.decision_feedback[0].observed_choice == game.decisions[1].choice

    schema_path = Path(command[command.index("--output-schema") + 1])
    assert schema_path.name == "schema.json"
    assert "deck_changes" in captured["schema"]["required"]
    assert "decision_feedback" in captured["schema"]["required"]
