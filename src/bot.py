from shared import (
    PLAY_RANK_VALUE,
    can_play_on,
    card_rank,
    card_face,
    suit_value,
    card_uid,
    format_cards,
    count_ranks,
    PLAY_RANKS,
    HAND_SORT_VALUE,
)
import random

# Reorder priority: highest to lowest score
REORDER_RANK_ORDER = ['T', 'A', 'K', 'Q', 'J', '2', '9', '8', '7', '6', '5', '4', '3']

# Gameplay: lowest legal rank to play first (excluding 2 and T)
PLAY_ORDER = ['3', '4', '5', '6', '7', '8', '9', 'J', 'Q', 'K', 'A']


def bot_sort_key_for_reorder(token: str) -> tuple:
    """Sort key for reorder: highest ranked cards first."""
    face = card_face(token)
    rank = face[0]
    # Higher rank = earlier in list
    priority = REORDER_RANK_ORDER.index(rank) if rank in REORDER_RANK_ORDER else 99
    return (priority, suit_value(face[1]), card_uid(token))


def bot_choose_reorder(visible: list[str], hand: list[str]) -> tuple[list[str], list[str]]:
    combined = visible + hand
    ordered = sorted(combined, key=bot_sort_key_for_reorder)
    new_visible = ordered[:3]
    new_hand = ordered[3:]
    return new_hand, new_visible


