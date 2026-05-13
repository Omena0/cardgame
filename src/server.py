from __future__ import annotations

import argparse
import json
import queue
import random
import re
import socket
import string
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from src.net import WebSocketConnection, accept_websocket
    from src.shared import (
        build_deck,
        can_play_on,
        card_face,
        card_rank,
        choose_start_player,
        count_ranks,
        format_cards,
        parse_cards,
        PLAY_RANKS,
        sort_hand,
    )
    from src.logging import Logger, LogLevel
except ModuleNotFoundError:
    from net import WebSocketConnection, accept_websocket
    from shared import (
        build_deck,
        can_play_on,
        card_face,
        card_rank,
        choose_start_player,
        count_ranks,
        format_cards,
        parse_cards,
        PLAY_RANKS,
        sort_hand,
    )
    from logging import Logger, LogLevel

NAME_RE = re.compile(r"^[A-Za-z0-9_ ]{4,20}$")
ROOM_CODE_RE = re.compile(r"^[A-Z0-9]{4,8}$")
BOT_PATH = Path(__file__).with_name("bot.py")
if not BOT_PATH.exists():
    BOT_PATH = Path(__file__).resolve().parent.parent / "docs" / "bot.py"

HIDDEN_MISPLAY_DRAW_DELAY = 0.3
MAX_ROOM_PLAYERS = 4

log = Logger("server")
log.level = LogLevel.TRACE if "-d" in sys.argv else LogLevel.INFO


def _cards_of_rank(tokens: list[str], rank: str) -> list[str]:
    return [token for token in tokens if card_rank(token) == rank]


def _best_rank_for_play(ranks: list[str], top_face: str | None) -> str | None:
    candidates = [rank for rank in ranks if rank not in {"2", "T"}]
    for rank in candidates:
        if can_play_on(top_face, f"{rank}C"):
            return rank
    return None


def bot_choose_action(state: dict) -> str:
    phase = state.get("phase", "game")
    if phase == "pregame":
        return "READY"
    if phase == "reorder":
        # Basic reorder: keep strongest cards visible.
        hand = list(state.get("hand", []))
        visible = list(state.get("visible", []))
        combined = hand + visible
        combined.sort(key=lambda token: card_rank(token), reverse=True)
        new_visible = combined[:3]
        new_hand = sort_hand(combined[3:])
        return f"REORDER | {format_cards(new_hand)} | {format_cards(new_visible)}"

    hand = list(state.get("hand", []))
    visible = list(state.get("visible", []))
    hidden_count = int(state.get("hidden_count", 0))
    pile = list(state.get("pile", []))
    draw_pile_size = int(state.get("draw_pile_size", 0))
    top_face = card_face(pile[-1]) if pile else None

    if not hand and not visible and hidden_count > 0:
        return "PLAY_HIDDEN 0"

    available = hand if hand else visible
    available_ranks = count_ranks(available)
    normal_ranks = [rank for rank in PLAY_RANKS if rank not in {"2", "T"} and available_ranks.get(rank)]

    if hand and available_ranks:
        best_rank = _best_rank_for_play(normal_ranks, top_face)
        if best_rank is not None:
            chosen = _cards_of_rank(hand, best_rank)
            if chosen:
                if len(chosen) == len(hand) and visible and all(card_rank(token) == best_rank for token in visible):
                    chosen = chosen + _cards_of_rank(visible, best_rank)
                return f"PLAY {format_cards(chosen)}"

    if not hand and visible:
        best_rank = _best_rank_for_play(normal_ranks, top_face)
        if best_rank is not None:
            chosen = _cards_of_rank(visible, best_rank)
            if chosen:
                return f"PLAY {format_cards(chosen)}"

    if draw_pile_size > 1:
        if available_ranks.get("2"):
            chosen = _cards_of_rank(available, "2")
            return f"PLAY {format_cards(chosen)}"
        if available_ranks.get("T"):
            chosen = _cards_of_rank(available, "T")
            return f"PLAY {format_cards(chosen)}"

    if available_ranks.get("2"):
        chosen = _cards_of_rank(available, "2")
        return f"PLAY {format_cards(chosen)}"
    if available_ranks.get("T"):
        chosen = _cards_of_rank(available, "T")
        return f"PLAY {format_cards(chosen)}"

    return "DRAW"

def _message(command: str, *fields: str) -> str:
    if not fields:
        return command
    return " | ".join([command, *fields])

@dataclass
class PlayerSeat:
    name: str
    session: Optional["ClientSession"] = None
    bot: Optional["BotRunner"] = None
    ready: bool = False
    reorder_done: bool = False
    hand: list[str] = field(default_factory=list)
    visible: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    placement: Optional[int] = None
    connected: bool = True

    @property
    def active(self) -> bool:
        return self.placement is None

    @property
    def total_cards(self) -> int:
        return len(self.hand) + len(self.visible) + len(self.hidden)


