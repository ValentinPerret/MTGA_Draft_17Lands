"""Parse MTGA detailed logs into privacy-minimized Limited game timelines."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from src import constants
from src.game_review.models import (
    GameAction,
    GameCard,
    GameDecision,
    GameOption,
    GameSnapshot,
    ParsedGame,
)


_LOG_TIME = re.compile(r"(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)")


class LocalCardCatalog:
    """Resolve Arena group IDs only from already-loaded or downloaded local data."""

    def __init__(self, sets_folder: str, active_dataset=None):
        self.sets_folder = Path(sets_folder)
        self.active_dataset = active_dataset
        self._cards: Dict[str, Dict] = {}
        self._loaded_sets: set[str] = set()
        self._load_active_dataset()

    def _load_active_dataset(self):
        data = getattr(self.active_dataset, "_dataset", None)
        if isinstance(data, dict):
            self._cards.update(data.get("card_ratings", {}))

    def prime(self, event_id: str, card_ids: Iterable[int]):
        needed = {str(value) for value in card_ids if value and str(value) not in self._cards}
        if not needed:
            return

        set_code = self._set_code(event_id)
        if set_code and set_code not in self._loaded_sets:
            self._loaded_sets.add(set_code)
            patterns = [
                str(self.sets_folder / f"{set_code}_*_All_Data.json"),
                str(self.sets_folder / f"{set_code}_*_Top_Data.json"),
                str(self.sets_folder / f"{set_code}_*Data.json"),
            ]
            paths: List[str] = []
            for pattern in patterns:
                paths.extend(glob.glob(pattern))
            for path in dict.fromkeys(paths):
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        ratings = json.load(handle).get("card_ratings", {})
                    for card_id in tuple(needed):
                        if card_id in ratings:
                            self._cards[card_id] = ratings[card_id]
                            needed.discard(card_id)
                    if not needed:
                        break
                except (OSError, ValueError, TypeError):
                    continue

        if needed:
            temp_path = Path(constants.TEMP_CARD_DATA_FILE)
            if temp_path.exists():
                try:
                    with temp_path.open("r", encoding="utf-8") as handle:
                        raw_sets = json.load(handle)
                    candidates = raw_sets.get(set_code, {}) if set_code else {}
                    for card_id in tuple(needed):
                        if card_id in candidates:
                            self._cards[card_id] = candidates[card_id]
                            needed.discard(card_id)
                except (OSError, ValueError, TypeError):
                    pass

    @staticmethod
    def _set_code(event_id: str) -> str:
        parts = str(event_id or "").split("_")
        for part in parts:
            if 2 <= len(part) <= 5 and part.isalpha() and part.upper() == part:
                return part
        return ""

    def resolve(self, card_id: int) -> Dict:
        return self._cards.get(str(card_id), {})


def _parse_log_time(line: str) -> Optional[str]:
    match = _LOG_TIME.search(line)
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group(1), "%m/%d/%Y %I:%M:%S %p")
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        return None


def iter_log_json(path: str) -> Iterator[Tuple[str, Dict]]:
    """Yield top-level JSON blocks with the timestamp from their preceding marker."""
    current_time = ""
    buffer: List[str] = []
    buffer_size = 0

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parsed_time = _parse_log_time(line)
            if parsed_time:
                current_time = parsed_time

            if not buffer and not line.lstrip().startswith("{"):
                continue

            buffer.append(line)
            buffer_size += len(line)
            if buffer_size > 8_000_000:
                buffer.clear()
                buffer_size = 0
                continue

            try:
                value = json.loads("".join(buffer))
            except json.JSONDecodeError:
                continue

            buffer.clear()
            buffer_size = 0
            if isinstance(value, dict):
                yield current_time, value


class _GameBuilder:
    def __init__(self, catalog: LocalCardCatalog, played_at: str = ""):
        self.catalog = catalog
        self.played_at = played_at
        self.match_id = ""
        self.event_id = "Limited"
        self.game_number = 1
        self.user_seat = 1
        self.user_team = 1
        self.super_format = ""
        self.completed = False
        self.result = "In progress"
        self.result_reason = ""
        self.game_info: Dict = {}
        self.turn_info: Dict = {}
        self.players: Dict[int, Dict] = {}
        self.zones: Dict[int, Dict] = {}
        self.objects: Dict[int, Dict] = {}
        self.pending: Dict[int, GameDecision] = {}
        self.decisions: List[GameDecision] = []
        self.actions: List[GameAction] = []
        self.deck_card_ids: List[int] = []
        self.sideboard_card_ids: List[int] = []
        self.deck_cards: List[GameCard] = []
        self.sideboard_cards: List[GameCard] = []
        self.game_state_messages = 0

    def apply_metadata(self, metadata: Dict):
        if metadata.get("event_id"):
            self.event_id = metadata["event_id"]
        if metadata.get("user_seat"):
            self.user_seat = metadata["user_seat"]
        if metadata.get("user_team"):
            self.user_team = metadata["user_team"]

    def process_gre_message(self, message: Dict):
        seats = message.get("systemSeatIds", [])
        if len(seats) == 1:
            self.user_seat = int(seats[0])

        message_type = message.get("type", "")
        if message_type == "GREMessageType_ConnectResp":
            deck_message = message.get("connectResp", {}).get("deckMessage", {})
            self.deck_card_ids = [
                int(card_id)
                for card_id in deck_message.get("deckCards", [])
                if str(card_id).isdigit()
            ]
            self.sideboard_card_ids = [
                int(card_id)
                for card_id in deck_message.get("sideboardCards", [])
                if str(card_id).isdigit()
            ]
            return
        if message_type == "GREMessageType_GameStateMessage":
            self._apply_game_state(message.get("gameStateMessage", {}))
            return

        request_map = {
            "GREMessageType_ActionsAvailableReq": ("action", "actionsAvailableReq"),
            "GREMessageType_MulliganReq": ("mulligan", "mulliganReq"),
            "GREMessageType_DeclareAttackersReq": ("attack", "declareAttackersReq"),
            "GREMessageType_DeclareBlockersReq": ("block", "declareBlockersReq"),
            "GREMessageType_SelectNReq": ("selection", "selectNReq"),
            "GREMessageType_SelectTargetsReq": ("target", "selectTargetsReq"),
            "GREMessageType_PayCostsReq": ("payment", "payCostsReq"),
        }
        if message_type in request_map:
            kind, payload_key = request_map[message_type]
            request_id = int(message.get("msgId", 0))
            payload = message.get(payload_key, {})
            self.pending[request_id] = GameDecision(
                request_id=request_id,
                state_id=int(message.get("gameStateId", 0)),
                kind=kind,
                options=self._options(kind, payload),
                snapshot=self._snapshot(),
            )

    def _apply_game_state(self, state: Dict):
        self.game_state_messages += 1
        info = state.get("gameInfo")
        if isinstance(info, dict):
            self.game_info.update(info)
            self.match_id = str(info.get("matchID", self.match_id))
            self.game_number = int(info.get("gameNumber", self.game_number) or 1)
            self.super_format = str(info.get("superFormat", self.super_format))
            if info.get("stage") == "GameStage_GameOver":
                self.completed = True
                self._apply_result(info.get("results", []))

        for player in state.get("players", []):
            seat = int(player.get("systemSeatNumber", 0))
            if seat:
                self.players.setdefault(seat, {}).update(player)
        if self.user_seat in self.players:
            self.user_team = int(self.players[self.user_seat].get("teamId", self.user_team))

        if isinstance(state.get("turnInfo"), dict):
            self.turn_info.update(state["turnInfo"])

        for zone in state.get("zones", []):
            zone_id = int(zone.get("zoneId", 0))
            if zone_id:
                existing = self.zones.setdefault(zone_id, {})
                existing.update(zone)
                if "objectInstanceIds" not in zone:
                    existing["objectInstanceIds"] = []

        for game_object in state.get("gameObjects", []):
            instance_id = int(game_object.get("instanceId", 0))
            if instance_id:
                self.objects.setdefault(instance_id, {}).update(game_object)

    def _apply_result(self, results: Iterable[Dict]):
        for result in results:
            if result.get("scope") not in (None, "MatchScope_Game"):
                continue
            winner = int(result.get("winningTeamId", 0) or 0)
            self.result = "Win" if winner == self.user_team else "Loss"
            self.result_reason = str(result.get("reason", "")).replace("ResultReason_", "")
            return

    def process_client_response(self, payload: Dict):
        response_type = str(payload.get("type", ""))
        supported = {
            "ClientMessageType_PerformActionResp",
            "ClientMessageType_MulliganResp",
            "ClientMessageType_DeclareAttackersResp",
            "ClientMessageType_DeclareBlockersResp",
            "ClientMessageType_SelectNResp",
            "ClientMessageType_SelectTargetsResp",
            "ClientMessageType_EffectCostResp",
            "ClientMessageType_ConcedeReq",
        }
        if response_type not in supported:
            return
        if response_type == "ClientMessageType_ConcedeReq":
            self.actions.append(
                GameAction(
                    turn=self._turn(),
                    phase=self._phase(),
                    action="Concede",
                    detail="Player conceded the game",
                )
            )
            return

        response_id = int(payload.get("respId", 0) or 0)
        decision = self.pending.pop(response_id, None)
        if decision is None:
            decision = GameDecision(
                request_id=response_id,
                state_id=int(payload.get("gameStateId", 0) or 0),
                kind=self._kind_from_response(response_type),
                snapshot=self._snapshot(),
            )
        decision.choice = self._choice_text(payload, decision)
        self.decisions.append(decision)
        self._append_action(payload, decision)

    @staticmethod
    def _kind_from_response(response_type: str) -> str:
        lowered = response_type.lower()
        for token in ("mulligan", "attacker", "blocker", "select", "target", "cost"):
            if token in lowered:
                return token
        return "action"

    def _append_action(self, payload: Dict, decision: GameDecision):
        response_type = payload.get("type", "")
        if response_type == "ClientMessageType_PerformActionResp":
            actions = payload.get("performActionResp", {}).get("actions", [])
            for action in actions:
                action_name = str(action.get("actionType", "ActionType_Unknown")).replace("ActionType_", "")
                card = self._card_from_action(action)
                self.actions.append(
                    GameAction(
                        turn=decision.snapshot.turn,
                        phase=decision.snapshot.phase,
                        action=action_name,
                        card=card,
                        detail=decision.choice,
                    )
                )
        elif decision.kind in {"attack", "attacker"}:
            self.actions.append(
                GameAction(
                    turn=decision.snapshot.turn,
                    phase=decision.snapshot.phase,
                    action="Attack",
                    detail=decision.choice,
                )
            )
        elif decision.kind in {"block", "blocker"}:
            self.actions.append(
                GameAction(
                    turn=decision.snapshot.turn,
                    phase=decision.snapshot.phase,
                    action="Block",
                    detail=decision.choice,
                )
            )

    def _choice_text(self, payload: Dict, decision: GameDecision) -> str:
        response_type = str(payload.get("type", "")).replace("ClientMessageType_", "")
        if "mulliganResp" in payload:
            return str(payload["mulliganResp"].get("decision", "")).replace("MulliganOption_", "")
        if "performActionResp" in payload:
            parts = []
            for action in payload["performActionResp"].get("actions", []):
                action_name = str(action.get("actionType", "Unknown")).replace("ActionType_", "")
                card = self._card_from_action(action)
                parts.append(f"{action_name} {card.name}" if card else action_name)
            return ", ".join(parts) or "Pass"
        if "declareAttackersResp" in payload:
            response = payload["declareAttackersResp"]
            if response.get("autoDeclare"):
                attackers = [
                    option.card.name
                    for option in decision.options
                    if option.card is not None
                ]
                return (
                    f"Attack with {len(attackers)} creature(s): {', '.join(attackers)}"
                    if attackers
                    else "Submit attackers"
                )
            selected = response.get("selectedAttackers", [])
            return f"Attack with {len(selected)} creature(s)"
        if "declareBlockersResp" in payload:
            selected = payload["declareBlockersResp"].get("selectedBlockers", [])
            return f"Block with {len(selected)} creature(s)"
        if "selectNResp" in payload:
            return f"Select {len(payload['selectNResp'].get('ids', []))} option(s)"
        return response_type

    def _options(self, kind: str, payload: Dict) -> List[GameOption]:
        options: List[GameOption] = []
        if kind == "action":
            for action in payload.get("actions", []):
                action_name = str(action.get("actionType", "Unknown")).replace("ActionType_", "")
                options.append(GameOption(action=action_name, card=self._card_from_action(action)))
        elif kind == "mulligan":
            options = [GameOption(action="Keep"), GameOption(action="Mulligan")]
        elif kind == "attack":
            for attacker in payload.get("qualifiedAttackers", payload.get("attackers", [])):
                card = self._card(int(attacker.get("attackerInstanceId", 0)))
                options.append(GameOption(action="Attack", card=card))
        elif kind == "block":
            for blocker in payload.get("blockers", []):
                card = self._card(int(blocker.get("blockerInstanceId", 0)))
                options.append(GameOption(action="Block", card=card))
        elif kind == "selection":
            for instance_id in payload.get("ids", []):
                options.append(GameOption(action="Select", card=self._card(int(instance_id))))
        else:
            options.append(GameOption(action=kind.capitalize()))
        return options

    def _snapshot(self) -> GameSnapshot:
        opponent_seat = next((seat for seat in self.players if seat != self.user_seat), 0)
        return GameSnapshot(
            turn=self._turn(),
            phase=self._phase(),
            step=str(self.turn_info.get("step", "")).replace("Step_", ""),
            active_seat=int(self.turn_info.get("activePlayer", 0) or 0),
            player_life=self._life(self.user_seat),
            opponent_life=self._life(opponent_seat),
            hand=self._cards_in_zone("ZoneType_Hand", owner=self.user_seat),
            player_battlefield=self._cards_in_zone("ZoneType_Battlefield", controller=self.user_seat),
            opponent_battlefield=self._cards_in_zone("ZoneType_Battlefield", controller=opponent_seat),
        )

    def _turn(self) -> int:
        return int(self.turn_info.get("turnNumber", 0) or 0)

    def _phase(self) -> str:
        return str(self.turn_info.get("phase", "Unknown")).replace("Phase_", "")

    def _life(self, seat: int) -> Optional[int]:
        if not seat or seat not in self.players:
            return None
        value = self.players[seat].get("lifeTotal")
        return int(value) if value is not None else None

    def _cards_in_zone(self, zone_type: str, owner: int = 0, controller: int = 0) -> List[GameCard]:
        cards: List[GameCard] = []
        for zone in self.zones.values():
            if zone.get("type") != zone_type:
                continue
            if owner and int(zone.get("ownerSeatId", owner) or owner) != owner:
                continue
            for instance_id in zone.get("objectInstanceIds", []):
                game_object = self.objects.get(int(instance_id), {})
                if controller and int(game_object.get("controllerSeatId", 0) or 0) != controller:
                    continue
                card = self._card(int(instance_id))
                if card:
                    cards.append(card)
        return cards

    def _card_from_action(self, action: Dict) -> Optional[GameCard]:
        instance_id = int(action.get("instanceId", action.get("grpId", 0)) or 0)
        return self._card(instance_id, int(action.get("grpId", 0) or 0))

    def _card(self, instance_id: int, explicit_card_id: int = 0) -> Optional[GameCard]:
        game_object = self.objects.get(instance_id, {})
        card_id = int(game_object.get("grpId", explicit_card_id) or explicit_card_id or 0)
        if not card_id and not game_object:
            return None
        local = self.catalog.resolve(card_id)
        raw_types = game_object.get("cardTypes", [])
        types = local.get("types") or [str(value).replace("CardType_", "") for value in raw_types]
        all_decks = local.get("deck_colors", {}).get("All Decks", {})
        gihwr = all_decks.get("gihwr")
        power = game_object.get("power", {}).get("value")
        toughness = game_object.get("toughness", {}).get("value")
        return GameCard(
            instance_id=instance_id,
            card_id=card_id,
            name=str(local.get("name") or f"Card {card_id}"),
            mana_cost=str(local.get("mana_cost", "")),
            cmc=float(local.get("cmc", 0.0) or 0.0),
            colors=list(local.get("colors") or []),
            types=types,
            oracle_text=str(local.get("oracle_text", "") or ""),
            gihwr=float(gihwr) if isinstance(gihwr, (int, float)) and gihwr > 0 else None,
            power=int(power) if isinstance(power, (int, float)) else None,
            toughness=int(toughness) if isinstance(toughness, (int, float)) else None,
            tapped=bool(game_object.get("isTapped", False)),
        )

    def card_ids(self) -> set[int]:
        object_ids = {
            int(value.get("grpId", 0) or 0)
            for value in self.objects.values()
            if value.get("grpId")
        }
        return object_ids | set(self.deck_card_ids) | set(self.sideboard_card_ids)

    def enrich_cards(self):
        self.catalog.prime(self.event_id, self.card_ids())

        def enrich_choice(value: str) -> str:
            def replace(match):
                local = self.catalog.resolve(int(match.group(1)))
                return str(local.get("name") or match.group(0))

            return re.sub(r"Card (\d+)", replace, value)

        def enrich(card: Optional[GameCard]) -> Optional[GameCard]:
            if card is None or not card.card_id:
                return card
            local = self.catalog.resolve(card.card_id)
            if not local:
                return card
            all_decks = local.get("deck_colors", {}).get("All Decks", {})
            gihwr = all_decks.get("gihwr")
            return card.model_copy(
                update={
                    "name": str(local.get("name") or card.name),
                    "mana_cost": str(local.get("mana_cost", card.mana_cost)),
                    "cmc": float(local.get("cmc", card.cmc) or 0.0),
                    "colors": list(local.get("colors") or card.colors),
                    "types": local.get("types") or card.types,
                    "oracle_text": str(local.get("oracle_text", card.oracle_text) or ""),
                    "gihwr": (
                        float(gihwr)
                        if isinstance(gihwr, (int, float)) and gihwr > 0
                        else card.gihwr
                    ),
                }
            )

        for decision in self.decisions:
            decision.choice = enrich_choice(decision.choice)
            decision.options = [
                option.model_copy(update={"card": enrich(option.card)})
                for option in decision.options
            ]
            snapshot = decision.snapshot
            for field in ("hand", "player_battlefield", "opponent_battlefield"):
                setattr(snapshot, field, [enrich(card) for card in getattr(snapshot, field)])
        self.actions = [
            action.model_copy(update={"card": enrich(action.card)})
            for action in self.actions
        ]
        self.deck_cards = [
            card
            for card_id in self.deck_card_ids
            if (card := self._card(0, card_id)) is not None
        ]
        self.sideboard_cards = [
            card
            for card_id in self.sideboard_card_ids
            if (card := self._card(0, card_id)) is not None
        ]

    def to_game(self) -> ParsedGame:
        self.enrich_cards()
        opponent_seat = next((seat for seat in self.players if seat != self.user_seat), 0)
        decisions = len(self.decisions)
        coverage = (
            "full"
            if self.game_state_messages >= 20 and decisions >= 3
            else ("partial" if self.game_state_messages >= 5 else "summary")
        )
        limited = self.super_format == "SuperFormat_Limited" or any(
            token in self.event_id.lower() for token in ("draft", "sealed")
        )
        deck_fingerprint = ""
        if self.deck_card_ids:
            normalized_deck = ",".join(
                str(card_id) for card_id in sorted(self.deck_card_ids)
            )
            deck_fingerprint = hashlib.sha256(
                normalized_deck.encode("utf-8")
            ).hexdigest()[:20]
        return ParsedGame(
            match_id=self.match_id,
            played_at=self.played_at,
            event_id=self.event_id,
            game_number=self.game_number,
            user_seat=self.user_seat,
            user_team=self.user_team,
            completed=self.completed,
            limited=limited,
            result=self.result,
            result_reason=self.result_reason,
            turns=self._turn(),
            coverage=coverage,
            game_state_messages=self.game_state_messages,
            decisions=self.decisions,
            actions=self.actions,
            deck_cards=self.deck_cards,
            sideboard_cards=self.sideboard_cards,
            deck_fingerprint=deck_fingerprint,
            final_player_life=self._life(self.user_seat),
            final_opponent_life=self._life(opponent_seat),
        )


class ArenaGameLogParser:
    """Extract Limited games without retaining account or opponent identity."""

    def __init__(self, card_catalog: Optional[LocalCardCatalog] = None):
        self.catalog = card_catalog or LocalCardCatalog(constants.SETS_FOLDER)

    def parse(self, path: str) -> List[ParsedGame]:
        if not path or not os.path.isfile(path):
            return []

        builders: List[_GameBuilder] = []
        current: Optional[_GameBuilder] = None
        metadata_by_match: Dict[str, Dict] = {}

        for timestamp, record in iter_log_json(path):
            room_event = record.get("matchGameRoomStateChangedEvent", {})
            if room_event:
                metadata = self._room_metadata(room_event)
                match_id = metadata.pop("match_id", "")
                if match_id:
                    metadata_by_match[match_id] = metadata
                    if current and current.match_id == match_id:
                        current.apply_metadata(metadata)

            gre_event = record.get("greToClientEvent", {})
            if gre_event:
                messages = gre_event.get("greToClientMessages", [])
                if any(message.get("type") == "GREMessageType_ConnectResp" for message in messages):
                    if current and current.match_id:
                        builders.append(current)
                    current = _GameBuilder(self.catalog, timestamp)
                if current is None:
                    current = _GameBuilder(self.catalog, timestamp)
                for message in messages:
                    current.process_gre_message(message)
                    if current.match_id in metadata_by_match:
                        current.apply_metadata(metadata_by_match[current.match_id])

            payload = record.get("payload")
            if current and isinstance(payload, dict) and str(payload.get("type", "")).startswith("ClientMessageType_"):
                current.process_client_response(payload)

        if current and current.match_id:
            builders.append(current)

        games: Dict[tuple[str, int], ParsedGame] = {}
        for builder in builders:
            if builder.match_id in metadata_by_match:
                builder.apply_metadata(metadata_by_match[builder.match_id])
            game = builder.to_game()
            if game.match_id and game.limited:
                games[(game.match_id, game.game_number)] = game
        return sorted(games.values(), key=lambda value: value.played_at, reverse=True)

    @staticmethod
    def _room_metadata(room_event: Dict) -> Dict:
        info = room_event.get("gameRoomInfo", {})
        config = info.get("gameRoomConfig", {})
        players = config.get("reservedPlayers", [])
        local_player = next(
            (player for player in players if str(player.get("platformId", "")).lower() in {"steammac", "mac", "windows", "steam"}),
            players[0] if players else {},
        )
        return {
            "match_id": str(config.get("matchId", "")),
            "event_id": str(local_player.get("eventId", "Limited")),
            "user_seat": int(local_player.get("systemSeatId", 1) or 1),
            "user_team": int(local_player.get("teamId", 1) or 1),
        }