def bot_choose_action(state: dict) -> str:
    phase = state.get("phase", "game")
    if phase == "pregame":
        return "READY"

    if phase == "reorder":
        hand = list(state.get("hand", []))
        visible = list(state.get("visible", []))
        new_hand, new_visible = bot_choose_reorder(visible, hand)
        return f"REORDER {format_cards(new_hand)} | {format_cards(new_visible)}"

    # Game phase
    hand = list(state.get("hand", []))
    visible = list(state.get("visible", []))
    hidden_count = int(state.get("hidden_count", 0))
    pile = list(state.get("pile", []))
    draw_pile_size = int(state.get("draw_pile_size", 0))
    top_face = card_face(pile[-1]) if pile else None
    players = state.get("players", [])
    self_name = state.get("self_name", "")
    turn_player = state.get("turn_player")

    # Determine next player (for targeting)
    next_player = None
    if turn_player and players:
        try:
            idx = players.index(turn_player)
            for offset in range(1, len(players) + 1):
                cand = players[(idx + offset) % len(players)]
                if cand != self_name:
                    next_player = cand
                    break
        except ValueError:
            pass

    # Get next player's hand/hidden info from state if available
    next_hand_counts = state.get("hand_counts", {})
    next_hidden_counts = state.get("hidden_counts", {})
    next_hand_count = next_hand_counts.get(next_player, 0) if next_player else 0
    next_hidden_count = next_hidden_counts.get(next_player, 0) if next_player else 0

    # If we have no cards and must play hidden
    if not hand and not visible and hidden_count > 0:
        return "PLAY_HIDDEN 0"

    # Combine all available cards
    available = hand if hand else visible
    available_ranks = count_ranks(available)

    # Helper: get legal ranks (can be played on top)
    def get_legal_ranks(cards):
        ranks = []
        for token in cards:
            r = card_rank(token)
            if r in {"2", "T"}:
                continue
            if can_play_on(top_face, card_face(token)):
                ranks.append(r)
        return sorted(set(ranks), key=lambda r: PLAY_RANK_VALUE[r])

    # Helper: get all cards of a rank
    def cards_of_rank(tokens, rank):
        return [t for t in tokens if card_rank(t) == rank]

    # Helper: check if playing certain cards would complete 4-of-a-kind
    def would_complete_four(rank, exclude_hand=0, exclude_visible=0):
        """Check if playing cards of rank would make 4 on pile."""
        pile_ranks = [card_rank(t) for t in pile]
        pile_count = pile_ranks.count(rank)
        # After playing, total count = current pile + cards we play + our hand/visible of that rank
        total = pile_count + exclude_hand + exclude_visible
        return total >= 4

    # PRIORITY 1: Check if we can complete 4-of-a-kind (always do it)
    for rank in available_ranks:
        if rank in {"2", "T"}:
            continue
        all_cards_of_rank = cards_of_rank(hand, rank) + cards_of_rank(visible, rank)
        if all_cards_of_rank and would_complete_four(rank, len(cards_of_rank(hand, rank)), len(cards_of_rank(visible, rank))):
            # Play all of that rank
            to_play = all_cards_of_rank
            return f"PLAY {format_cards(to_play)}"

    # PRIORITY 2: If next player has only visible cards (no hidden) and we can force a draw
    # Play only one card (preferably a low one) to keep pressure
    if next_player and next_hidden_count == 0 and next_hand_count > 0:
        legal_ranks = get_legal_ranks(hand) if hand else get_legal_ranks(visible)
        if legal_ranks:
            # Pick lowest legal rank to prolong pressure
            chosen_rank = legal_ranks[0]
            to_play = cards_of_rank(hand, chosen_rank) if hand else cards_of_rank(visible, chosen_rank)
            # Play as few as possible unless forced
            if len(to_play) > 1 and chosen_rank not in {"K", "Q", "J"}:
                to_play = [to_play[0]]
            return f"PLAY {format_cards(to_play)}"

    # PRIORITY 3: Next player has 3 hidden → play middle rank in hand
    if next_player and next_hidden_count == 3:
        # Middle rank strategy: play a rank that's not too high or too low
        if hand:
            hand_ranks = [card_rank(t) for t in hand]
            unique_ranks = sorted(set(hand_ranks), key=lambda r: PLAY_RANK_VALUE[r])
            if unique_ranks:
                # Pick middle-ish rank
                mid_idx = len(unique_ranks) // 2
                chosen_rank = unique_ranks[mid_idx]
                to_play = cards_of_rank(hand, chosen_rank)
                return f"PLAY {format_cards(to_play)}"
        if visible:
            vis_ranks = [card_rank(t) for t in visible]
            unique_ranks = sorted(set(vis_ranks), key=lambda r: PLAY_RANK_VALUE[r])
            if unique_ranks:
                mid_idx = len(unique_ranks) // 2
                chosen_rank = unique_ranks[mid_idx]
                to_play = cards_of_rank(visible, chosen_rank)
                return f"PLAY {format_cards(to_play)}"

    # PRIORITY 4: Next player has <3 hidden → play highest rank
    if next_player and next_hidden_count < 3:
        legal_ranks = get_legal_ranks(hand) if hand else get_legal_ranks(visible)
        if legal_ranks:
            chosen_rank = legal_ranks[-1]  # Highest legal
            to_play = cards_of_rank(hand, chosen_rank) if hand else cards_of_rank(visible, chosen_rank)
            return f"PLAY {format_cards(to_play)}"

    # PRIORITY 5: Always play lowest legal card (excluding 2 and 10)
    # Play as many of that rank as possible, unless rank is K or higher (then only 1)
    if hand:
        legal_ranks = get_legal_ranks(hand)
        if legal_ranks:
            chosen_rank = legal_ranks[0]  # Lowest legal
            all_same = cards_of_rank(hand, chosen_rank)
            # If rank is K, Q, J, A → play only 1 card
            if chosen_rank in {"K", "Q", "J", "A"} and len(all_same) > 1:
                all_same = [all_same[0]]
            # If rank is 9 or lower, play all
            return f"PLAY {format_cards(all_same)}"

    if visible:
        legal_ranks = get_legal_ranks(visible)
        if legal_ranks:
            chosen_rank = legal_ranks[0]
            all_same = cards_of_rank(visible, chosen_rank)
            if chosen_rank in {"K", "Q", "J", "A"} and len(all_same) > 1:
                all_same = [all_same[0]]
            return f"PLAY {format_cards(all_same)}"

    # PRIORITY 6: Can't play anything → try 2 or 10 if draw pile > 1
    if draw_pile_size > 1:
        if available_ranks.get("2"):
            to_play = cards_of_rank(available, "2")
            return f"PLAY {format_cards(to_play)}"
        if available_ranks.get("T"):
            to_play = cards_of_rank(available, "T")
            return f"PLAY {format_cards(to_play)}"

    # PRIORITY 7: No draw pile safety → try 2 or 10 anyway
    if available_ranks.get("2"):
        to_play = cards_of_rank(available, "2")
        return f"PLAY {format_cards(to_play)}"
    if available_ranks.get("T"):
        to_play = cards_of_rank(available, "T")
        return f"PLAY {format_cards(to_play)}"

    # PRIORITY 8: Draw
    return "DRAW"