class BotRunner:
    def __init__(self, room: "Room", seat_name: str) -> None:
        self.room = room
        self.seat_name = seat_name
        self.process = subprocess.Popen(
            [sys.executable, str(BOT_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.responses: queue.Queue[str] = queue.Queue()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

    def _reader_loop(self) -> None:
        assert self.process.stdout is not None
        for raw_line in self.process.stdout:
            line = raw_line.strip()
            if line:
                self.responses.put(line)

    def send_state(self, state: dict) -> None:
        if self.process.stdin is None:
            return
        payload = json.dumps(state, separators=(",", ":"))
        self.process.stdin.write(f"STATE {payload}\n")
        self.process.stdin.flush()

    def request_command(self, state: dict, timeout: float = 2.0) -> str | None:
        if self.process.poll() is not None:
            return None
        while not self.responses.empty():
            try:
                self.responses.get_nowait()
            except queue.Empty:
                break
        self.send_state(state)
        if self.process.stdin is None:
            return None
        self.process.stdin.write("GO\n")
        self.process.stdin.flush()
        try:
            return self.responses.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                if self.process.stdin:
                    self.process.stdin.write("QUIT\n")
                    self.process.stdin.flush()
            except OSError:
                pass
            try:
                self.process.terminate()
            except OSError:
                pass

class ClientSession:
    def __init__(self, server: "GameServer", conn: WebSocketConnection, address: tuple[str, int]) -> None:
        self.server = server
        self.conn = conn
        self.address = address
        self.seat: Optional[PlayerSeat] = None
        self.room_code: str | None = None
        self.alive = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def send(self, line: str) -> None:
        if self.alive:
            self.conn.send_text(line)

    def close(self) -> None:
        self.alive = False
        self.conn.close()

    def _loop(self) -> None:
        try:
            while True:
                line = self.conn.recv_text()
                if line is None:
                    break
                log.trace(f"Recv: {line}")
                self.server.submit_command(self, line, source="client")
        except Exception:
            pass
        finally:
            self.alive = False
            self.server.handle_disconnect(self)

class Room:
    def __init__(self, server: "GameServer", code: str, fill_with_bots: bool = True) -> None:
        self.server = server
        self.code = code
        self.fill_with_bots = fill_with_bots

        self.phase = "lobby"
        self.seats: list[PlayerSeat] = []

        self.turn_index = 0
        self.turn_bonus_available = False
        self.turn_deadline = 0.0
        self.pile: list[str] = []
        self.draw_pile: list[str] = []
        self.initial_start_name: str | None = None
        self.play_lock_until = 0.0
        self.reorder_start_time = 0.0

    def public_player_list(self) -> str:
        return ",".join(seat.name for seat in self.seats)

    def broadcast(self, line: str) -> None:
        for seat in self.seats:
            if seat.session and seat.session.alive:
                seat.session.send(line)

    def send_private(self, seat_name: str, line: str) -> None:
        seat = self.seat_by_name(seat_name)
        if seat and seat.session and seat.session.alive:
            seat.session.send(line)

    def send_room_context(self, session: ClientSession) -> None:
        session.send(_message("ROOM", self.code))
        session.send(_message("PLAYERS", self.public_player_list()))
        session.send(_message("LOBBY_FILL_BOTS", "1" if self.fill_with_bots else "0"))
        for seat in self.seats:
            session.send(_message("READY_STATE", seat.name, "1" if seat.ready else "0"))

    def seat_by_name(self, seat_name: str) -> Optional[PlayerSeat]:
        for seat in self.seats:
            if seat.name == seat_name:
                return seat
        return None

    def current_turn_seat(self) -> Optional[PlayerSeat]:
        active = [seat for seat in self.seats if seat.active]
        if not active:
            return None
        self.turn_index %= len(self.seats)
        for offset in range(len(self.seats)):
            seat = self.seats[(self.turn_index + offset) % len(self.seats)]
            if seat.active:
                self.turn_index = (self.turn_index + offset) % len(self.seats)
                return seat
        return None

    def next_active_index(self, start_index: int) -> int:
        if not self.seats:
            return 0
        for offset in range(1, len(self.seats) + 1):
            candidate = (start_index + offset) % len(self.seats)
            if self.seats[candidate].active:
                return candidate
        return start_index

    def current_top_face(self) -> str | None:
        return card_face(self.pile[-1]) if self.pile else None

    def seat_index(self, seat_name: str | None) -> int:
        if seat_name is None:
            return 0
        for index, seat in enumerate(self.seats):
            if seat.name == seat_name:
                return index
        return 0

    def _broadcast_ready_state(self, seat: PlayerSeat) -> None:
        self.broadcast(_message("READY_STATE", seat.name, "1" if seat.ready else "0"))

    def state_for_bot(self, seat: PlayerSeat) -> dict:
        current = self.current_turn_seat()
        return {
            "phase": "pregame" if self.phase == "lobby" else self.phase,
            "self_name": seat.name,
            "hand": list(seat.hand),
            "visible": list(seat.visible),
            "hidden_count": len(seat.hidden),
            "pile": list(self.pile),
            "draw_pile_size": len(self.draw_pile),
            "turn_player": current.name if current else None,
            "bonus_available": self.turn_bonus_available,
            "players": [other.name for other in self.seats],
            "hand_counts": {other.name: len(other.hand) for other in self.seats},
        }

    def send_public_state(self) -> None:
        self.broadcast(_message("PLAYERS", self.public_player_list()))
        for seat in self.seats:
            self.broadcast(_message("VISIBLE", seat.name, format_cards(seat.visible)))
            self.broadcast(_message("HAND_COUNT", seat.name, str(len(seat.hand))))
            self.broadcast(_message("HIDDEN_COUNT", seat.name, str(len(seat.hidden))))
            self.send_private(seat.name, _message("HAND", format_cards(seat.hand)))

    def join_player(self, session: ClientSession, name: str) -> tuple[bool, str]:
        if self.phase != "lobby":
            return False, "room already started"
        if len(self.seats) >= MAX_ROOM_PLAYERS:
            return False, "room is full"
        if not NAME_RE.match(name):
            return False, "invalid username"
        if self.seat_by_name(name) is not None:
            return False, "name already taken"

        seat = PlayerSeat(name=name, session=session, connected=True)
        session.seat = seat
        session.room_code = self.code
        self.seats.append(seat)

        self.broadcast(_message("PLAYERS", self.public_player_list()))
        self._broadcast_ready_state(seat)
        self.broadcast(_message("LOBBY_FILL_BOTS", "1" if self.fill_with_bots else "0"))
        log.info(f"Player joined room {self.code}: {name}")
        return True, ""

    def _spawn_bots_to_four_locked(self) -> None:
        existing = {seat.name for seat in self.seats}
        index = 1
        while len(self.seats) < MAX_ROOM_PLAYERS:
            name = f"Bot{index}"
            index += 1
            if name in existing:
                continue
            seat = PlayerSeat(name=name, bot=BotRunner(self, name), ready=True, connected=False)
            self.seats.append(seat)
            existing.add(name)
            self._broadcast_ready_state(seat)

    def start_game_if_ready(self) -> None:
        if self.phase != "lobby":
            return
        if not self.seats:
            return
        if any(not seat.ready for seat in self.seats):
            return

        if self.fill_with_bots and len(self.seats) < MAX_ROOM_PLAYERS:
            self._spawn_bots_to_four_locked()
            self.broadcast(_message("PLAYERS", self.public_player_list()))

        if len(self.seats) < 2:
            return

        # Check again after spawning bots (bots are ready=True)
        if any(not seat.ready for seat in self.seats):
            return

        self._start_game_locked()

    def _start_game_locked(self) -> None:
        self.phase = "reorder"
        deck = build_deck(len(self.seats))
        for seat in self.seats:
            seat.hidden = [deck.pop() for _ in range(3)]
        for seat in self.seats:
            seat.visible = [deck.pop() for _ in range(3)]
        for seat in self.seats:
            seat.hand = [deck.pop() for _ in range(5)]
            seat.reorder_done = False
            seat.placement = None

        self.draw_pile = deck
        self.pile = []
        self.initial_start_name = choose_start_player(
            [(seat.name, seat.hand + seat.visible + seat.hidden) for seat in self.seats]
        )
        self.turn_index = self.seat_index(self.initial_start_name)
        self.turn_bonus_available = False
        self.reorder_start_time = time.monotonic()

        self.broadcast("START_GAME")
        self.send_public_state()

        for seat in self.seats:
            if seat.bot:
                self.request_bot_reorder_async(seat.name)

    REORDER_MIN_DURATION = 5.0

    def begin_game_if_reordered(self) -> None:
        if self.phase != "reorder":
            return
        if any(not seat.reorder_done for seat in self.seats):
            return
        if time.monotonic() - self.reorder_start_time < self.REORDER_MIN_DURATION:
            remaining = self.REORDER_MIN_DURATION - (time.monotonic() - self.reorder_start_time)
            threading.Timer(remaining, self.begin_game_if_reordered).start()
            return

        self.phase = "game"
        self.turn_index = self.seat_index(self.initial_start_name)
        self.turn_bonus_available = False
        self.broadcast("BEGIN")
        self.announce_turn()

    def announce_turn(self) -> None:
        seat = self.current_turn_seat()
        if seat is None:
            return
        self.turn_deadline = time.monotonic() + 60.0
        self.broadcast(_message("TURN_START", seat.name))
        if seat.bot:
            self.request_bot_move_async(seat.name)

    def request_bot_reorder_async(self, seat_name: str) -> None:
        threading.Thread(target=self._request_bot_reorder, args=(seat_name,), daemon=True).start()

    def request_bot_move_async(self, seat_name: str) -> None:
        threading.Thread(target=self._request_bot_move, args=(seat_name,), daemon=True).start()

    def _request_bot_reorder(self, seat_name: str) -> None:
        time.sleep(0.25)
        with self.server.lock:
            seat = self.seat_by_name(seat_name)
            if seat is None or seat.bot is None or self.phase != "reorder" or not seat.active:
                return
            state = self.state_for_bot(seat)
            bot = seat.bot

        assert bot is not None
        command = bot.request_command(state, timeout=2.0)
        if not command:
            command = bot_choose_action(state)
        self.submit_command(seat_name, command, source="bot")

        with self.server.lock:
            seat = self.seat_by_name(seat_name)
            if seat and self.phase == "reorder" and not seat.reorder_done:
                seat.reorder_done = True
        self.begin_game_if_reordered()

    def _request_bot_move(self, seat_name: str) -> None:
        time.sleep(1.2)
        with self.server.lock:
            seat = self.seat_by_name(seat_name)
            if seat is None or seat.bot is None or self.phase != "game" or not seat.active:
                return
            current = self.current_turn_seat()
            if current is None or current.name != seat_name:
                return
            state = self.state_for_bot(seat)
            bot = seat.bot

        assert bot is not None
        command = bot.request_command(state, timeout=2.0)
        if not command:
            command = bot_choose_action(state)
        self.submit_command(seat_name, command, source="bot")

    def replace_with_bot(self, seat_name: str, reason: str = "disconnect") -> None:
        seat = self.seat_by_name(seat_name)
        if seat is None:
            return
        session_to_close: ClientSession | None = None
        if seat.bot is None:
            seat.bot = BotRunner(self, seat.name)
        if seat.session is not None:
            session_to_close = seat.session
            seat.session = None
        seat.connected = False
        seat.ready = True
        if self.phase in {"reorder", "game"}:
            seat.reorder_done = True if self.phase == "game" else seat.reorder_done
        self.broadcast(_message("PLAYER_QUIT", seat.name))
        log.warning(f"Room {self.code}: seat {seat.name} replaced with bot ({reason})")

        if session_to_close is not None:
            session_to_close.close()

        if self.phase == "reorder":
            self.request_bot_reorder_async(seat_name)
            self.begin_game_if_reordered()
        elif self.phase == "game":
            current = self.current_turn_seat()
            if current and current.name == seat_name:
                self.request_bot_move_async(seat_name)

    def remove_lobby_seat(self, seat_name: str, reason: str = "left") -> None:
        seat = self.seat_by_name(seat_name)
        if seat is None:
            return
        if seat.bot:
            seat.bot.close()
        if seat.session:
            seat.session.seat = None
            seat.session.room_code = self.code
            seat.session.close()
        self.seats = [other for other in self.seats if other.name != seat_name]
        self.broadcast(_message("PLAYER_LEFT", seat_name, reason))
        self.broadcast(_message("PLAYERS", self.public_player_list()))

    def _can_reorder_cards(self, seat: PlayerSeat, hand_cards: list[str], visible_cards: list[str]) -> bool:
        if len(hand_cards) != 5 or len(visible_cards) != 3:
            return False
        current = seat.hand + seat.visible
        return sorted(current) == sorted(hand_cards + visible_cards)

    def _broadcast_seat_cards(self, seat: PlayerSeat) -> None:
        self.broadcast(_message("VISIBLE", seat.name, format_cards(seat.visible)))
        self.broadcast(_message("HAND_COUNT", seat.name, str(len(seat.hand))))
        self.broadcast(_message("HIDDEN_COUNT", seat.name, str(len(seat.hidden))))
        self.send_private(seat.name, _message("HAND", format_cards(seat.hand)))

    def has_any_legal_play(self, seat: PlayerSeat) -> bool:
        top_face = self.current_top_face()
        if seat.hand:
            if any(card_rank(token) in {"2", "T"} for token in seat.hand):
                return True
            if any(can_play_on(top_face, card_face(token)) for token in seat.hand):
                return True
            if not seat.visible and seat.hidden:
                hand_ranks = {card_rank(token) for token in seat.hand}
                if len(hand_ranks) == 1:
                    hand_rank = next(iter(hand_ranks))
                    return any(
                        can_play_on(top_face, card_face(token)) and card_rank(token) == hand_rank
                        for token in seat.hidden
                    )
            return False
        if seat.visible:
            if any(card_rank(token) in {"2", "T"} for token in seat.visible):
                return True
            return any(can_play_on(top_face, card_face(token)) for token in seat.visible)
        if seat.hidden:
            return any(can_play_on(top_face, card_face(token)) for token in seat.hidden)
        return False

    def _advance_turn_locked(self) -> None:
        self.turn_index = self.next_active_index(self.turn_index)
        self.turn_bonus_available = False
        self.turn_deadline = 0.0

        active = [seat for seat in self.seats if seat.active]
        if len(active) == 1:
            last = active[0]
            if last.placement is None:
                last.placement = len(self.seats)
                self.broadcast(_message("PLAYER_WIN", last.name, str(last.placement)))
            self.phase = "finished"
            winner = next((seat for seat in self.seats if seat.placement == 1), last)
            self.broadcast(_message("GAME_OVER", winner.name))

    def _draw_to_five_locked(self, seat: PlayerSeat) -> list[str]:
        drawn: list[str] = []
        while len(seat.hand) + len(drawn) < 5 and self.draw_pile:
            drawn.append(self.draw_pile.pop())
        if drawn:
            self.broadcast(_message("DRAW_CARDS", seat.name, str(len(drawn))))
            seat.hand.extend(drawn)
            seat.hand = sort_hand(seat.hand)
        return drawn

    def _draw_pile_into_hand_locked(self, seat: PlayerSeat) -> list[str]:
        drawn = list(self.pile)
        if drawn:
            seat.hand.extend(drawn)
            seat.hand = sort_hand(seat.hand)
            self.pile.clear()
        return drawn

    def _collect_failed_play_rank_locked(self, seat: PlayerSeat, failed_token: str) -> list[str]:
        failed_rank = card_rank(failed_token)
        collected: list[str] = []

        hand_matches = [token for token in seat.hand if card_rank(token) == failed_rank]
        if hand_matches:
            for token in hand_matches:
                seat.hand.remove(token)
            collected.extend(hand_matches)

        visible_matches = [token for token in seat.visible if card_rank(token) == failed_rank]
        if visible_matches:
            for token in visible_matches:
                seat.visible.remove(token)
            collected.extend(visible_matches)

        # Only remove the specific hidden card that was played, not all of that rank
        if failed_token in seat.hidden:
            seat.hidden.remove(failed_token)
            collected.append(failed_token)

        # Collect all cards of the failed rank from the pile
        pile_matches = [token for token in self.pile if card_rank(token) == failed_rank]
        for token in pile_matches:
            self.pile.remove(token)
        collected.extend(pile_matches)

        return collected

    def _check_win_locked(self, seat: PlayerSeat) -> bool:
        return seat.total_cards == 0

    def _play_tokens_locked(
        self,
        seat: PlayerSeat,
        tokens: list[str],
        from_hidden: bool = False,
        hidden_index: int | None = None,
    ) -> tuple[bool, str]:
        if time.monotonic() < self.play_lock_until:
            return False, "wait"
        if self.phase != "game":
            return False, "game has not started"

        current = self.current_turn_seat()
        if current is None or current.name != seat.name:
            return False, "not your turn"

        if from_hidden:
            if hidden_index is None or hidden_index < 0 or hidden_index >= len(seat.hidden):
                return False, "invalid hidden card index"
            if seat.visible:
                return False, "hidden cards are not available yet"
            hidden_token = seat.hidden[hidden_index]
            if seat.hand:
                hand_ranks = {card_rank(token) for token in seat.hand}
                if len(hand_ranks) != 1 or next(iter(hand_ranks)) != card_rank(hidden_token):
                    return False, "hidden cards are not available yet"
                tokens = list(seat.hand) + [hidden_token]
            else:
                tokens = [hidden_token]
        else:
            if not tokens:
                return False, "no cards selected"
            token_set = set(tokens)
            if len(token_set) != len(tokens):
                return False, "duplicate cards selected"
            current_cards = set(seat.hand + seat.visible)
            if not token_set.issubset(current_cards):
                return False, "selected cards are not in your hand or visible row"
            ranks = {card_rank(token) for token in tokens}
            if len(ranks) != 1:
                return False, "cards must be the same rank"
            rank = next(iter(ranks))
            selected_from_hand = [token for token in tokens if token in seat.hand]
            selected_from_visible = [token for token in tokens if token in seat.visible]
            if selected_from_visible and seat.hand:
                if len(selected_from_hand) != len(seat.hand):
                    return False, "visible cards can only be played together with your last matching hand cards"
                if len({card_rank(token) for token in seat.hand}) != 1 or card_rank(seat.hand[0]) != rank:
                    return False, "your hand must all match the visible cards to combine them"
            if seat.hand and selected_from_visible and len(selected_from_hand) == len(seat.hand):
                pass
            elif seat.hand and selected_from_visible:
                return False, "visible cards are not available yet"

        played_rank = card_rank(tokens[0])
        played_face = card_face(tokens[0])
        top_face = self.current_top_face()
        playable = can_play_on(top_face, played_face) or played_rank in {"2", "T"}
        hidden_misplay = from_hidden and not playable
        if not playable and not hidden_misplay:
            return False, "card cannot be played on the current pile"

        if from_hidden:
            hidden_token = seat.hidden.pop(hidden_index or 0)
            played_tokens = list(tokens)
            if seat.hand:
                for token in list(seat.hand):
                    seat.hand.remove(token)
            tokens = played_tokens
            if hidden_token not in tokens:
                tokens.append(hidden_token)
        else:
            for token in tokens:
                if token in seat.hand:
                    seat.hand.remove(token)
                elif token in seat.visible:
                    seat.visible.remove(token)

        for token in tokens:
            self.pile.append(token)

        if hidden_misplay:
            collected = self._collect_failed_play_rank_locked(seat, tokens[0])
            if collected:
                seat.hand.extend(collected)
                seat.hand = sort_hand(seat.hand)
            return True, "hidden_fail"

        four_kind = len(self.pile) >= 4 and len({card_rank(token) for token in self.pile[-4:]}) == 1
        special_reset = played_rank == "T" or four_kind
        return True, "special" if special_reset else "ok"

    def _finalize_play_locked(self, seat: PlayerSeat, tokens: list[str], from_hidden: bool = False) -> None:
        if from_hidden:
            self.broadcast(_message("PLAY_HIDDEN", seat.name, format_cards(tokens)))
        else:
            self.broadcast(_message("PLAY", seat.name, format_cards(tokens)))

        top_token = self.pile[-1] if self.pile else tokens[-1]
        self.broadcast(_message("PILE_UPDATE", top_token, str(len(self.pile))))
        self.broadcast(_message("DRAW_PILE_SIZE", str(len(self.draw_pile))))
        self._broadcast_seat_cards(seat)

    def _after_successful_play_locked(self, seat: PlayerSeat, special_reset: bool) -> None:
        if self._check_win_locked(seat):
            if seat.placement is None:
                seat.placement = len([player for player in self.seats if player.placement is not None]) + 1
                self.broadcast(_message("PLAYER_WIN", seat.name, str(seat.placement)))

            active = [player for player in self.seats if player.active]
            if len(active) == 1:
                last = active[0]
                if last.placement is None:
                    last.placement = len(self.seats)
                    self.broadcast(_message("PLAYER_WIN", last.name, str(last.placement)))
                self.phase = "finished"
                self.broadcast(_message("GAME_OVER", seat.name))
                return

            # Winner is out immediately; move to next active player.
            self._advance_turn_locked()
            if self.phase == "game":
                self.announce_turn()
            return

        if self.turn_bonus_available:
            self.turn_bonus_available = False
            self._draw_to_five_locked(seat)
            self._broadcast_seat_cards(seat)
            self._advance_turn_locked()
            if self.phase == "game":
                self.announce_turn()
            return

        if special_reset:
            self.pile.clear()
            self.play_lock_until = time.monotonic() + 0.6
            threading.Thread(target=self._delayed_pile_clear, args=(seat.name,), daemon=True).start()
            self.turn_bonus_available = True
            self.turn_deadline = time.monotonic() + 60.0
            if seat.bot:
                self.request_bot_move_async(seat.name)
            self.announce_turn()
            return

        self._draw_to_five_locked(seat)
        self._broadcast_seat_cards(seat)
        self._advance_turn_locked()
        if self.phase == "game":
            self.announce_turn()

    def _force_draw_locked(self, seat: PlayerSeat) -> None:
        drawn = self._draw_pile_into_hand_locked(seat)
        self.broadcast(_message("DRAW_ALL", seat.name))
        if drawn:
            self.broadcast(_message("PILE_CLEAR", seat.name))
            self.broadcast(_message("DRAW_PILE_SIZE", str(len(self.draw_pile))))
            self._broadcast_seat_cards(seat)

    def _schedule_hidden_fail_draw_locked(self, seat_name: str) -> None:
        self.play_lock_until = time.monotonic() + HIDDEN_MISPLAY_DRAW_DELAY
        threading.Thread(target=self._delayed_hidden_fail_draw, args=(seat_name,), daemon=True).start()

    def _delayed_hidden_fail_draw(self, seat_name: str) -> None:
        time.sleep(HIDDEN_MISPLAY_DRAW_DELAY)
        with self.server.lock:
            self.play_lock_until = 0.0
            if self.phase != "game":
                return
            seat = self.seat_by_name(seat_name)
            if seat is None or not seat.active:
                return
            current = self.current_turn_seat()
            if current is None or current.name != seat_name:
                return
            self._force_draw_locked(seat)
            self._advance_turn_locked()
            if self.phase == "game":
                self.announce_turn()

    def _delayed_pile_clear(self, seat_name: str) -> None:
        time.sleep(0.6)
        with self.server.lock:
            if self.phase != "game":
                return
            self.play_lock_until = 0.0
            self.broadcast(_message("PILE_CLEAR", seat_name))

    def _handle_quit(self, session: ClientSession) -> None:
        seat = session.seat
        if seat is None:
            return

        if self.phase == "lobby":
            self.seats = [other for other in self.seats if other.name != seat.name]
            session.seat = None
            session.room_code = self.code
            self.broadcast(_message("PLAYER_LEFT", seat.name, "quit"))
            self.broadcast(_message("PLAYERS", self.public_player_list()))
            self.server.maybe_delete_room_locked(self.code)
            return

        if seat.session is session:
            seat.session = None
        seat.connected = False
        if seat.bot is None:
            seat.bot = BotRunner(self, seat.name)

        seat.ready = True
        if self.phase == "reorder":
            seat.reorder_done = True
        self.broadcast(_message("PLAYER_QUIT", seat.name))

        if self.phase == "reorder":
            self.begin_game_if_reordered()
        elif self.phase == "game":
            current = self.current_turn_seat()
            if current and current.name == seat.name:
                self.request_bot_move_async(seat.name)

    def handle_disconnect(self, session: ClientSession) -> None:
        seat = session.seat
        if seat is None:
            return

        if seat.session is session:
            seat.session = None

        if self.phase == "lobby":
            self.seats = [other for other in self.seats if other.name != seat.name]
            self.broadcast(_message("PLAYER_LEFT", seat.name, "disconnect"))
            self.broadcast(_message("PLAYERS", self.public_player_list()))
            self.server.maybe_delete_room_locked(self.code)
            return

        seat.connected = False
        if seat.bot is None:
            self.replace_with_bot(seat.name, reason="disconnect")

    def set_fill_bots(self, enabled: bool) -> None:
        if self.phase != "lobby":
            return
        self.fill_with_bots = enabled
        self.broadcast(_message("LOBBY_FILL_BOTS", "1" if self.fill_with_bots else "0"))

    def check_timeout_locked(self) -> None:
        if self.phase != "game":
            return
        seat = self.current_turn_seat()
        if seat is None or seat.bot is not None:
            return
        if self.turn_deadline and time.monotonic() > self.turn_deadline:
            self.replace_with_bot(seat.name, reason="timeout")

    def submit_command(self, session: ClientSession | str, line: str, source: str = "client") -> None:
        with self.server.lock:
            self._submit_command_locked(session, line, source=source)

    def _submit_command_locked(self, session: ClientSession | str, line: str, source: str = "client") -> None:
        raw = line.strip()
        if not raw:
            return

        if " | " in raw:
            parts = [part.strip() for part in raw.split(" | ")]
            command = parts[0].upper()
            args = parts[1:]
        else:
            command, _, rest = raw.partition(" ")
            command = command.upper()
            args = [rest.strip()] if rest.strip() else []

        if isinstance(session, str):
            seat = self.seat_by_name(session)
            session_obj = seat.session if seat is not None else None
        else:
            seat = session.seat
            session_obj = session

        def reply(text: str) -> None:
            if session_obj is not None:
                session_obj.send(_message("INVALID", text))

        if seat is None:
            reply("join first")
            return

        if command == "QUIT":
            if session_obj is not None:
                self._handle_quit(session_obj)
            return

        if command == "SET_FILL_BOTS":
            if self.phase != "lobby":
                reply("cannot change bot fill now")
                return
            if not args:
                reply("missing fill bots flag")
                return
            value = args[0].strip().lower()
            if value not in {"0", "1", "false", "true", "off", "on"}:
                reply("fill bots must be 0 or 1")
                return
            enabled = value in {"1", "true", "on"}
            self.set_fill_bots(enabled)
            return

        if command == "READY":
            if self.phase != "lobby":
                reply("not in lobby")
                return
            seat.ready = True
            self._broadcast_ready_state(seat)
            self.start_game_if_ready()
            return

        if command == "REORDER":
            if self.phase not in {"reorder", "game"}:
                reply("cannot reorder now")
                return
            if len(args) < 2:
                reply("missing reorder data")
                return
            hand_cards = parse_cards(args[0])
            visible_cards = parse_cards(args[1])
            if not self._can_reorder_cards(seat, hand_cards, visible_cards):
                reply("reorder does not match current cards")
                return
            seat.hand = hand_cards
            seat.visible = visible_cards
            self._broadcast_seat_cards(seat)
            if source == "bot" and self.phase == "reorder":
                seat.reorder_done = True
                self.begin_game_if_reordered()
            return

        if command == "REORDER_DONE":
            if self.phase != "reorder":
                reply("not in reorder phase")
                return
            seat.reorder_done = True
            self.begin_game_if_reordered()
            return

        if command in {"PLAY", "PLAY_HIDDEN", "DRAW"}:
            if self.phase != "game":
                reply("game has not started")
                return

            current = self.current_turn_seat()
            if current is None or current.name != seat.name:
                reply("not your turn")
                return

            if command == "DRAW":
                if self.has_any_legal_play(seat):
                    reply("you still have playable cards")
                    return
                self._force_draw_locked(seat)
                self._advance_turn_locked()
                if self.phase == "game":
                    self.announce_turn()
                return

            if command == "PLAY_HIDDEN":
                if not args:
                    reply("missing hidden card index")
                    return
                try:
                    hidden_index = int(args[0])
                except ValueError:
                    reply("hidden index must be a number")
                    return
                if hidden_index < 0 or hidden_index >= len(seat.hidden):
                    reply("invalid hidden card index")
                    return

                hidden_token = seat.hidden[hidden_index]
                played_tokens = [hidden_token]
                hand_ranks = {card_rank(token) for token in seat.hand}
                if seat.hand and len(hand_ranks) == 1 and next(iter(hand_ranks)) == card_rank(hidden_token):
                    played_tokens = list(seat.hand) + [hidden_token]

                ok, result = self._play_tokens_locked(
                    seat, [hidden_token], from_hidden=True, hidden_index=hidden_index
                )
                if not ok:
                    reply(result)
                    return

                self._finalize_play_locked(seat, played_tokens, from_hidden=True)
                if result == "hidden_fail":
                    self.turn_deadline = time.monotonic() + HIDDEN_MISPLAY_DRAW_DELAY + 1.0
                    self._schedule_hidden_fail_draw_locked(seat.name)
                    return

                special_reset = result == "special"
                self.turn_deadline = time.monotonic() + 60.0
                self._after_successful_play_locked(seat, special_reset)
                return

            selected = parse_cards(args[0] if args else "")
            ok, result = self._play_tokens_locked(seat, selected)
            if not ok:
                reply(result)
                return
            special_reset = result == "special"
            self._finalize_play_locked(seat, selected, from_hidden=False)
            self.turn_deadline = time.monotonic() + 60.0
            self._after_successful_play_locked(seat, special_reset)
            return

        reply(f"unknown command {command}")

class GameServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.lock = threading.RLock()
        self.running = True
        self.rooms: dict[str, Room] = {}
        self._accept_thread: Optional[threading.Thread] = None
        self._timer_thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None

    def run(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._socket.listen()
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()
        log.info(f"Server listening on ws://{self.host}:{self.port}")
        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.running = False
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self.running = False
        with self.lock:
            for room in self.rooms.values():
                for seat in room.seats:
                    if seat.bot:
                        seat.bot.close()
                    if seat.session:
                        seat.session.close()
            self.rooms.clear()
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
        log.info("Server shut down")

    def _accept_loop(self) -> None:
        assert self._socket is not None
        while self.running:
            try:
                client, address = self._socket.accept()
                conn = accept_websocket(client)
                log.debug(f"Accepted connection from {address[0]}:{address[1]}")
                ClientSession(self, conn, address)
            except OSError:
                break
            except Exception:
                continue

    def _timer_loop(self) -> None:
        while self.running:
            time.sleep(0.25)
            with self.lock:
                for room in list(self.rooms.values()):
                    room.check_timeout_locked()
                for room_code in list(self.rooms.keys()):
                    self.maybe_delete_room_locked(room_code)

    def generate_room_code_locked(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        for _ in range(200):
            code = "".join(random.choice(alphabet) for _ in range(5))
            if code not in self.rooms:
                return code
        raise RuntimeError("unable to allocate room code")

    def create_room_locked(self, fill_with_bots: bool = True) -> Room:
        code = self.generate_room_code_locked()
        room = Room(self, code, fill_with_bots=fill_with_bots)
        self.rooms[code] = room
        return room

    def maybe_delete_room_locked(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if room is None:
            return

        if not room.seats:
            # Keep empty lobby rooms alive so creator can select room code first
            # and send JOIN_GAME afterward.
            if room.phase != "lobby":
                self.rooms.pop(room_code, None)
                log.info(f"Room removed: {room_code} (empty)")
            return

        has_live_session = any(seat.session and seat.session.alive for seat in room.seats)
        if room.phase in {"lobby", "finished"} and not has_live_session:
            for seat in room.seats:
                if seat.bot:
                    seat.bot.close()
            self.rooms.pop(room_code, None)
            log.info(f"Room removed: {room_code} (no active clients)")

    def resolve_room_for_session_locked(self, session: ClientSession) -> Room | None:
        if session.seat is not None:
            for room in self.rooms.values():
                if any(seat is session.seat for seat in room.seats):
                    session.room_code = room.code
                    return room
            return None
        if session.room_code:
            return self.rooms.get(session.room_code)
        return None

    def submit_command(self, session: ClientSession, line: str, source: str = "client") -> None:
        raw = line.strip()
        if not raw:
            return

        if " | " in raw:
            parts = [part.strip() for part in raw.split(" | ")]
            command = parts[0].upper()
            args = parts[1:]
        else:
            command, _, rest = raw.partition(" ")
            command = command.upper()
            args = [rest.strip()] if rest.strip() else []

        with self.lock:
            def reply(text: str) -> None:
                session.send(_message("INVALID", text))

            if command == "CREATE_ROOM":
                if session.seat is not None:
                    reply("already joined")
                    return
                enabled = True
                if args:
                    value = args[0].strip().lower()
                    if value not in {"0", "1", "false", "true", "off", "on"}:
                        reply("fill bots must be 0 or 1")
                        return
                    enabled = value in {"1", "true", "on"}
                room = self.create_room_locked(fill_with_bots=enabled)
                session.room_code = room.code
                room.send_room_context(session)
                log.info(f"Room created: {room.code}")
                return

            if command == "ROOM":
                if not args:
                    reply("missing room code")
                    return
                code = args[0].strip().upper()
                if not ROOM_CODE_RE.match(code):
                    reply("invalid room code")
                    return
                room = self.rooms.get(code)
                if room is None:
                    reply("room not found")
                    return
                if room.phase != "lobby" and session.seat is None:
                    reply("room already in game")
                    return
                if session.seat is not None:
                    current_room = self.resolve_room_for_session_locked(session)
                    if current_room is not None and current_room.code != code:
                        reply("already joined a room")
                        return
                session.room_code = code
                room.send_room_context(session)
                return

            if command == "JOIN_GAME":
                if session.seat is not None:
                    reply("already joined")
                    return
                if not args:
                    reply("missing username")
                    return
                if not session.room_code:
                    reply("select room first")
                    return
                room = self.rooms.get(session.room_code)
                if room is None:
                    reply("room not found")
                    return
                ok, err = room.join_player(session, args[0])
                if not ok:
                    reply(err)
                return

            room = self.resolve_room_for_session_locked(session)
            if room is None:
                reply("select room first")
                return

            room._submit_command_locked(session, raw, source=source)

    def handle_disconnect(self, session: ClientSession) -> None:
        with self.lock:
            room = self.resolve_room_for_session_locked(session)
            if room is None:
                return
            room.handle_disconnect(session)
            self.maybe_delete_room_locked(room.code)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multiplayer card game websocket server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("-d", action="store_true")
    args = parser.parse_args(argv)
    GameServer(args.host, args.port).run()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
