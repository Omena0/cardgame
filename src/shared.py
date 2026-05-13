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

def parse_message(line: str) -> tuple[str, list[str]]:
    parts = [part.strip() for part in line.strip().split("|")]
    command = parts[0].strip().upper()
    return command, parts[1:]
