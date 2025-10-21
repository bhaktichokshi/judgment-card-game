import json
import logging
import os
import random
import string
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "app" / "static"
DATA_DIR = BASE_DIR / "data"
SCOREBOARD_FILE = DATA_DIR / "scoreboard.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("judgment")

SUIT_SEQUENCE = ["S", "D", "C", "H"]
SUIT_SYMBOLS = {"S": "♠", "D": "♦", "C": "♣", "H": "♥"}
SUIT_NAMES = {"S": "Spades", "D": "Diamonds", "C": "Clubs", "H": "Hearts"}
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_ORDER = {rank: index for index, rank in enumerate(RANKS)}
DEFAULT_BASE_HAND = 8
BASE_HAND_OPTIONS = {4: 12, 8: 6, 16: 3}


def build_deck() -> List[str]:
    """Return a fresh 52-card deck represented as strings like 'AS'."""
    return [f"{rank}{suit}" for suit in SUIT_SEQUENCE for rank in RANKS]


def card_sort_key(card: str) -> Tuple[int, int]:
    rank, suit = card[:-1], card[-1]
    return (suit_index(suit), RANK_ORDER[rank])


def suit_index(suit: str) -> int:
    return SUIT_SEQUENCE.index(suit)


def card_to_display(card: str) -> str:
    rank, suit = card[:-1], card[-1]
    return f"{rank}{SUIT_SYMBOLS[suit]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PlayerState:
    player_id: str
    name: str
    total_score: int = 0
    correct_bids: int = 0


@dataclass
class RoundState:
    cards_per_player: int
    dealer_index: int
    starter_index: int
    trump_index: int
    bids: Dict[str, Optional[int]] = field(default_factory=dict)
    tricks_won: Dict[str, int] = field(default_factory=dict)
    hands: Dict[str, List[str]] = field(default_factory=dict)
    current_trick: List[Tuple[str, str]] = field(default_factory=list)
    trick_history: List[Dict[str, str]] = field(default_factory=list)
    status: str = "bidding"
    blind_bidding: bool = False
    current_turn_index: int = 0


@dataclass
class GameState:
    players_order: List[str]
    dealer_index: int
    trump_index: int
    round_sequence: List[int]
    current_round: int = 0
    round_state: Optional[RoundState] = None
    started_at: datetime = field(default_factory=utc_now)
    finished: bool = False
    round_log: List[dict] = field(default_factory=list)


@dataclass
class Room:
    code: str
    created_at: datetime
    host_id: str
    players: List[PlayerState] = field(default_factory=list)
    status: str = "waiting"
    game: Optional[GameState] = None
    last_result: Optional[dict] = None
    base_cards: int = DEFAULT_BASE_HAND


class ScoreboardStorage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def append_entry(self, entry: dict) -> None:
        with self.lock:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            data.append(entry)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_entries(self) -> List[dict]:
        with self.lock:
            return json.loads(self.path.read_text(encoding="utf-8"))


