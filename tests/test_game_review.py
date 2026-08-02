import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.game_review.analyzer import analyze_game
from src.game_review.codex_reviewer import CodexGameReviewer, build_game_review_payload
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
                    },
                    "20": {
                        "name": "Careful Researcher",
                        "types": ["Creature"],
                        "mana_cost": "{1}{U}",
                        "oracle_text": "When this enters, surveil 1.",
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


def test_codex_payload_is_minimized_and_has_local_card_context(tmp_path):
    game = _parse_fixture(tmp_path)
    payload = build_game_review_payload(game)
    serialized = json.dumps(payload)

    assert "private-match-id" not in serialized
    assert "Player.log" not in serialized
    assert "Careful Researcher" in serialized
    assert "surveil 1" in serialized
    assert payload["decisions"][0]["decision_type"] == "mulligan"


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
    }
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["prompt"] = kwargs["input"]
        captured["env"] = kwargs["env"]
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
