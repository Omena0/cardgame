from __future__ import annotations

import argparse
import math
import queue
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pygame

import pygame_textinput as pygame_textinput
from anim import distance_based_duration
from caching import Pool, cache
from cards import load_card
from easing import EASE_OUT, EasingType, ease
from events import draw, event, handle_events
from logging import Logger, LogLevel
from net import WebSocketConnection, connect_websocket
import sys
from shared import (
    card_face,
    card_rank,
    format_cards,
    parse_cards,
    sort_hand,
    token_label,
)

pygame.init()

log:Logger = Logger("client")
Logger.level = LogLevel.TRACE if '-d' in sys.argv else LogLevel.INFO

SURFACE_DURATION = distance_based_duration(0.05, 0.0025)

def _load_face_surface(face: str) -> pygame.Surface:
    image = load_card(face)
    return image.convert_alpha() if image.get_alpha() is not None else image.convert()


def _make_back_surface(size: tuple[int, int]) -> pygame.Surface:
    return pygame.transform.smoothscale(load_card("1B"), size)


FACE_POOL = Pool(_load_face_surface)
BACK_POOL = Pool(_make_back_surface)
SCALED_FACE_POOL = Pool(
    lambda key: pygame.transform.smoothscale(FACE_POOL[key[0]], key[1])
)

SCREEN_W = 1600
SCREEN_H = 960
FPS = 60

PLAY_CARD_SIZE = (92, 132)
SMALL_CARD_SIZE = (72, 104)
BACK_CARD_SIZE = (92, 132)

TABLE_GREEN = (27, 76, 51)
TABLE_GREEN_DARK = (14, 44, 30)
PANEL = (20, 28, 30)
PANEL_SOFT = (35, 47, 48)
GOLD = (221, 187, 98)
RED = (220, 92, 92)
BLUE = (91, 139, 228)
WHITE = (245, 245, 240)
MUTED = (192, 198, 190)
BLACK = (18, 18, 18)
PLAY_ANIM_DURATION = 0.18
DRAW_ANIM_DURATION = 0.16
MENU_BUTTON_SIZE = (340, 68)

@dataclass
class Tween:
    start: tuple[float, float]
    end: tuple[float, float]
    start_time: float
    duration: float = 0.18
    easing: EasingType = EasingType.Quad

    def sample(self, now: float) -> tuple[float, float]:
        if self.duration <= 0:
            return self.end
        t = max(0.0, min(1.0, (now - self.start_time) / self.duration))
        eased = ease(self.easing, EASE_OUT, t)
        x = self.start[0] + (self.end[0] - self.start[0]) * eased
        y = self.start[1] + (self.end[1] - self.start[1]) * eased
        return x, y

    def finished(self, now: float) -> bool:
        return (now - self.start_time) >= self.duration

def face_surface(face: str) -> pygame.Surface:
    return FACE_POOL[face]

def scaled_face_surface(face: str, width: int, height: int) -> pygame.Surface:
    return SCALED_FACE_POOL[(face, (width, height))]

def back_surface(width: int, height: int) -> pygame.Surface:
    return BACK_POOL[(width, height)]

def rounded_panel(
    surface: pygame.Surface,
    rect: pygame.Rect,
    color: tuple[int, int, int],
    radius: int = 16,
    alpha: int = 255,
) -> None:
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(panel, (*color, alpha), panel.get_rect(), border_radius=radius)
    surface.blit(panel, rect.topleft)

def draw_text(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    pos: tuple[int, int],
    color: tuple[int, int, int],
    center: bool = False,
) -> pygame.Rect:
    image = font.render(text, True, color)
    rect = image.get_rect()
    if center:
        rect.center = pos
    else:
        rect.topleft = pos
    surface.blit(image, rect)
    return rect

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

@cache
def player_layout_shell(
    player_count: int, width: int, height: int
) -> tuple[tuple[tuple[float, float], tuple[float, float], tuple[float, float], int, float, float]]:
    shell: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float], int, float, float]] = []

    center_x = width * 0.5
    center_y = height * 0.5

    outer_rx = width * 0.31
    outer_ry = height * 0.29

    visible_gap = 52.0
    hand_gap = 142.0
    side_card_edge_push = width * 0.08
    vertical_panel_pull = height * 0.062

    for index in range(player_count):
        theta = math.pi / 2 + (math.tau * index / max(1, player_count))
        radial_x = math.cos(theta)
        radial_y = math.sin(theta)
        tangent_x = -radial_y
        tangent_y = radial_x

        base_anchor = (
            center_x + radial_x * outer_rx,
            center_y + radial_y * outer_ry,
        )
        card_anchor = (
            base_anchor[0] + radial_x * side_card_edge_push * abs(radial_x),
            base_anchor[1] + radial_y * side_card_edge_push * abs(radial_x),
        )

        panel_pull = vertical_panel_pull * abs(radial_y)
        panel_center = (
            base_anchor[0] - radial_x * panel_pull,
            base_anchor[1] - radial_y * panel_pull,
        )

        visible_center = (
            card_anchor[0] + radial_x * visible_gap,
            card_anchor[1] + radial_y * visible_gap,
        )

        hand_center = (
            card_anchor[0] + radial_x * hand_gap,
            card_anchor[1] + radial_y * hand_gap,
        )

        angle = math.degrees(theta) - 90.0
        shell.append((panel_center, visible_center, hand_center, int(angle), tangent_x, tangent_y))

    return tuple(shell)

