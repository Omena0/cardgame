from __future__ import annotations

import argparse
import json
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.net import WebSocketConnection, accept_websocket
from src.shared import (
    bot_choose_action,
    build_deck,
    can_play_on,
    card_face,
    card_rank,
    choose_start_player,
    count_ranks,
    format_cards,
    parse_cards,
    pile_rank_value,
    sort_hand,
    token_label,
)
from logging import Logger, LogLevel

NAME_RE = re.compile(r"^[A-Za-z0-9_ ]{4,20}$")
BOT_PATH = Path(__file__).with_name("bot.py")
log = Logger("server")
log.level = LogLevel.TRACE if '-d' in sys.argv else LogLevel.INFO
HIDDEN_MISPLAY_DRAW_DELAY = 0.5

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
    def __init__(self, server: "GameServer", seat_name: str) -> None:
        self.server = server
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

                log.trace(f'Recv: {line}')

                self.server.submit_command(self, line, source="client")
        except Exception:
            pass
        finally:
            self.alive = False
            self.server.handle_disconnect(self)


class GameServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.lock = threading.RLock()
        self.running = True
        self.phase = "pregame"
        self.seats: list[PlayerSeat] = []
        self.turn_index = 0
        self.turn_bonus_available = False
        self.turn_deadline = 0.0
        self.pile: list[str] = []
        self.draw_pile: list[str] = []
        self.initial_start_name: str | None = None
        self.play_lock_until = 0.0
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
            for seat in self.seats:
                if seat.bot:
                    seat.bot.close()
                if seat.session:
                    seat.session.close()
            self.seats.clear()
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
            timed_out_name: str | None = None
            with self.lock:
                if self.phase != "game":
                    continue
                seat = self.current_turn_seat()
                if seat is None or seat.bot is not None:
                    continue
                if self.turn_deadline and time.monotonic() > self.turn_deadline:
                    timed_out_name = seat.name
            if timed_out_name:
                self.replace_with_bot(timed_out_name, reason="timeout")

    def broadcast(self, line: str) -> None:
        with self.lock:
            for seat in self.seats:
                if seat.session and seat.session.alive:
                    seat.session.send(line)

    def send_private(self, seat_name: str, line: str) -> None:
        with self.lock:
            seat = self.seat_by_name(seat_name)
            if seat and seat.session and seat.session.alive:
                seat.session.send(line)

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

    def public_player_list(self) -> str:
        return ",".join(seat.name for seat in self.seats)

    def current_top_face(self) -> str | None:
        return card_face(self.pile[-1]) if self.pile else None

    def current_draw_count(self) -> int:
        return len(self.draw_pile)

    def state_for_bot(self, seat: PlayerSeat) -> dict:
        current = self.current_turn_seat()
        return {
            "phase": self.phase,
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

    def start_game_if_ready(self) -> None:
        with self.lock:
            if self.phase != "pregame":
                return
            if len(self.seats) == 1:
                self._spawn_solo_bots_locked()
            if len(self.seats) < 2:
                return
            if any(not seat.ready for seat in self.seats):
                return
            self._start_game_locked()

    def _spawn_solo_bots_locked(self) -> None:
        if len(self.seats) != 1:
            return
        existing = {seat.name for seat in self.seats}
        index = 1
        while len(self.seats) < 4:
            name = f"Bot{index}"
            index += 1
            if name in existing:
                continue
            seat = PlayerSeat(name=name, bot=BotRunner(self, name), ready=True, connected=False)
            self.seats.append(seat)
            existing.add(name)
        self.broadcast(_message("PLAYERS", self.public_player_list()))

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
        self.draw_pile = deck
        self.pile = []
        self.initial_start_name = choose_start_player([(seat.name, seat.hand + seat.visible + seat.hidden) for seat in self.seats])
        self.turn_index = self.seat_index(self.initial_start_name)
        self.turn_bonus_available = False
        self.broadcast("START_GAME")
        self.send_public_state()
        for seat in self.seats:
            if seat.bot:
                self.request_bot_reorder_async(seat.name)

    def seat_index(self, seat_name: str) -> int:
        for index, seat in enumerate(self.seats):
            if seat.name == seat_name:
                return index
        return 0

    def begin_game_if_reordered(self) -> None:
        with self.lock:
            if self.phase != "reorder":
                return
            if any(not seat.reorder_done for seat in self.seats):
                return
            self.phase = "game"
            self.turn_index = self.seat_index(self.initial_start_name) if self.initial_start_name else 0
            self.turn_bonus_available = False
            self.broadcast("BEGIN")
        self.announce_turn()

    def announce_turn(self) -> None:
        with self.lock:
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
        with self.lock:
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
        with self.lock:
            seat = self.seat_by_name(seat_name)
            if seat and self.phase == "reorder" and not seat.reorder_done:
                seat.reorder_done = True
        self.begin_game_if_reordered()

    def _request_bot_move(self, seat_name: str) -> None:
        time.sleep(0.85)
        with self.lock:
            seat = self.seat_by_name(seat_name)
            if seat is None or seat.bot is None or self.phase != "game" or not seat.active:
                return
            if self.current_turn_seat() is None or self.current_turn_seat().name != seat_name:
                return
            state = self.state_for_bot(seat)
            bot = seat.bot
        assert bot is not None
        command = bot.request_command(state, timeout=2.0)
        if not command:
            command = bot_choose_action(state)
        self.submit_command(seat_name, command, source="bot")

    def replace_with_bot(self, seat_name: str, reason: str = "disconnect") -> None:
        session_to_close: ClientSession | None = None
        with self.lock:
            seat = self.seat_by_name(seat_name)
            if seat is None:
                return
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
            log.warning(f"Seat {seat.name} replaced with bot ({reason})")
        if session_to_close is not None:
            session_to_close.close()
        if self.phase == "reorder":
            self.request_bot_reorder_async(seat_name)
            self.begin_game_if_reordered()
        elif self.phase == "game" and self.current_turn_seat() and self.current_turn_seat().name == seat_name:
            self.request_bot_move_async(seat_name)

    def remove_seat(self, seat_name: str, reason: str = "left") -> None:
        with self.lock:
            seat = self.seat_by_name(seat_name)
            if seat is None:
                return
            if seat.bot:
                seat.bot.close()
            if seat.session:
                seat.session.close()
            self.seats = [other for other in self.seats if other.name != seat_name]
            self.broadcast(_message("PLAYER_LEFT", seat_name, reason))
            self.broadcast(_message("PLAYERS", self.public_player_list()))
            log.info(f"Player removed before game start: {seat_name} ({reason})")
        self.start_game_if_ready()

    def handle_disconnect(self, session: ClientSession) -> None:
        with self.lock:
            seat = session.seat
            if seat is None:
                return
            if seat.session is session:
                seat.session = None
            if self.phase == "pregame":
                seat_name = seat.name
            else:
                seat.connected = False
                needs_bot = seat.bot is None
                seat_name = None
        if self.phase == "pregame" and seat_name:
            self.remove_seat(seat_name, reason="disconnect")
        elif needs_bot:
            self.replace_with_bot(seat.name, reason="disconnect")

    def join_player(self, session: ClientSession, name: str) -> None:
        with self.lock:
            if self.phase != "pregame":
                session.send(_message("INVALID", "game already started"))
                session.close()
                return
            if not NAME_RE.match(name):
                session.send(_message("INVALID", "invalid username"))
                session.close()
                return
            if self.seat_by_name(name) is not None:
                session.send(_message("INVALID", "name already taken"))
                session.close()
                return
            seat = PlayerSeat(name=name, session=session, connected=True)
            session.seat = seat
            self.seats.append(seat)
            self.broadcast(_message("PLAYERS", self.public_player_list()))
            log.info(f"Player joined: {name}")

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
            self.broadcast(_message("GAME_OVER", last.name))
            return

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

    def _check_win_locked(self, seat: PlayerSeat) -> bool:
        return seat.total_cards == 0

    def _play_tokens_locked(self, seat: PlayerSeat, tokens: list[str], from_hidden: bool = False, hidden_index: int | None = None) -> tuple[bool, str]:
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
            return True, "hidden_fail"

        four_kind = len(self.pile) >= 4 and len({card_rank(token) for token in self.pile[-4:]}) == 1
        special_reset = played_rank == "T" or four_kind
        return True, "special" if special_reset else "ok"

    def _finalize_play_locked(self, seat: PlayerSeat, tokens: list[str], result: str, from_hidden: bool = False) -> None:
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
                self.broadcast(_message("GAME_OVER", last.name))
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
            self.play_lock_until = time.monotonic() + 0.2
            threading.Thread(target=self._delayed_pile_clear, args=(seat.name,), daemon=True).start()
            self.turn_bonus_available = True
            self.turn_deadline = time.monotonic() + 60.0
            if seat.bot:
                self.request_bot_move_async(seat.name)
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
        with self.lock:
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
        time.sleep(0.2)
        with self.lock:
            if self.phase != "game":
                return
            self.play_lock_until = 0.0
            self.broadcast(_message("PILE_CLEAR", seat_name))

    def _handle_quit(self, session: ClientSession) -> None:
        seat_name = None
        with self.lock:
            seat = session.seat
            if seat is None:
                return
            seat_name = seat.name
            if self.phase == "pregame":
                pass
            else:
                if seat.session is session:
                    seat.session = None
                seat.connected = False
                if seat.bot is None:
                    seat.bot = BotRunner(self, seat.name)
                seat.ready = True
                if self.phase == "reorder":
                    seat.reorder_done = True
                self.broadcast(_message("PLAYER_QUIT", seat.name))
        if self.phase == "pregame" and seat_name:
            self.remove_seat(seat_name, reason="quit")
        elif self.phase == "reorder":
            self.begin_game_if_reordered()
        elif self.phase == "game" and seat_name and self.current_turn_seat() and self.current_turn_seat().name == seat_name:
            self.request_bot_move_async(seat_name)

    def submit_command(
        self, session: ClientSession | str, line: str, source: str = "client"
    ) -> None:
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
            if isinstance(session, str):
                seat = self.seat_by_name(session)
                session_obj = seat.session if seat is not None else None
            else:
                seat = session.seat
                session_obj = session

            def reply(text: str) -> None:
                if session_obj is not None:
                    session_obj.send(_message("INVALID", text))

            if command == "JOIN_GAME":
                if seat is not None:
                    reply("already joined")
                    return
                if not args:
                    reply("missing username")
                    return
                if session_obj is not None:
                    self.join_player(session_obj, args[0])
                return

            if seat is None:
                reply("join first")
                return

            if command == "QUIT":
                if session_obj is not None:
                    self._handle_quit(session_obj)
                return

            if command == "READY":
                if self.phase != "pregame":
                    reply("not in pregame")
                    return
                seat.ready = True
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
                    self._finalize_play_locked(seat, played_tokens, result, from_hidden=True)
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
                self._finalize_play_locked(seat, selected, result)
                self.turn_deadline = time.monotonic() + 60.0
                self._after_successful_play_locked(seat, special_reset)
                return

            reply(f"unknown command {command}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Multiplayer card game websocket server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('-d', action='store_true')
    args = parser.parse_args(argv)
    GameServer(args.host, args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