class GameManager:
    def __init__(self, scoreboard: ScoreboardStorage) -> None:
        self.rooms: Dict[str, Room] = {}
        self.lock = threading.Lock()
        self.scoreboard = scoreboard

    def create_room(self, host_name: str, base_cards: int) -> dict:
        host_name = host_name.strip()
        if not host_name:
            raise ValueError("Host name is required")
        base_cards = self._validate_base(base_cards)

        with self.lock:
            code = self._generate_room_code()
            player_id = self._generate_player_id()
            room = Room(
                code=code,
                created_at=utc_now(),
                host_id=player_id,
                base_cards=base_cards,
            )
            host = PlayerState(player_id=player_id, name=host_name)
            room.players.append(host)
            self.rooms[code] = room
            logger.info(
                "Room %s created by host %s (%s) with base hand %d",
                code,
                host_name,
                player_id,
                base_cards,
            )
            return {
                "room_code": code,
                "player_id": player_id,
                "player_name": host_name,
                "base_cards": base_cards,
                "max_players": self._max_players_for_base(base_cards),
            }

    def join_room(self, room_code: str, player_name: str) -> dict:
        player_name = player_name.strip()
        if not player_name:
            raise ValueError("Player name is required")

        with self.lock:
            room = self.rooms.get(room_code.upper())
            if not room:
                raise ValueError("Room not found")
            if room.status != "waiting":
                raise ValueError("Game already started")

            new_player_id = self._generate_player_id()
            tentative_count = len(room.players) + 1
            max_players = self._max_players_for_base(room.base_cards)
            if tentative_count > max_players or tentative_count * room.base_cards >= 52:
                raise ValueError(
                    f"Player limit reached for base {room.base_cards}: maximum {max_players} players"
                )

            room.players.append(PlayerState(player_id=new_player_id, name=player_name))
            logger.info(
                "Player %s (%s) joined room %s; total players=%d",
                player_name,
                new_player_id,
                room.code,
                len(room.players),
            )
            return {
                "room_code": room.code,
                "player_id": new_player_id,
                "player_name": player_name,
                "base_cards": room.base_cards,
                "max_players": max_players,
            }

    def start_game(self, room_code: str, player_id: str) -> dict:
        with self.lock:
            room = self._get_room_or_raise(room_code)
            if room.host_id != player_id:
                raise ValueError("Only the host can start the game")
            if len(room.players) < 2:
                raise ValueError("At least two players are required")
            if room.status != "waiting":
                raise ValueError("Game already started")

            base_cards = room.base_cards
            self._validate_base(base_cards)
            max_players = self._max_players_for_base(base_cards)
            if len(room.players) > max_players or len(room.players) * base_cards >= 52:
                raise ValueError(
                    f"Too many players for base {base_cards}. Maximum allowed: {max_players}"
                )

            round_sequence = list(range(base_cards, 0, -1)) + list(
                range(2, base_cards + 1)
            )
            order = [p.player_id for p in room.players]
            logger.info(
                "Starting game in room %s with %d players; starting hand=%d; sequence=%s",
                room.code,
                len(order),
                base_cards,
                round_sequence,
            )
            game = GameState(
                players_order=order,
                dealer_index=0,
                trump_index=0,
                round_sequence=round_sequence,
                round_log=[],
            )
            room.game = game
            room.status = "playing"
            game.round_state = self._create_round_state(room)
            return {"status": "ok"}

    def submit_bid(self, room_code: str, player_id: str, bid_value: int) -> dict:
        with self.lock:
            room = self._get_room_or_raise(room_code)
            game = self._require_game_in_progress(room)
            round_state = self._require_round_state(game)
            if round_state.status != "bidding":
                raise ValueError("Bidding has finished for this round")

            logger.info(
                "Room %s round %d: player %s attempting bid %s (current_turn=%s)",
                room.code,
                game.current_round + 1,
                self._player_log(room, player_id),
                bid_value,
                self._player_log(
                    room, game.players_order[round_state.current_turn_index]
                ),
            )

            self._ensure_player_turn(game, round_state, player_id)
            if bid_value < 0 or bid_value > round_state.cards_per_player:
                raise ValueError("Bid outside allowed range")

            dealer_id = game.players_order[round_state.dealer_index]
            projected_total = sum(b for b in round_state.bids.values() if b is not None)
            if player_id == dealer_id:
                if projected_total + bid_value == round_state.cards_per_player:
                    raise ValueError("Dealer bid cannot make totals equal cards dealt")

            round_state.bids[player_id] = bid_value
            if all(b is not None for b in round_state.bids.values()):
                round_state.status = "playing"
                round_state.current_turn_index = round_state.starter_index
            else:
                round_state.current_turn_index = self._next_bidder_index(
                    game, round_state
                )

            logger.info(
                "Room %s round %d bids now %s; next turn=%s",
                room.code,
                game.current_round + 1,
                self._bids_debug(room, round_state),
                self._player_log(
                    room, game.players_order[round_state.current_turn_index]
                ),
            )

            return {"status": "ok"}

    def play_card(self, room_code: str, player_id: str, card: str) -> dict:
        card = card.strip().upper()
        with self.lock:
            room = self._get_room_or_raise(room_code)
            game = self._require_game_in_progress(room)
            round_state = self._require_round_state(game)
            if round_state.status != "playing":
                raise ValueError("Cannot play cards during bidding")

            logger.info(
                "Room %s round %d: player %s playing %s (current_turn=%s)",
                room.code,
                game.current_round + 1,
                self._player_log(room, player_id),
                card,
                self._player_log(
                    room, game.players_order[round_state.current_turn_index]
                ),
            )
            self._ensure_player_turn(game, round_state, player_id)
            hand = round_state.hands[player_id]
            if card not in hand:
                raise ValueError("Card not in hand")

            if not self._card_play_allowed(round_state, hand, card):
                raise ValueError("You must follow suit when possible")

            hand.remove(card)
            round_state.current_trick.append((player_id, card))

            if len(round_state.current_trick) == len(game.players_order):
                self._close_trick(room, game, round_state)
            else:
                round_state.current_turn_index = (
                    round_state.current_turn_index + 1
                ) % len(game.players_order)

            return {"status": "ok"}

    def get_state(self, room_code: str, player_id: Optional[str]) -> dict:
        with self.lock:
            room = self.rooms.get(room_code.upper())
            if not room:
                raise ValueError("Room not found")

            players_payload = []
            for idx, player in enumerate(room.players):
                players_payload.append(
                    {
                        "player_id": player.player_id,
                        "name": player.name,
                        "total_score": player.total_score,
                        "correct_bids": player.correct_bids,
                        "is_host": player.player_id == room.host_id,
                        "is_you": player_id == player.player_id,
                        "seat": idx,
                    }
                )

            try:
                max_players = self._max_players_for_base(room.base_cards)
            except ValueError:
                max_players = None

            response = {
                "room": {
                    "code": room.code,
                    "status": room.status,
                    "players": players_payload,
                    "host_id": room.host_id,
                    "created_at": room.created_at.replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "base_cards": room.base_cards,
                    "max_players": max_players,
                },
                "scoreboard": self.scoreboard.load_entries(),
            }

            if not room.game:
                return response

            game = room.game
            round_state = game.round_state
            trump_suit = SUIT_SEQUENCE[game.trump_index]

            game_payload = {
                "started": True,
                "finished": game.finished,
                "current_round": game.current_round + 1,
                "total_rounds": len(game.round_sequence),
                "cards_per_player": round_state.cards_per_player if round_state else None,
                "dealer_id": game.players_order[round_state.dealer_index]
                if round_state
                else None,
                "starter_id": game.players_order[round_state.starter_index]
                if round_state
                else None,
                "phase": round_state.status if round_state else "waiting",
                "trump": {
                    "code": trump_suit,
                    "name": SUIT_NAMES[trump_suit],
                    "symbol": SUIT_SYMBOLS[trump_suit],
                },
                "bids": round_state.bids if round_state else {},
                "tricks_won": round_state.tricks_won if round_state else {},
                "current_trick": [
                    {
                        "player_id": play[0],
                        "player_name": self._player_name(room, play[0]),
                        "card": play[1],
                        "display": card_to_display(play[1]),
                    }
                    for play in (round_state.current_trick if round_state else [])
                ],
                "blind_bidding": bool(round_state.blind_bidding) if round_state else False,
            }

            if round_state and player_id in round_state.hands:
                hand_cards = (
                    sorted(round_state.hands[player_id], key=card_sort_key)
                    if not round_state.blind_bidding or round_state.status != "bidding"
                    else ["??"]
                )
                game_payload["hand"] = [
                    {"card": card, "display": card_to_display(card)}
                    for card in hand_cards
                    if card != "??"
                ]
                if hand_cards == ["??"]:
                    game_payload["hand"] = [{"card": "??", "display": "??"}]

            if round_state:
                turn_player = game.players_order[round_state.current_turn_index]
                game_payload["current_turn"] = {
                    "player_id": turn_player,
                    "player_name": self._player_name(room, turn_player),
                }
                if (
                    player_id
                    and round_state.status == "bidding"
                    and player_id == turn_player
                ):
                    game_payload["allowed_bids"] = self._allowed_bids(
                        game, round_state, player_id
                    )
                if (
                    player_id
                    and round_state.status == "playing"
                    and player_id == turn_player
                ):
                    game_payload["allowed_cards"] = self._allowed_cards(
                        round_state, player_id
                    )

            if (
                round_state
                and player_id
                and player_id in round_state.hands
                and not round_state.hands[player_id]
                and round_state.status == "playing"
            ):
                game_payload["hand"] = []

        current_index: Optional[int] = None
        if round_state and not game.finished:
            current_index = game.current_round

        rounds_summary: List[dict] = []
        for idx, cards in enumerate(game.round_sequence):
            entry = {
                "round": idx + 1,
                "cards": cards,
                "status": "pending",
            }
            if idx < len(game.round_log):
                log = game.round_log[idx]
                entry.update(
                    {
                        "status": "complete",
                        "bids": dict(log.get("bids", {})),
                        "tricks_won": dict(log.get("tricks_won", {})),
                        "points": dict(log.get("points", {})),
                        "results": dict(log.get("results", {})),
                    }
                )
            elif current_index is not None and idx == current_index and round_state:
                entry.update(
                    {
                        "status": round_state.status,
                        "bids": dict(round_state.bids),
                        "tricks_won": dict(round_state.tricks_won),
                        "points": {},
                    }
                )
                entry["is_current"] = True
            rounds_summary.append(entry)

        game_payload["rounds"] = rounds_summary

        response["game"] = game_payload
        response["last_result"] = room.last_result
        return response

    def get_scoreboard(self) -> List[dict]:
        return self.scoreboard.load_entries()

    def _create_round_state(self, room: Room) -> RoundState:
        game = room.game
        assert game is not None

        cards_per_player = game.round_sequence[game.current_round]
        deck = build_deck()
        random.shuffle(deck)

        hands: Dict[str, List[str]] = {}
        for player_id in game.players_order:
            hand_cards = [deck.pop() for _ in range(cards_per_player)]
            hand_cards.sort(key=card_sort_key)
            hands[player_id] = hand_cards

        starter_index = (game.dealer_index + 1) % len(game.players_order)
        bids = {player_id: None for player_id in game.players_order}
        tricks_won = {player_id: 0 for player_id in game.players_order}
        blind = cards_per_player == 1

        return RoundState(
            cards_per_player=cards_per_player,
            dealer_index=game.dealer_index,
            starter_index=starter_index,
            trump_index=game.trump_index,
            bids=bids,
            tricks_won=tricks_won,
            hands=hands,
            blind_bidding=blind,
            current_turn_index=starter_index,
        )

    def _next_bidder_index(self, game: GameState, round_state: RoundState) -> int:
        order_len = len(game.players_order)
        next_index = (round_state.current_turn_index + 1) % order_len
        while round_state.bids[game.players_order[next_index]] is not None:
            next_index = (next_index + 1) % order_len
        return next_index

    def _allowed_bids(self, game: GameState, round_state: RoundState, player_id: str) -> List[int]:
        dealer_id = game.players_order[round_state.dealer_index]
        allowed = list(range(0, round_state.cards_per_player + 1))
        if player_id == dealer_id:
            current_total = sum(
                bid for pid, bid in round_state.bids.items() if bid is not None and pid != player_id
            )
            forbidden = round_state.cards_per_player - current_total
            if forbidden in allowed:
                allowed.remove(forbidden)
        return allowed

    def _allowed_cards(self, round_state: RoundState, player_id: str) -> List[str]:
        hand = round_state.hands[player_id]
        if not round_state.current_trick:
            return list(hand)
        lead_suit = round_state.current_trick[0][1][-1]
        matching = [card for card in hand if card.endswith(lead_suit)]
        return matching if matching else list(hand)

    def _card_play_allowed(self, round_state: RoundState, hand: List[str], card: str) -> bool:
        if not round_state.current_trick:
            return True
        lead_suit = round_state.current_trick[0][1][-1]
        if card.endswith(lead_suit):
            return True
        return not any(c.endswith(lead_suit) for c in hand)

    def _close_trick(self, room: Room, game: GameState, round_state: RoundState) -> None:
        trump_suit = SUIT_SEQUENCE[round_state.trump_index]
        winning_play = round_state.current_trick[0]
        lead_suit = winning_play[1][-1]
        for play in round_state.current_trick[1:]:
            _, card = play
            if card.endswith(trump_suit):
                if not winning_play[1].endswith(trump_suit) or self._card_higher(card, winning_play[1]):
                    winning_play = play
            elif winning_play[1].endswith(trump_suit):
                continue
            elif card.endswith(lead_suit) and self._card_higher(card, winning_play[1]):
                winning_play = play

        winner_id = winning_play[0]
        round_state.tricks_won[winner_id] += 1
        round_state.trick_history.append(
            {
                "winner_id": winner_id,
                "cards": [
                    {
                        "player_id": pid,
                        "player_name": self._player_name(room, pid),
                        "card": card,
                        "display": card_to_display(card),
                    }
                    for pid, card in round_state.current_trick
                ],
            }
        )
        round_state.current_trick.clear()

        if all(len(hand) == 0 for hand in round_state.hands.values()):
            logger.info(
                "Room %s round %d: trick complete, winner=%s; bids=%s; tricks=%s",
                room.code,
                game.current_round + 1,
                self._player_log(room, winner_id),
                self._bids_debug(room, round_state),
                {
                    self._player_log(room, pid): won
                    for pid, won in round_state.tricks_won.items()
                },
            )
            self._complete_round(room, game, round_state)
        else:
            round_state.current_turn_index = game.players_order.index(winner_id)

    def _card_higher(self, card_a: str, card_b: str) -> bool:
        rank_a, rank_b = card_a[:-1], card_b[:-1]
        return RANK_ORDER[rank_a] > RANK_ORDER[rank_b]

    def _complete_round(self, room: Room, game: GameState, round_state: RoundState) -> None:
        round_state.status = "complete"
        round_record = {
            "index": game.current_round,
            "cards": round_state.cards_per_player,
            "bids": {pid: bid for pid, bid in round_state.bids.items()},
            "tricks_won": {pid: count for pid, count in round_state.tricks_won.items()},
            "points": {},
            "results": {},
            "status": "complete",
        }
        for player in room.players:
            tricks = round_state.tricks_won[player.player_id]
            bid = round_state.bids[player.player_id]
            hit = bid is not None and tricks == bid
            gained = 10 + 11 * bid if hit and bid is not None else 0
            round_record["points"][player.player_id] = gained
            round_record["results"][player.player_id] = "hit" if hit else "miss"
            if hit:
                player.correct_bids += 1
                player.total_score += gained

        if len(game.round_log) > game.current_round:
            game.round_log[game.current_round] = round_record
        else:
            game.round_log.append(round_record)

        game.dealer_index = round_state.starter_index
        game.trump_index = (game.trump_index + 1) % len(SUIT_SEQUENCE)
        game.current_round += 1

        if game.current_round >= len(game.round_sequence):
            game.finished = True
            room.status = "finished"
            result = self._tally_results(room)
            room.last_result = result
            self.scoreboard.append_entry(result)
            game.round_state = None
        else:
            logger.info(
                "Room %s round completed; scores=%s; advancing to round %d",
                room.code,
                {
                    self._player_log(room, player.player_id): player.total_score
                    for player in room.players
                },
                game.current_round + 1,
            )
            game.round_state = self._create_round_state(room)

    def _tally_results(self, room: Room) -> dict:
        timestamp = utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
        scores = [(player.total_score, player) for player in room.players]
        guesses = [(player.correct_bids, player) for player in room.players]
        max_score = max(score for score, _ in scores)
        max_guess = max(guess for guess, _ in guesses)

        score_winners = [player.player_id for score, player in scores if score == max_score]
        guess_winners = [player.player_id for guess, player in guesses if guess == max_guess]
        mega = [pid for pid in score_winners if pid in guess_winners]

        return {
            "room_code": room.code,
            "completed_at": timestamp,
            "base_cards": room.base_cards,
            "players": [
                {
                    "player_id": player.player_id,
                    "name": player.name,
                    "total_score": player.total_score,
                    "correct_bids": player.correct_bids,
                }
                for player in room.players
            ],
            "score_winners": score_winners,
            "guess_winners": guess_winners,
            "mega_winners": mega,
        }

    def _ensure_player_turn(self, game: GameState, round_state: RoundState, player_id: str) -> None:
        expected_player = game.players_order[round_state.current_turn_index]
        if expected_player != player_id:
            raise ValueError("It is not your turn")

    def _player_name(self, room: Room, player_id: str) -> str:
        for player in room.players:
            if player.player_id == player_id:
                return player.name
        return "Unknown"

    def _require_game_in_progress(self, room: Room) -> GameState:
        if not room.game:
            raise ValueError("Game not started")
        return room.game

    def _require_round_state(self, game: GameState) -> RoundState:
        if not game.round_state:
            raise ValueError("Round state unavailable")
        return game.round_state

    def _get_room_or_raise(self, room_code: str) -> Room:
        room = self.rooms.get(room_code.upper())
        if not room:
            raise ValueError("Room not found")
        return room

    def _player_log(self, room: Room, player_id: str) -> str:
        for player in room.players:
            if player.player_id == player_id:
                return f"{player.name}({player_id[:6]})"
        return f"unknown({player_id[:6]})"

    def _bids_debug(self, room: Room, round_state: RoundState) -> Dict[str, Optional[int]]:
        summary = {}
        for player_id, bid in round_state.bids.items():
            summary[self._player_log(room, player_id)] = bid
        return summary

    def _validate_base(self, base_cards: int) -> int:
        if base_cards not in BASE_HAND_OPTIONS:
            raise ValueError("Invalid base hand size")
        return base_cards

    def _max_players_for_base(self, base_cards: int) -> int:
        base_cards = self._validate_base(base_cards)
        return BASE_HAND_OPTIONS[base_cards]

    def _generate_room_code(self) -> str:
        while True:
            code = "".join(random.choices(string.ascii_uppercase, k=4))
            if code not in self.rooms:
                return code

    @staticmethod
    def _generate_player_id() -> str:
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=16))


