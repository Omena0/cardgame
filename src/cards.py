import os

import pygame


def _face_from_token(card: str) -> str:
    return card.split("~", 1)[0]

def index_to_card(i: int):
    if i == 0:
        return "1B"
    elif i == 1:
        return "2B"
    else:
        suits = ["C", "D", "S", "H"]
        ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]
        suit_index = (i - 2) // 13 % 4
        rank_index = (i - 2) % 13
        return ranks[rank_index] + suits[suit_index]

def card_to_index(card: str):
    if card == "1B":
        return 0
    elif card == "2B":
        return 1
    else:
        suits = ["C", "D", "S", "H"]
        ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]
        rank = card[0]
        suit = card[1]
        rank_index = ranks.index(rank)
        suit_index = suits.index(suit)
        return 2 + suit_index * 13 + rank_index

def load_card(card: str):
    face = _face_from_token(card)
    file = os.path.join('assets', face + '.png')
    image = pygame.image.load(file).convert()
    return image