class GameClient:
    def __init__(self, host: str, port: int, name: str) -> None:
        self.host = host
        self.port = port
        self.self_name = name
        self.conn: WebSocketConnection = connect_websocket(host, port)
        self.inbox: queue.Queue[str] = queue.Queue()
        self.reader = threading.Thread(target=self._recv_loop, daemon=True)
        self.reader.start()

        self.players: list[str] = []
        self.bot_seats: set[str] = set()
        self.phase = "menu"
        self.menu_screen = "main"
        self.turn_player: str | None = None
        self.turn_deadline: float | None = None
        self.status = "Main Menu"
        self.ready = False
        self.reorder_done = False
        self.room_code: str | None = None
        self.fill_bots_enabled = True
        self.ready_by_player: dict[str, bool] = {}
        self.auto_ready_after_join = False
        self.auto_done_reorder_once = False
        self.pending_auto_done_reorder = False
        self.pending_room_join = False
        self.pending_join_code = ""
        self.join_error: str | None = None
        self.tutorial_thread: threading.Thread | None = None

        self.self_hand: list[str] = []
        self.self_visible: list[str] = []
        self.self_hidden_count = 3

        self.visible_by_player: dict[str, list[str]] = {}

        self.hand_counts: dict[str, int] = {}
        self.hidden_counts: dict[str, int] = {}

        self.pile: list[str] = []
        self.draw_pile_size = 0
        self.finished: dict[str, int] = {}
        self.game_over_text: str | None = None

        self.selected: dict[str, object] | None = None
        self.selected_at = 0.0
        self.dragging: dict[str, object] | None = None
        self.last_card_click: tuple[str | None, bool, float] = (None, False, 0.0)
        self.messages = deque(maxlen=7)

        self.card_motion: dict[str, Tween] = {}
        self.last_positions: dict[str, tuple[float, float]] = {}
        self.play_animations: list[dict[str, object]] = []
        self.draw_animations: list[dict[str, object]] = []
        self.slot_layout: dict[str, dict[str, object]] = {}

        self.card_rects: dict[str, pygame.Rect] = {}
        self.hidden_rects: list[pygame.Rect] = []

        self.buttons: dict[str, pygame.Rect] = {}
        self.window = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)

        pygame.display.set_caption(f"Card Game - {name}")

        self.clock = pygame.time.Clock()
        self.running = True

        self.title_font = pygame.font.SysFont("DejaVu Sans", 28, bold=True)
        self.body_font = pygame.font.SysFont("DejaVu Sans", 20)
        self.small_font = pygame.font.SysFont("DejaVu Sans", 18)
        self.big_font = pygame.font.SysFont("DejaVu Sans", 52, bold=True)
        self.join_input = pygame_textinput.TextInputVisualizer(font_object=self.title_font)

        self.push_message("Choose a mode from the main menu.")

    def _recv_loop(self) -> None:
        try:
            while True:
                line = self.conn.recv_text()
                if line is None:
                    break
                self.inbox.put(line)
        except Exception as exc:
            self.inbox.put(f"INVALID | connection lost: {exc}")

    def send(self, line: str) -> None:
        try:
            self.conn.send_text(line)
        except OSError:
            self.running = False

    def push_message(self, text: str) -> None:
        self.messages.append((time.monotonic(), text))

    def parse_message(self, line: str) -> tuple[str, list[str]]:
        if " | " in line:
            parts = [part.strip() for part in line.split(" | ")]
            return parts[0].upper(), parts[1:]
        command, _, rest = line.partition(" ")
        return command.upper(), [rest.strip()] if rest.strip() else []

    def _reset_for_lobby(self) -> None:
        self.phase = "pregame"
        self.ready = False
        self.reorder_done = False
        self.turn_player = None
        self.turn_deadline = None
        self.ready_by_player.clear()
        self.pending_auto_done_reorder = False
        self.self_hand.clear()
        self.self_visible.clear()
        self.self_hidden_count = 3
        self.visible_by_player.clear()
        self.hand_counts.clear()
        self.hidden_counts.clear()
        self.pile.clear()
        self.draw_pile_size = 0
        self.finished.clear()
        self.game_over_text = None
        self.selected = None
        self.selected_at = 0.0
        self.last_card_click = (None, False, 0.0)
        self.play_animations.clear()
        self.draw_animations.clear()

    def _request_create_room(self, fill_with_bots: bool) -> None:
        self.fill_bots_enabled = fill_with_bots
        self.pending_room_join = True
        self.pending_join_code = ""
        self.join_error = None
        self.send(f"CREATE_ROOM | {1 if fill_with_bots else 0}")

    def _request_join_room(self, room_code: str) -> None:
        code = room_code.strip().upper()
        if not code:
            self.join_error = "Enter room code."
            return
        self.pending_room_join = True
        self.pending_join_code = code
        self.join_error = None
        self.send(f"ROOM | {code}")

    def _join_current_room(self) -> None:
        self.send(f"JOIN_GAME | {self.self_name}")
        self._reset_for_lobby()
        self.menu_screen = "lobby"

    def _maybe_auto_reorder_done(self) -> None:
        if (
            self.pending_auto_done_reorder
            and self.phase == "reorder"
            and len(self.self_hand) == 5
            and len(self.self_visible) == 3
            and not self.reorder_done
        ):
            self.send_reorder_done()
            self.pending_auto_done_reorder = False

    def _start_tutorial(self) -> None:
        if self.tutorial_thread and self.tutorial_thread.is_alive():
            return

        def run() -> None:
            rules_path = (Path(__file__).resolve().parent.parent / "docs" / "RULES.html").resolve()
            url = rules_path.as_uri()
            try:
                import webview  # type: ignore

                window = webview.create_window("Card Game Tutorial", url, width=980, height=740)
                webview.start(gui="qt")
                if window is not None:
                    return
            except Exception:
                pass
            webbrowser.open(url)

        self.tutorial_thread = threading.Thread(target=run, daemon=True)
        self.tutorial_thread.start()

    def process_network(self) -> None:
        while True:
            try:
                line = self.inbox.get_nowait()
            except queue.Empty:
                return

            log.trace(f'Recv: {line}')

            command, args = self.parse_message(line)

            if command == "ROOM" and args:
                self.room_code = args[0].strip().upper()
                if self.pending_room_join:
                    self.pending_room_join = False
                    self._join_current_room()
                elif self.phase == "menu":
                    self.menu_screen = "lobby"
                self.push_message(f"Room {self.room_code}")

            elif command == "LOBBY_FILL_BOTS" and args:
                self.fill_bots_enabled = args[0].strip() in {"1", "true", "TRUE", "on", "ON"}

            elif command == "READY_STATE" and len(args) >= 2:
                value = args[1].strip() in {"1", "true", "TRUE", "on", "ON"}
                self.ready_by_player[args[0]] = value
                if args[0] == self.self_name:
                    self.ready = value

            if command == "PLAYERS" and args:
                self.players = [name for name in args[0].split(",") if name]
                self._prune_removed_players()
                if self.phase == "menu" and self.self_name in self.players:
                    self.phase = "pregame"
                    self.menu_screen = "lobby"
                if self.phase == "pregame" and self.auto_ready_after_join and self.self_name in self.players and not self.ready:
                    self.send_ready()
                    self.auto_ready_after_join = False

            elif command == "START_GAME":
                self.phase = "reorder"
                self.reorder_done = False
                self.pending_auto_done_reorder = self.auto_done_reorder_once
                self.selected = None
                self.selected_at = 0.0
                self.last_card_click = (None, False, 0.0)
                self.game_over_text = None
                self.finished.clear()
                self.play_animations.clear()
                self.draw_animations.clear()
                self.push_message("Initial deal complete. Reorder your cards, then press Done.")

            elif command == "BEGIN":
                self.phase = "game"
                self.selected = None
                self.selected_at = 0.0
                self.last_card_click = (None, False, 0.0)
                self.game_over_text = None
                self.push_message("Game started.")

            elif command == "TURN_START" and args:
                self.turn_player = args[0]
                self.turn_deadline = time.monotonic() + 60.0
                self.selected = None
                self.selected_at = 0.0
                if self.turn_player == self.self_name:
                    self.push_message("Your turn.")
                else:
                    self.push_message(f"{self.turn_player} is playing.")

            elif command == "VISIBLE" and len(args) >= 2:
                name = args[0]
                cards = parse_cards(args[1])
                self.visible_by_player[name] = cards
                if name == self.self_name:
                    self.self_visible = cards
                    self._maybe_auto_reorder_done()

            elif command == "HAND_COUNT" and len(args) >= 2:
                self.hand_counts[args[0]] = int(args[1])

            elif command == "HIDDEN_COUNT" and len(args) >= 2:
                self.hidden_counts[args[0]] = int(args[1])
                if args[0] == self.self_name:
                    self.self_hidden_count = int(args[1])

            elif command == "HAND" and args:
                self.self_hand = parse_cards(args[0])
                self.self_hand = sort_hand(self.self_hand)
                self._maybe_auto_reorder_done()

            elif command == "PLAY" and len(args) >= 2:
                self._queue_play_animation(args[0], parse_cards(args[1]), hidden=False)
                self._apply_play(args[0], parse_cards(args[1]), hidden=False)

            elif command == "PLAY_HIDDEN" and len(args) >= 2:
                hidden_cards = parse_cards(args[1])
                self._queue_play_animation(args[0], hidden_cards, hidden=True)
                self._apply_play(args[0], hidden_cards, hidden=True)

            elif command == "DRAW_CARDS" and len(args) >= 2:
                self._queue_draw_animation(args[0], count=int(args[1]))

            elif command == "NEW_CARDS" and args:
                new_cards = parse_cards(args[0])
                self.self_hand.extend(new_cards)
                self.self_hand = sort_hand(self.self_hand)
                self.push_message(f"Drew {len(new_cards)} card(s).")

            elif command == "PILE_UPDATE" and len(args) >= 2:
                pass
            elif command == "DRAW_PILE_SIZE" and args:
                self.draw_pile_size = int(args[0])

            elif command == "DRAW_ALL" and args:
                self._queue_draw_animation(args[0], cards=list(self.pile))
                self._apply_draw_all(args[0])

            elif command == "PILE_CLEAR":
                self.pile.clear()

            elif command == "PLAYER_WIN" and len(args) >= 2:
                self.finished[args[0]] = int(args[1])
                self.push_message(f"{args[0]} finished #{args[1]}.")

            elif command == "GAME_OVER" and args:
                self.phase = "finished"
                self.game_over_text = f"Winner: {args[0]}"

            elif command == "PLAYER_QUIT" and args:
                self.bot_seats.add(args[0])
                self.push_message(f"{args[0]} is now bot-controlled.")

            elif command == "PLAYER_LEFT" and args:
                name = args[0]
                self.players = [player for player in self.players if player != name]
                self._prune_removed_players()
                self.push_message(f"{name} left before the game started.")

            elif command == "INVALID" and args:
                message = args[0]
                if self.pending_room_join:
                    self.pending_room_join = False
                    self.join_error = message
                self.push_message(message)

            elif command == "READY":
                self.ready = True

    def _prune_removed_players(self) -> None:
        allowed = set(self.players)
        allowed.add(self.self_name)
        self.visible_by_player = {
            name: cards for name, cards in self.visible_by_player.items() if name in allowed
        }
        self.hand_counts = {name: count for name, count in self.hand_counts.items() if name in allowed}
        self.hidden_counts = {name: count for name, count in self.hidden_counts.items() if name in allowed}
        self.bot_seats = {name for name in self.bot_seats if name in allowed}
        self.finished = {name: placement for name, placement in self.finished.items() if name in allowed}
        self.ready_by_player = {name: value for name, value in self.ready_by_player.items() if name in allowed}

    def _apply_play(self, player: str, cards: list[str], hidden: bool) -> None:
        for token in cards:
            self.pile.append(token)
        if player == self.self_name:
            if hidden:
                played_hand_tokens = self.selected_tokens()
                for token in played_hand_tokens:
                    if token in self.self_hand:
                        self.self_hand.remove(token)
                    elif token in self.self_visible:
                        self.self_visible.remove(token)
                self.self_hidden_count = max(0, self.self_hidden_count - 1)

            else:
                for token in cards:
                    if token in self.self_hand:
                        self.self_hand.remove(token)

                    elif token in self.self_visible:
                        self.self_visible.remove(token)

            self.self_hand = sort_hand(self.self_hand)
            self.selected = None

        else:
            if hidden:
                self.hidden_counts[player] = max(
                    0, self.hidden_counts.get(player, 0) - 1
                )

            else:
                visible = self.visible_by_player.get(player, [])
                remaining_visible = visible[:]
                removed_from_visible = 0

                for token in cards:
                    if token in remaining_visible:
                        remaining_visible.remove(token)
                        removed_from_visible += 1

                self.visible_by_player[player] = remaining_visible
                self.hand_counts[player] = max(
                    0,
                    self.hand_counts.get(player, 0) - (len(cards) - removed_from_visible),
                )

    def _apply_draw_all(self, player: str) -> None:
        if player == self.self_name:
            self.self_hand.extend(self.pile)
            self.self_hand = sort_hand(self.self_hand)
        else:
            self.hand_counts[player] = self.hand_counts.get(player, 0) + len(self.pile)

        self.pile.clear()

    def _player_slot_center(self, player: str) -> tuple[float, float]:
        slot = self.slot_layout.get(player)
        if not slot:
            width, height = self.window.get_size()
            layout = self.layout(width, height)
            slot = layout.get(player)
        if not slot:
            width, height = self.window.get_size()
            return width * 0.5, height * 0.5
        return slot["panel"]

    def _queue_play_animation(self, player: str, cards: list[str], hidden: bool) -> None:
        if not cards:
            return
        width, height = self.window.get_size()
        end = (width * 0.5 + 20, height * 0.5)
        start = self._player_slot_center(player)
        if hidden:
            start = (start[0], start[1] - 24)
        now = time.monotonic()
        stagger = 0.04
        for index, token in enumerate(cards):
            self.play_animations.append(
                {
                    "token": token,
                    "player": player,
                    "start": (start[0] + index * 10, start[1] - index * 2),
                    "end": (end[0] + index * 6, end[1] - index * 4),
                    "start_time": now + index * stagger,
                    "duration": PLAY_ANIM_DURATION,
                    "hidden": hidden,
                }
            )

    def _queue_draw_animation(self, player: str, count: int = 0, cards: list[str] | None = None) -> None:
        if cards is not None and not cards:
            return
        if cards is None and count <= 0:
            return
        width, height = self.window.get_size()
        start = (width * 0.5 - 155, height * 0.5)
        end = self._player_slot_center(player)
        now = time.monotonic()
        draw_cards = cards if cards is not None else [None] * count
        for index, token in enumerate(draw_cards):
            self.draw_animations.append(
                {
                    "start": (start[0] - index * 6, start[1] - index * 3),
                    "end": (end[0] + index * 8, end[1] - index * 4),
                    "start_time": now + index * 0.03,
                    "duration": DRAW_ANIM_DURATION,
                    "token": token,
                }
            )

    def current_turn_is_self(self) -> bool:
        return self.turn_player == self.self_name and self.phase == "game"

    def sort_hand_local(self) -> None:
        self.self_hand = sort_hand(self.self_hand)
        self.push_message("Hand sorted.")
        self.emit_reorder()

    def emit_reorder(self) -> None:
        if len(self.self_hand) != 5 or len(self.self_visible) != 3:
            return
        self.send(f"REORDER | {format_cards(self.self_hand)} | {format_cards(self.self_visible)}")

    def send_ready(self) -> None:
        self.send("READY")
        self.ready = True
        self.push_message("Ready sent.")

    def send_reorder_done(self) -> None:
        if len(self.self_hand) != 5 or len(self.self_visible) != 3:
            self.push_message("Reorder must end with exactly 5 hand and 3 visible cards.")
            return
        self.send("REORDER_DONE")
        self.reorder_done = True
        self.push_message("Reorder locked.")

    def send_play(self) -> None:
        if not self.selected:
            return

        if self.selected.get("kind") == "hidden":
            self.send(f"PLAY_HIDDEN {self.selected['index']}")
        elif self.selected.get("kind") == "card":
            tokens = self.selected_tokens()
            if tokens:
                self.send(f"PLAY {format_cards(tokens)}")
        else:
            tokens = self.selected_tokens()
            if tokens:
                self.send(f"PLAY {format_cards(tokens)}")

    def send_play_tokens(self, tokens: list[str]) -> None:
        if tokens:
            self.send(f"PLAY {format_cards(tokens)}")

    def send_draw(self) -> None:
        self.send("DRAW")

    def send_fill_bots(self, enabled: bool) -> None:
        self.fill_bots_enabled = enabled
        self.send(f"SET_FILL_BOTS | {1 if enabled else 0}")

    def _selection_from_card(self, token: str) -> dict[str, object] | None:
        if token in self.self_hand or token in self.self_visible:
            return {"kind": "card", "tokens": [token]}

        return None

    def _self_hand_rank(self) -> str | None:
        if not self.self_hand:
            return None
        ranks = {card_rank(token) for token in self.self_hand}
        if len(ranks) != 1:
            return None
        return next(iter(ranks))

    def _same_rank_tokens(self, token: str) -> list[str]:
        rank = card_rank(token)
        matching_hand = [card for card in self.self_hand if card_rank(card) == rank]

        if self.self_hand:
            if not matching_hand:
                return []
            if self._self_hand_rank() == rank:
                matching_visible = [
                    card for card in self.self_visible if card_rank(card) == rank
                ]
                return matching_hand + matching_visible
            return matching_hand

        return [card for card in self.self_visible if card_rank(card) == rank]

    def _toggle_card_focus(self, token: str) -> None:
        current = self.selected_tokens()
        if token in current:
            updated = [card for card in current if card != token]
        else:
            updated = current + [token]
        if updated:
            self.selected = {"kind": "card", "tokens": updated}
            self.selected_at = time.monotonic()
        else:
            self.selected = None
            self.selected_at = 0.0

    def _hidden_index_from_point(self, pos: tuple[int, int]) -> int:
        if not self.hidden_rects:
            return 0
        rects = self.hidden_rects
        best = min(
            range(len(rects)),
            key=lambda index: abs(rects[index].centerx - pos[0])
            + abs(rects[index].centery - pos[1]),
        )
        return best

    def _handle_menu_mouse_down(self, pos: tuple[int, int]) -> None:
        if self.menu_screen == "main":
            if self._click_button("menu_play", pos):
                self.menu_screen = "play"
                return
            if self._click_button("menu_bot", pos):
                self.auto_ready_after_join = True
                self.auto_done_reorder_once = False
                self._request_create_room(True)
                return
            if self._click_button("menu_tutorial", pos):
                self._start_tutorial()
                return
            if self._click_button("menu_quit", pos):
                self.running = False
                return
            return

        if self.menu_screen == "play":
            if self._click_button("play_join", pos):
                self.menu_screen = "join_room"
                self.join_error = None
                self.join_input.value = ""
                return
            if self._click_button("play_create", pos):
                self.auto_ready_after_join = False
                self.auto_done_reorder_once = False
                self._request_create_room(True)
                return
            if self._click_button("play_back", pos):
                self.menu_screen = "main"
                return
            return

        if self.menu_screen == "join_room":
            if self._click_button("join_confirm", pos):
                self.auto_ready_after_join = False
                self.auto_done_reorder_once = False
                self._request_join_room(self.join_input.value)
                return
            if self._click_button("join_cancel", pos):
                self.menu_screen = "play"
                self.join_error = None
                return

    def handle_keydown(self, event: pygame.event.Event) -> None:
        if self.phase == "menu" and self.menu_screen == "join_room":
            self.join_input.update([event])
            self.join_input.value = self.join_input.value.upper()[:8]
            if event.key == pygame.K_RETURN:
                self.auto_ready_after_join = False
                self.auto_done_reorder_once = False
                self._request_join_room(self.join_input.value)
            return

        if event.key == pygame.K_ESCAPE:
            if self.phase == "menu":
                if self.menu_screen == "join_room":
                    self.menu_screen = "play"
                    self.join_error = None
                    return
                if self.menu_screen == "play":
                    self.menu_screen = "main"
                    return
            self.running = False

    def handle_mouse_down(self, pos: tuple[int, int]) -> None:
        if self.phase == "menu":
            self._handle_menu_mouse_down(pos)
            return

        if self.phase == "reorder" and self.reorder_done:
            return
        if self.phase == "pregame" and self._click_button("fill_bots", pos):
            self.send_fill_bots(not self.fill_bots_enabled)
            return
        if self._click_button("ready", pos):
            self.send_ready()
            return
        if self._click_button("done", pos):
            self.send_reorder_done()
            return
        if self._click_button("play", pos):
            self.send_play()
            return
        if self._click_button("draw", pos):
            self.send_draw()
            return

        allowed_tokens = set(self.self_hand + self.self_visible)
        for token, rect in self.card_rects.items():
            if token not in allowed_tokens or not rect.collidepoint(pos):
                continue
            if self.phase == "reorder":
                self.dragging = {
                    "type": "token",
                    "token": token,
                    "start": pos,
                    "origin": rect.center,
                }
                return
            if self.current_turn_is_self():
                self.dragging = {
                    "type": "token",
                    "token": token,
                    "start": pos,
                    "origin": rect.center,
                }
                return

        if (
            self.phase != "reorder"
            and self.current_turn_is_self()
            and not self.self_hand
            and not self.self_visible
            and self.self_hidden_count > 0
        ):
            hidden_index = self._hidden_index_from_point(pos)
            self.selected = {"kind": "hidden", "index": hidden_index}
            self.selected_at = time.monotonic()
            self.push_message(f"Selected hidden card {hidden_index + 1}.")
            return

        if (
            self.phase != "reorder"
            and self.current_turn_is_self()
            and not self.self_visible
            and self.self_hidden_count > 0
            and self.self_hand
            and self._self_hand_rank() is not None
        ):
            hidden_index = self._hidden_index_from_point(pos)
            self.selected = {
                "kind": "hidden",
                "index": hidden_index,
                "tokens": list(self.self_hand),
            }
            self.selected_at = time.monotonic()
            self.push_message(f"Selected hidden card {hidden_index + 1} with matching hand.")
            return

    def handle_mouse_up(self, pos: tuple[int, int]) -> None:
        if self.phase == "reorder" and self.reorder_done:
            self.dragging = None
            return
        if self.dragging and self.dragging.get("type") == "token":
            token = self.dragging["token"]
            moved = math.hypot(pos[0] - self.dragging["start"][0], pos[1] - self.dragging["start"][1])
            if moved < 8:
                if self.phase == "reorder":
                    self._move_token_between_zones(token, to_visible=token in self.self_hand)
                    self.emit_reorder()
                    self.selected = None
                    self.selected_at = 0.0
                elif self.current_turn_is_self():
                    now = time.monotonic()
                    last_token, _last_selected, last_time = self.last_card_click
                    is_double_click = last_token == token and (now - last_time) <= 0.35
                    
                    if is_double_click:
                        tokens = self._same_rank_tokens(token)
                        if tokens:
                            self.selected = {"kind": "card", "tokens": tokens}
                            self.selected_at = now
                            self.send_play_tokens(tokens)
                        else:
                            self._toggle_card_focus(token)
                    else:
                        self._toggle_card_focus(token)
                    
                    self.last_card_click = (token, token in self.selected_tokens(), now)
            else:
                if self.phase == "reorder":
                    if self._drop_in_visible_zone(pos):
                        self._move_token_between_zones(token, to_visible=True)
                        self.emit_reorder()
                    elif self._drop_in_hand_zone(pos):
                        self._move_token_between_zones(token, to_visible=False)
                        self.emit_reorder()
                elif self.current_turn_is_self() and token in self.self_hand:
                    if self._drop_in_play_zone(pos):
                        tokens = self.selected_tokens()
                        if not tokens:
                            tokens = [token]
                            self.selected = {"kind": "card", "tokens": tokens}
                            self.selected_at = time.monotonic()
                        self.send_play_tokens(tokens)
                    elif self._drop_in_hand_zone(pos):
                        self.self_hand = self._reorder_token_in_list(self.self_hand, token, pos)
                        self.emit_reorder()
                    elif self._drop_in_visible_zone(pos):
                        self.self_hand = self._reorder_token_in_list(self.self_hand, token, pos)
                        self.emit_reorder()
            self.dragging = None
            return

        if self.phase != "reorder" and self.current_turn_is_self():
            for token, rect in self.card_rects.items():
                if rect.collidepoint(pos):
                    was_selected = token in self.selected_tokens()
                    self._toggle_card_focus(token)
                    self.last_card_click = (token, was_selected, time.monotonic())
                    if self.selected and self.selected.get("kind") == "card":
                        token_label_text = token_label(token)
                        self.push_message(f"Focused {token_label_text}.")
                    return
            if (
                not self.self_hand
                and not self.self_visible
                and self.self_hidden_count > 0
            ):
                hidden_index = self._hidden_index_from_point(pos)
                self.selected = {"kind": "hidden", "index": hidden_index}
                self.selected_at = time.monotonic()
                self.push_message(f"Selected hidden card {hidden_index + 1}.")

    def handle_mouse_motion(self, pos: tuple[int, int]) -> None:
        if self.dragging:
            self.dragging["current"] = pos

    def _move_token_between_zones(self, token: str, to_visible: bool) -> None:
        if token in self.self_hand:
            self.self_hand.remove(token)
        if token in self.self_visible:
            self.self_visible.remove(token)
        if to_visible:
            self.self_visible.append(token)
        else:
            self.self_hand.append(token)
            self.self_hand = sort_hand(self.self_hand)

    def _reorder_token_in_list(
        self, tokens: list[str], token: str, pos: tuple[int, int]
    ) -> list[str]:
        ordered = tokens[:]
        if token not in ordered:
            return ordered
        ordered.remove(token)
        index = 0
        for other in ordered:
            rect = self.card_rects.get(other)
            if rect and pos[0] > rect.centerx:
                index += 1
        ordered.insert(index, token)
        return ordered

    def _click_button(self, name: str, pos: tuple[int, int]) -> bool:
        rect = self.buttons.get(name)
        return bool(rect and rect.collidepoint(pos))

    def _drop_in_hand_zone(self, pos: tuple[int, int]) -> bool:
        rect = self.buttons.get("hand_zone")
        return bool(rect and rect.collidepoint(pos))

    def _drop_in_visible_zone(self, pos: tuple[int, int]) -> bool:
        rect = self.buttons.get("visible_zone")
        return bool(rect and rect.collidepoint(pos))

    def _drop_in_play_zone(self, pos: tuple[int, int]) -> bool:
        rect = self.buttons.get("play")
        return bool(rect and rect.collidepoint(pos))

    def layout(self, width: int, height: int) -> dict[str, dict[str, object]]:
        names = self.players[:] if self.players else [self.self_name]

        if self.self_name in names:
            names.remove(self.self_name)

        names.insert(0, self.self_name)

        slots: list[dict[str, object]] = []
        base = player_layout_shell(len(names), width, height)

        for index, name in enumerate(names):
            if index < len(base):
                panel_center, visible_center, hand_center, angle, spread_x, spread_y = base[index]

                slots.append(
                    {
                        "name": name,
                        "panel": panel_center,
                        "visible": visible_center,
                        "hand": hand_center,
                        "angle": angle,
                        "spread": (spread_x, spread_y),
                    }
                )

            else:
                break

        return {slot["name"]: slot for slot in slots}

    def ensure_motion(
        self, token: str, target: tuple[float, float]
    ) -> tuple[float, float]:
        now = time.monotonic()
        motion = self.card_motion.get(token)
        last = self.last_positions.get(token)
        if motion is None:
            start = last or target
            duration = SURFACE_DURATION(start, target)
            motion = Tween(start=start, end=target, start_time=now, duration=duration)
            self.card_motion[token] = motion
        elif (abs(motion.end[0] - target[0]) > 1.0) or (
            abs(motion.end[1] - target[1]) > 1.0
        ):
            current = motion.sample(now)
            duration = SURFACE_DURATION(current, target)
            motion = Tween(start=current, end=target, start_time=now, duration=duration)
            self.card_motion[token] = motion
        value = motion.sample(now)
        if motion.finished(now):
            self.last_positions[token] = motion.end
            self.card_motion[token] = Tween(
                start=motion.end, end=motion.end, start_time=now, duration=0.0
            )
        return value

    def draw_background(self, surface: pygame.Surface) -> None:
        width, height = surface.get_size()
        surface.fill(TABLE_GREEN)
        for x in range(0, width, 32):
            pygame.draw.line(surface, (30, 92, 61), (x, 0), (x, height), 1)
        for y in range(0, height, 32):
            pygame.draw.line(surface, (30, 92, 61), (0, y), (width, y), 1)
        glow = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.circle(
            glow, (70, 180, 110, 60), (width // 2, height // 2), 420
        )
        pygame.draw.circle(
            glow, (255, 255, 255, 25), (width // 2, height // 2), 180
        )
        surface.blit(glow, (0, 0))

    def draw_panel(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        title: str,
        subtitle: str,
        active: bool,
    ) -> None:
        shadow = rect.copy()
        shadow.x += 4
        shadow.y += 4
        rounded_panel(surface, shadow, (0, 0, 0), 18, 90)
        rounded_panel(surface, rect, PANEL_SOFT if active else PANEL, 18, 240)
        pygame.draw.rect(
            surface, GOLD if active else (90, 105, 104), rect, 2, border_radius=18
        )
        draw_text(surface, self.body_font, title, (rect.x + 16, rect.y + 10), WHITE)
        if subtitle:
            draw_text(
                surface, self.small_font, subtitle, (rect.x + 16, rect.y + 36), MUTED
            )

    def draw_player_zone(
        self, surface: pygame.Surface, name: str, slot: dict[str, object], width: int, height: int
    ) -> None:
        is_self = name == self.self_name
        visible = (
            self.visible_by_player.get(name, []) if not is_self else self.self_visible
        )

        hidden_count = (
            self.hidden_counts.get(name, 3) if not is_self else self.self_hidden_count
        )

        hand_count = (
            self.hand_counts.get(name, len(self.self_hand))
            if not is_self
            else len(self.self_hand)
        )

        hand = self.self_hand if is_self else []

        panel_center = slot["panel"]
        visible_center = slot["visible"]
        hand_center = slot["hand"]

        angle = int(slot["angle"])
        spread_x, spread_y = slot["spread"]

        label = name if name != self.self_name else f"{name} (you)"

        if name in self.bot_seats:
            label += " [bot]"

        panel_rect = pygame.Rect(0, 0, 280, 86)
        panel_rect.center = (int(panel_center[0]), int(panel_center[1]))

        self.draw_panel(
            surface,
            panel_rect,
            label,
            f"Hand {hand_count}  Hidden {hidden_count}",
            self.turn_player == name and self.phase == "game",
        )

        # Hidden stack.
        hidden_show = min(hidden_count, 3)
        hidden_scale = 1.0 if is_self else 0.84
        hidden_size = (
            int(BACK_CARD_SIZE[0] * hidden_scale),
            int(BACK_CARD_SIZE[1] * hidden_scale),
        )

        hidden_spacing = max(24, int(hidden_size[0] * 0.58))
        hidden_offset = (hidden_show - 1) / 2.0
        hidden_draws: list[tuple[float, float, pygame.Surface, pygame.Rect]] = []

        for index in range(hidden_show):
            offset_index = (index - hidden_offset) * hidden_spacing
            offset_x = spread_x * offset_index
            offset_y = spread_y * offset_index

            center = (
                visible_center[0] + offset_x,
                visible_center[1] + offset_y,
            )

            rect = pygame.Rect(0, 0, *hidden_size).move(
                center[0] - hidden_size[0] // 2, center[1] - hidden_size[1] // 2
            )

            if is_self:
                self.hidden_rects.append(rect)

            image = back_surface(*hidden_size)

            if not is_self and angle:
                image = pygame.transform.rotozoom(image, angle, 1.0)
                rect = image.get_rect(center=center)

            hidden_draws.append((rect.centerx, rect.centery, image, rect))

        for _cx, _cy, image, rect in sorted(hidden_draws, key=lambda item: (item[0], item[1])):
            surface.blit(image, rect.topleft)

        if is_self and self.selected and self.selected.get("kind") == "hidden" and self.hidden_rects:
            hidden_focus = self.hidden_rects[0].unionall(self.hidden_rects)
            pygame.draw.rect(surface, GOLD, hidden_focus.inflate(10, 10), 3, border_radius=14)

        # Visible cards.
        if visible:
            if is_self:
                spacing = 64 if len(visible) <= 4 else 58 if len(visible) <= 7 else 50

            else:
                spacing = 48

            visible_offset = (len(visible) - 1) / 2.0
            visible_draws: list[tuple[float, float, pygame.Surface, pygame.Rect, str]] = []

            for index, token in enumerate(visible):
                face = card_face(token)
                target_center = (
                    visible_center[0] + spread_x * (index - visible_offset) * spacing,
                    visible_center[1] + spread_y * (index - visible_offset) * spacing,
                )

                center = self.ensure_motion(token, target_center)
                base_size = PLAY_CARD_SIZE if is_self else SMALL_CARD_SIZE

                image = scaled_face_surface(face, *base_size)

                if angle:
                    image = pygame.transform.rotozoom(image, angle, 1.0)

                rect = image.get_rect(center=center)

                if self.dragging and self.dragging.get("token") == token:
                    current = self.dragging.get("current", self.dragging["start"])
                    rect.x += int(current[0] - self.dragging["start"][0])
                    rect.y += int(current[1] - self.dragging["start"][1])

                if token in self.selected_tokens():
                    progress = min(1.0, (time.monotonic() - self.selected_at) / 0.14)
                    lift = int(18 * (1 - (1 - progress) * (1 - progress)))
                    rect.y -= lift

                visible_draws.append((rect.centerx, rect.centery, image, rect, token))

                self.card_rects[token] = rect
                self.last_positions[token] = rect.center

            for _cx, _cy, image, rect, token in sorted(visible_draws, key=lambda item: (item[0], item[1])):
                surface.blit(image, rect)

        # Hand cards.
        if is_self:
            self.buttons["hand_zone"] = pygame.Rect(0, 0, 460, 160)
            self.buttons["hand_zone"].center = (int(hand_center[0]), int(hand_center[1]))
            self.buttons["visible_zone"] = pygame.Rect(0, 0, 460, 140)
            self.buttons["visible_zone"].center = (int(visible_center[0]), int(visible_center[1]))

            if hand:
                if len(hand) <= 5:
                    spacing = 74
                elif len(hand) <= 8:
                    spacing = 68
                elif len(hand) <= 11:
                    spacing = 60
                else:
                    spacing = 52

                hand_offset = (len(hand) - 1) / 2.0
                hand_scale = clamp(1.0 - max(0, len(hand) - 8) * 0.04, 0.72, 1.0)

                hand_size = (
                    int(PLAY_CARD_SIZE[0] * hand_scale),
                    int(PLAY_CARD_SIZE[1] * hand_scale),
                )

                hand_draws: list[tuple[float, float, pygame.Surface, pygame.Rect, str]] = []

                for index, token in enumerate(hand):
                    face = card_face(token)
                    target_center = (
                        hand_center[0] + spread_x * (index - hand_offset) * spacing,
                        hand_center[1] + spread_y * (index - hand_offset) * spacing,
                    )

                    center = self.ensure_motion(token, target_center)

                    rect = pygame.Rect(0, 0, *hand_size)
                    rect.center = center

                    if token in self.selected_tokens():
                        progress = min(1.0, (time.monotonic() - self.selected_at) / 0.14)
                        lift = int(20 * (1 - (1 - progress) * (1 - progress)))
                        rect.y -= lift

                    if self.dragging and self.dragging.get("token") == token:
                        current = self.dragging.get("current", self.dragging["start"])
                        rect.x += int(current[0] - self.dragging["start"][0])
                        rect.y += int(current[1] - self.dragging["start"][1])

                    hand_draws.append((rect.centerx, rect.centery, scaled_face_surface(face, *hand_size), rect, token))

                    if token == self.selected_card_token():
                        pygame.draw.rect(
                            surface, GOLD, rect.inflate(10, 10), 3, border_radius=14
                        )

                    self.card_rects[token] = rect
                    self.last_positions[token] = rect.center

                for _cx, _cy, image, rect, _token in sorted(hand_draws, key=lambda item: (item[0], item[1])):
                    surface.blit(image, rect)

        elif hand_count:
            back_show = min(hand_count, 5)
            back_scale = 0.80
            back_size = (
                int(BACK_CARD_SIZE[0] * back_scale),
                int(BACK_CARD_SIZE[1] * back_scale),
            )

            back_spacing = max(22, int(back_size[0] * 0.56))
            back_offset = (back_show - 1) / 2.0
            back_draws: list[tuple[float, float, pygame.Surface, pygame.Rect]] = []

            for index in range(back_show):
                offset_index = (index - back_offset) * back_spacing
                center = (
                    hand_center[0] + spread_x * offset_index,
                    hand_center[1] + spread_y * offset_index,
                )

                rect = pygame.Rect(0, 0, *back_size).move(
                    center[0] - back_size[0] // 2, center[1] - back_size[1] // 2
                )

                image = back_surface(*back_size)

                if angle:
                    image = pygame.transform.rotozoom(image, angle, 1.0)
                    rect = image.get_rect(center=center)
                back_draws.append((rect.centerx, rect.centery, image, rect))

            for _cx, _cy, image, rect in sorted(back_draws, key=lambda item: (item[0], item[1])):
                surface.blit(image, rect.topleft)

    def selected_tokens(self) -> list[str]:
        if not self.selected:
            return []
        if self.selected.get("kind") == "card":
            tokens = self.selected.get("tokens")
            if isinstance(tokens, list):
                return [token for token in tokens if token]
            token = self.selected.get("token")
            return [token] if token else []
        if self.selected.get("kind") == "hidden":
            tokens = self.selected.get("tokens")
            if isinstance(tokens, list):
                return [token for token in tokens if token]
        return []

    def selected_card_token(self) -> str | None:
        tokens = self.selected_tokens()
        return tokens[0] if tokens else None

    def draw_center_area(self, surface: pygame.Surface) -> None:
        width, height = surface.get_size()
        center_x = width * 0.5
        center_y = height * 0.5
        draw_rect = pygame.Rect(0, 0, *BACK_CARD_SIZE)
        draw_rect.center = (int(center_x - 155), int(center_y))
        play_rect = pygame.Rect(0, 0, *PLAY_CARD_SIZE)
        play_rect.center = (int(center_x + 20 + 175), int(center_y))

        self.buttons["draw"] = draw_rect.inflate(28, 28)
        self.buttons["play"] = play_rect.inflate(28, 28)

        stack_count = min(6, self.draw_pile_size)
        if stack_count:
            for index in range(stack_count):
                offset = min(10, index * 3)
                rect = draw_rect.copy()
                rect.x -= offset
                rect.y -= offset
                surface.blit(back_surface(*BACK_CARD_SIZE), rect)

        else:
            pygame.draw.rect(
                surface, (72, 107, 103), draw_rect.inflate(8, 8), 2, border_radius=14
            )

        if self.pile:
            pile_show = self.pile[-min(6, len(self.pile)) :]
            for index, token in enumerate(pile_show):
                face = card_face(token)
                target_center = (
                    play_rect.centerx + index * 3 - 175,
                    play_rect.centery - index * 3,
                )

                center = self.ensure_motion(token, target_center)
                image = scaled_face_surface(face, *PLAY_CARD_SIZE)
                rect = image.get_rect(center=center)
                surface.blit(image, rect)
                self.card_rects[token] = rect
                self.last_positions[token] = rect.center
        else:
            surface.blit(back_surface(*PLAY_CARD_SIZE), (play_rect.x-175, play_rect.y))

        pygame.draw.rect(surface, (72, 107, 103), play_rect.inflate(8, 8), 2, border_radius=14)

        self._draw_transient_animations(surface, play_rect, draw_rect)

        draw_text(surface, self.title_font, "DRAW", (draw_rect.centerx, draw_rect.centery - 18), WHITE, center=True)
        draw_text(surface, self.small_font, f"cards: {self.draw_pile_size}", (draw_rect.centerx, draw_rect.centery + 28), WHITE, center=True)
        draw_text(surface, self.title_font, "PLAY", (play_rect.centerx, play_rect.centery - 18), WHITE, center=True)
        draw_text(surface, self.small_font, f"cards: {len(self.pile)}", (play_rect.centerx, play_rect.centery + 28), WHITE, center=True)

        if self.current_turn_is_self():
            pygame.draw.rect(surface, BLUE, self.buttons["draw"], 2, border_radius=18)
            pygame.draw.rect(surface, GOLD if self.selected else BLUE, self.buttons["play"], 2, border_radius=18)

        if self.selected and self.current_turn_is_self():
            pygame.draw.rect(surface, GOLD, play_rect.inflate(14, 14), 2, border_radius=16)

    def _draw_transient_animations(
        self, surface: pygame.Surface, play_rect: pygame.Rect, draw_rect: pygame.Rect
    ) -> None:
        now = time.monotonic()
        self.play_animations = [
            item for item in self.play_animations if now - float(item["start_time"]) <= float(item["duration"]) + 0.08
        ]
        self.draw_animations = [
            item for item in self.draw_animations if now - float(item["start_time"]) <= float(item["duration"]) + 0.08
        ]

        for item in self.play_animations:
            elapsed = now - float(item["start_time"])
            if elapsed < 0:
                continue
            duration = max(0.001, float(item["duration"]))
            progress = clamp(elapsed / duration, 0.0, 1.0)
            start_x, start_y = item["start"]
            end_x, end_y = item["end"]
            x = start_x + (end_x - start_x) * progress
            y = start_y + (end_y - start_y) * progress
            token = str(item["token"])
            image = scaled_face_surface(card_face(token), *PLAY_CARD_SIZE)
            if item.get("hidden") and elapsed < 0.18 and card_rank(token) == "T":
                reveal = pygame.Surface(image.get_size(), pygame.SRCALPHA)
                pygame.draw.rect(reveal, (255, 236, 160, 70), reveal.get_rect(), border_radius=14)
                image.blit(reveal, (0, 0))
            rect = image.get_rect(center=(x, y))
            surface.blit(image, rect)

        for item in self.draw_animations:
            elapsed = now - float(item["start_time"])
            if elapsed < 0:
                continue
            duration = max(0.001, float(item["duration"]))
            progress = clamp(elapsed / duration, 0.0, 1.0)
            start_x, start_y = item["start"]
            end_x, end_y = item["end"]
            x = start_x + (end_x - start_x) * progress
            y = start_y + (end_y - start_y) * progress
            scale = 0.92 + 0.08 * (1 - progress)
            token = item.get("token")
            if token:
                image = pygame.transform.smoothscale(
                    face_surface(card_face(str(token))),
                    (int(PLAY_CARD_SIZE[0] * scale), int(PLAY_CARD_SIZE[1] * scale)),
                )
            else:
                image = pygame.transform.smoothscale(
                    back_surface(*BACK_CARD_SIZE),
                    (int(BACK_CARD_SIZE[0] * scale), int(BACK_CARD_SIZE[1] * scale)),
                )
            rect = image.get_rect(center=(x, y))
            surface.blit(image, rect)

    def draw_game_over(self, surface: pygame.Surface) -> None:
        width, height = surface.get_size()
        if self.phase != "finished" and not self.game_over_text:
            return
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((8, 18, 14, 150))
        surface.blit(overlay, (0, 0))

        rankings = sorted(self.finished.items(), key=lambda item: item[1])
        winner_name = rankings[0][0] if rankings else ""

        banner_height = 140 + max(0, len(rankings) - 1) * 32
        banner = pygame.Rect(0, 0, 460, banner_height)
        banner.center = (width // 2, height // 2 - 30)
        rounded_panel(surface, banner, PANEL, 22, 240)
        pygame.draw.rect(surface, GOLD, banner, 3, border_radius=22)
        draw_text(
            surface,
            self.big_font,
            f"Winner: {winner_name}",
            (banner.centerx, banner.y + 40),
            WHITE,
            center=True,
        )
        for offset, (name, place) in enumerate(rankings):
            if offset == 0:
                continue
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(place, "th")
            draw_text(
                surface,
                self.body_font,
                f"{place}{suffix}: {name}",
                (banner.centerx, banner.y + 80 + (offset - 1) * 32),
                MUTED,
                center=True,
            )

    def draw_top_bars(self, surface: pygame.Surface) -> None:
        width, _height = surface.get_size()
        turn_label = self.turn_player if self.turn_player else "Waiting"
        turn_text = (
            "Your turn" if self.turn_player == self.self_name else f"Turn: {turn_label}"
        )
        turn_rect = pygame.Rect(20, 20, 260, 54)
        rounded_panel(surface, turn_rect, PANEL, 18, 230)
        pygame.draw.rect(
            surface,
            GOLD if self.turn_player == self.self_name else BLUE,
            turn_rect,
            2,
            border_radius=18,
        )
        draw_text(
            surface, self.title_font, turn_text, turn_rect.center, WHITE, center=True
        )

        remaining = 60
        if self.turn_deadline is not None:
            remaining = max(0, int(math.ceil(self.turn_deadline - time.monotonic())))
        timer_rect = pygame.Rect(width - 160, 20, 140, 54)
        rounded_panel(surface, timer_rect, PANEL, 18, 230)
        pygame.draw.rect(
            surface, RED if remaining <= 10 else GOLD, timer_rect, 2, border_radius=18
        )
        draw_text(
            surface,
            self.title_font,
            str(remaining),
            timer_rect.center,
            WHITE,
            center=True,
        )

        if self.room_code:
            room_rect = pygame.Rect(0, 0, 220, 44)
            room_rect.center = (width // 2, 86)
            rounded_panel(surface, room_rect, PANEL, 14, 230)
            pygame.draw.rect(surface, GOLD, room_rect, 2, border_radius=14)
            draw_text(surface, self.small_font, f"Room {self.room_code}", room_rect.center, MUTED, center=True)

    def draw_notifications(self, surface: pygame.Surface) -> None:
        now = time.monotonic()
        _width, height = surface.get_size()
        y = height - 118
        visible_messages = [item for item in self.messages if (now - item[0]) <= 3.2]
        self.messages = deque(visible_messages, maxlen=7)
        for stamp, message in visible_messages[-4:]:
            alpha = int(clamp(255 - (now - stamp) * 180, 50, 255))
            text_surface = self.body_font.render(message, True, WHITE)
            box = text_surface.get_rect()
            box.x = 22
            box.y = y
            panel = pygame.Surface((box.width + 24, box.height + 14), pygame.SRCALPHA)
            pygame.draw.rect(
                panel, (20, 24, 25, alpha), panel.get_rect(), border_radius=14
            )
            panel.blit(text_surface, (12, 7))
            surface.blit(panel, (18, y - 2))
            y -= 30

    def _menu_button(
        self,
        surface: pygame.Surface,
        key: str,
        label: str,
        center: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        rect = pygame.Rect(0, 0, *MENU_BUTTON_SIZE)
        rect.center = center
        self.buttons[key] = rect
        rounded_panel(surface, rect, color, 18, 242)
        pygame.draw.rect(surface, GOLD, rect, 2, border_radius=18)
        draw_text(surface, self.title_font, label, rect.center, WHITE, center=True)

    def draw_menu(self, surface: pygame.Surface) -> None:
        width, height = surface.get_size()
        self.buttons.clear()

        title_rect = pygame.Rect(0, 0, 580, 100)
        title_rect.center = (width // 2, 130)
        rounded_panel(surface, title_rect, PANEL, 22, 230)
        pygame.draw.rect(surface, GOLD, title_rect, 2, border_radius=22)
        draw_text(surface, self.big_font, "Card Game", title_rect.center, WHITE, center=True)

        if self.menu_screen == "main":
            start_y = 270
            spacing = 94
            self._menu_button(surface, "menu_play", "Play", (width // 2, start_y), BLUE)
            self._menu_button(surface, "menu_bot", "Play Vs. Computer", (width // 2, start_y + spacing), PANEL_SOFT)
            self._menu_button(surface, "menu_tutorial", "Tutorial", (width // 2, start_y + spacing * 2), PANEL_SOFT)
            self._menu_button(surface, "menu_quit", "Quit", (width // 2, start_y + spacing * 3), RED)
            return

        if self.menu_screen == "play":
            subtitle = "Create or join a room"
            draw_text(surface, self.body_font, subtitle, (width // 2, 200), MUTED, center=True)
            self._menu_button(surface, "play_join", "Join Room", (width // 2, 320), BLUE)
            self._menu_button(surface, "play_create", "Create Room", (width // 2, 414), PANEL_SOFT)
            self._menu_button(surface, "play_back", "Back", (width // 2, 508), PANEL)
            return

        if self.menu_screen == "join_room":
            panel = pygame.Rect(0, 0, 620, 320)
            panel.center = (width // 2, height // 2)
            rounded_panel(surface, panel, PANEL, 22, 238)
            pygame.draw.rect(surface, GOLD, panel, 2, border_radius=22)

            draw_text(surface, self.title_font, "Join Room", (panel.centerx, panel.y + 44), WHITE, center=True)
            draw_text(surface, self.body_font, "Enter lobby code", (panel.centerx, panel.y + 88), MUTED, center=True)

            input_rect = pygame.Rect(panel.x + 80, panel.y + 126, panel.width - 160, 62)
            rounded_panel(surface, input_rect, PANEL_SOFT, 14, 245)
            pygame.draw.rect(surface, BLUE, input_rect, 2, border_radius=14)
            self.join_input.update([])
            input_surface = self.join_input.surface
            input_pos = (
                input_rect.x + 16,
                input_rect.y + (input_rect.height - input_surface.get_height()) // 2,
            )
            surface.blit(input_surface, input_pos)

            confirm_rect = pygame.Rect(panel.x + 80, panel.y + 214, 210, 58)
            cancel_rect = pygame.Rect(panel.right - 290, panel.y + 214, 210, 58)
            self.buttons["join_confirm"] = confirm_rect
            self.buttons["join_cancel"] = cancel_rect
            rounded_panel(surface, confirm_rect, BLUE, 14, 245)
            rounded_panel(surface, cancel_rect, PANEL_SOFT, 14, 245)
            pygame.draw.rect(surface, GOLD, confirm_rect, 2, border_radius=14)
            pygame.draw.rect(surface, GOLD, cancel_rect, 2, border_radius=14)
            draw_text(surface, self.body_font, "Join", confirm_rect.center, WHITE, center=True)
            draw_text(surface, self.body_font, "Cancel", cancel_rect.center, WHITE, center=True)

            if self.join_error:
                draw_text(surface, self.small_font, self.join_error, (panel.centerx, panel.bottom - 26), RED, center=True)
            return

    def draw(self) -> None:
        width, height = self.window.get_size()
        self.draw_background(self.window)
        if self.phase == "menu":
            self.draw_menu(self.window)
            self.draw_notifications(self.window)
            pygame.display.flip()
            return

        self.card_rects.clear()
        self.hidden_rects.clear()

        layout = self.layout(width, height)

        self.slot_layout = layout
        self.buttons.clear()
        self.draw_center_area(self.window)

        for name in self.players or [self.self_name]:
            slot = layout.get(name)
            if slot:
                self.draw_player_zone(self.window, name, slot, width, height)

        self.draw_top_bars(self.window)
        self.draw_notifications(self.window)

        if self.phase == "pregame":
            ready_rect = pygame.Rect(width // 2 - 100, 18, 200, 54)
            self.buttons["ready"] = ready_rect
            rounded_panel(
                self.window, ready_rect, GOLD if not self.ready else PANEL, 18, 240
            )

            pygame.draw.rect(self.window, GOLD, ready_rect, 2, border_radius=18)

            draw_text(
                self.window,
                self.title_font,
                "READY",
                ready_rect.center,
                BLACK if not self.ready else WHITE,
                center=True,
            )

            fill_rect = pygame.Rect(width // 2 + 122, 20, 236, 50)
            self.buttons["fill_bots"] = fill_rect
            rounded_panel(self.window, fill_rect, PANEL, 14, 240)
            pygame.draw.rect(self.window, BLUE, fill_rect, 2, border_radius=14)
            check = "[x]" if self.fill_bots_enabled else "[ ]"
            draw_text(
                self.window,
                self.small_font,
                f"{check} Fill With Bots",
                (fill_rect.centerx, fill_rect.centery),
                WHITE,
                center=True,
            )

        elif self.phase == "reorder":
            done_rect = pygame.Rect(width // 2 - 100, 18, 200, 54)
            self.buttons["done"] = done_rect

            rounded_panel(
                self.window,
                done_rect,
                BLUE if not self.reorder_done else PANEL,
                18,
                240,
            )

            pygame.draw.rect(self.window, BLUE, done_rect, 2, border_radius=18)

            draw_text(
                self.window,
                self.title_font,
                "DONE",
                done_rect.center,
                WHITE,
                center=True,
            )

        if self.selected and self.current_turn_is_self():
            focus_rect = pygame.Rect(width * 0.5 - 120, height * 0.5 - 220, 240, 42)
            rounded_panel(self.window, focus_rect, PANEL, 14, 220)

            if self.selected.get("kind") == "hidden":
                draw_text(
                    self.window,
                    self.body_font,
                    "Hidden card selected",
                    focus_rect.center,
                    WHITE,
                    center=True,
                )

            else:
                selected_tokens = self.selected_tokens()

                if selected_tokens:
                    label = token_label(selected_tokens[0])
                    draw_text(
                        self.window,
                        self.body_font,
                        f"Card focused: {label}",
                        focus_rect.center,
                        WHITE,
                        center=True,
                    )

                else:
                    draw_text(
                        self.window,
                        self.body_font,
                        "Card focused - press PLAY",
                        focus_rect.center,
                        WHITE,
                        center=True,
                    )
        self.draw_game_over(self.window)

        pygame.display.flip()

    def loop(self) -> None:
        while self.running:
            self.process_network()
            handle_events()
            self.clock.tick(FPS)

        try:
            self.send("QUIT")
        except Exception:
            pass
        self.conn.close()

CLIENT: GameClient | None = None

@event(pygame.QUIT)
def _on_quit(_event: pygame.event.Event) -> None:
    if CLIENT is not None:
        CLIENT.running = False

@event(pygame.VIDEORESIZE)
def _on_resize(event: pygame.event.Event) -> None:
    if CLIENT is not None:
        CLIENT.window = pygame.display.set_mode(
            (max(1100, event.w), max(760, event.h)), pygame.RESIZABLE
        )

@event(pygame.MOUSEBUTTONDOWN)
def _on_mouse_down(event: pygame.event.Event) -> None:
    if CLIENT is not None and event.button == 1:
        CLIENT.handle_mouse_down(event.pos)

@event(pygame.MOUSEBUTTONUP)
def _on_mouse_up(event: pygame.event.Event) -> None:
    if CLIENT is not None and event.button == 1:
        CLIENT.handle_mouse_up(event.pos)

@event(pygame.MOUSEMOTION)
def _on_mouse_motion(event: pygame.event.Event) -> None:
    if CLIENT is not None:
        CLIENT.handle_mouse_motion(event.pos)

@event(pygame.KEYDOWN)
def _on_keydown(event: pygame.event.Event) -> None:
    if CLIENT is not None:
        CLIENT.handle_keydown(event)

@draw
def _draw_frame() -> None:
    if CLIENT is not None:
        CLIENT.draw()

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pygame websocket card client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--name", required=False)
    parser.add_argument('-d', action='store_true')
    args = parser.parse_args(argv)

    name = args.name or f"Player{int(time.time()) % 10000}"
    log.info("Connecting", host=args.host, port=args.port, name=name)
    global CLIENT
    CLIENT = GameClient(args.host, args.port, name)
    CLIENT.loop()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