class JudgmentRequestHandler(BaseHTTPRequestHandler):
    manager = GameManager(ScoreboardStorage(SCOREBOARD_FILE))

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            rel = parsed.path[len("/static/") :]
            self._serve_static(rel)
            return
        if parsed.path == "/api/state":
            params = parse_qs(parsed.query or "")
            room = params.get("room", [None])[0]
            player_id = params.get("player_id", [None])[0]
            if not room:
                self._send_json({"error": "room parameter required"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                data = self.manager.get_state(room, player_id)
                self._send_json(data)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/scoreboard":
            data = self.manager.get_scoreboard()
            self._send_json({"entries": data})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Resource not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return

        handlers = {
            "/api/create_room": self._handle_create_room,
            "/api/join_room": self._handle_join_room,
            "/api/start_game": self._handle_start_game,
            "/api/submit_bid": self._handle_submit_bid,
            "/api/play_card": self._handle_play_card,
        }

        handler = handlers.get(parsed.path)
        if not handler:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
            return

        try:
            response = handler(payload)
            self._send_json(response)
        except ValueError as exc:
            logger.warning("Request %s failed: %s", parsed.path, exc)
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _handle_create_room(self, payload: dict) -> dict:
        host_name = payload.get("host_name", "")
        base_value = payload.get("base_cards", DEFAULT_BASE_HAND)
        try:
            base_cards = int(base_value)
        except (TypeError, ValueError):
            raise ValueError("Invalid base hand size")
        return self.manager.create_room(host_name, base_cards)

    def _handle_join_room(self, payload: dict) -> dict:
        room_code = payload.get("room_code", "")
        player_name = payload.get("player_name", "")
        return self.manager.join_room(room_code, player_name)

    def _handle_start_game(self, payload: dict) -> dict:
        room_code = payload.get("room_code", "")
        player_id = payload.get("player_id", "")
        return self.manager.start_game(room_code, player_id)

    def _handle_submit_bid(self, payload: dict) -> dict:
        room_code = payload.get("room_code", "")
        player_id = payload.get("player_id", "")
        bid_value = payload.get("bid")
        if not isinstance(bid_value, int):
            raise ValueError("Bid must be an integer")
        return self.manager.submit_bid(room_code, player_id, bid_value)

    def _handle_play_card(self, payload: dict) -> dict:
        room_code = payload.get("room_code", "")
        player_id = payload.get("player_id", "")
        card = payload.get("card", "")
        return self.manager.play_card(room_code, player_id, card)

    def _serve_static(self, relative_path: str) -> None:
        path = (STATIC_DIR / relative_path).resolve()
        if not str(path).startswith(str(STATIC_DIR.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid path")
            return
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._set_common_headers()
        if path.suffix == ".html":
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif path.suffix == ".css":
            self.send_header("Content-Type", "text/css; charset=utf-8")
        elif path.suffix == ".js":
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
        else:
            self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        response = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._set_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _set_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")

    def log_message(self, fmt: str, *args) -> None:
        # Reduce console noise by only logging warnings/errors
        pass


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), JudgmentRequestHandler)
    print(f"Judgment server running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server")
    finally:
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    run_server(host="0.0.0.0", port=port)
