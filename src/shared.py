from __future__ import annotations

import math
import random
from collections import Counter

SUITS = ("C", "D", "S", "H")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K")

PLAY_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A")
START_RANKS = ("3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A", "2")
HAND_SORT_RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "J", "Q", "K", "A", "T")

PLAY_RANK_VALUE = {rank: index for index, rank in enumerate(PLAY_RANKS)}
START_RANK_VALUE = {rank: index for index, rank in enumerate(START_RANKS)}
HAND_SORT_VALUE = {rank: index for index, rank in enumerate(HAND_SORT_RANKS)}

BOT_VISIBLE_PRIORITY = {
    "T": 12,
    "A": 11,
    "K": 10,
    "Q": 9,
    "J": 8,
    "2": 7,
    "9": 6,
    "8": 5,
    "7": 4,
    "6": 3,
    "5": 2,
    "4": 1,
    "3": 0,
}


def card_face(token: str) -> str:
    return token.split("~", 1)[0]


def card_uid(token: str) -> str:
    parts = token.split("~", 1)
    return parts[1] if len(parts) == 2 else ""


def make_token(face: str, uid: int) -> str:
    return f"{face}~{uid}"


def card_rank(token: str) -> str:
    return card_face(token)[0]


def card_suit(token: str) -> str:
    return card_face(token)[1]


def pretty_rank(rank: str) -> str:
    return "10" if rank == "T" else rank


def token_label(token: str) -> str:
    face = card_face(token)
    return f"{pretty_rank(face[0])}{face[1]}"


def parse_cards(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def format_cards(tokens: list[str]) -> str:
    return ",".join(tokens)


def suit_value(suit: str) -> int:
    return SUITS.index(suit)


def hand_sort_key(token: str) -> tuple[int, int, str]:
    face = card_face(token)
    return HAND_SORT_VALUE[face[0]], suit_value(face[1]), card_uid(token)


def bot_sort_key(token: str) -> tuple[int, int, str]:
    face = card_face(token)
    return -BOT_VISIBLE_PRIORITY[face[0]], suit_value(face[1]), card_uid(token)


def build_deck(player_count: int) -> list[str]:
    deck_count = max(1, math.ceil(player_count / 4))
    tokens: list[str] = []
    uid = 1
    for _deck_index in range(deck_count):
        for suit in SUITS:
            for rank in RANKS:
                tokens.append(make_token(f"{rank}{suit}", uid))
                uid += 1
    random.shuffle(tokens)
    return tokens


def can_play_on(top_face: str | None, face: str) -> bool:
    rank = face[0]
    if rank in {"2", "T"}:
        return True
    if top_face is None:
        return True
    return PLAY_RANK_VALUE[rank] >= PLAY_RANK_VALUE[top_face[0]]


def pile_rank_value(face: str) -> int:
    return PLAY_RANK_VALUE[face[0]]


def start_key(tokens: list[str]) -> tuple[int, ...]:
    counts = Counter(card_rank(token) for token in tokens)
    key: list[int] = []
    for rank in START_RANKS:
        count = counts.get(rank, 0)
        if count:
            key.append(START_RANK_VALUE[rank])
            key.append(-count)
    return tuple(key)


def choose_start_player(players: list[tuple[str, list[str]]]) -> str:
    return min(players, key=lambda item: start_key(item[1]))[0]


def count_ranks(tokens: list[str]) -> Counter[str]:
    return Counter(card_rank(token) for token in tokens)


def sort_hand(tokens: list[str]) -> list[str]:
    return sorted(tokens, key=hand_sort_key)


def bot_choose_reorder(visible: list[str], hand: list[str]) -> tuple[list[str], list[str]]:
    combined = visible + hand
    ordered = sorted(combined, key=bot_sort_key)
    new_visible = ordered[:3]
    new_hand = ordered[3:]
    return new_hand, new_visible


def _best_rank_for_play(ranks: list[str], top_face: str | None) -> str | None:
    candidates = [rank for rank in ranks if rank not in {"2", "T"}]
    candidates.sort(key=lambda rank: PLAY_RANK_VALUE[rank])
    for rank in candidates:
        if can_play_on(top_face, f"{rank}C"):
            return rank
    return None


def _cards_of_rank(tokens: list[str], rank: str) -> list[str]:
    return [token for token in tokens if card_rank(token) == rank]


def bot_choose_action(state: dict) -> str:
    phase = state.get("phase", "game")
    if phase == "pregame":
        return "READY"
    if phase == "reorder":
        hand = list(state.get("hand", []))
        visible = list(state.get("visible", []))
        new_hand, new_visible = bot_choose_reorder(visible, hand)
        return f"REORDER {format_cards(new_hand)} | {format_cards(new_visible)}"

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


def parse_message(line: str) -> tuple[str, list[str]]:
    parts = [part.strip() for part in line.strip().split("|")]
    command = parts[0].strip().upper()
    return command, parts[1:]
