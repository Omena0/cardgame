# Card Game Rules

## Game Setup

### Deck

- Uses a normal card deck with Jokers removed
- Can be played with 2-n players
- Add additional decks if playing with more than 4 players

### Card Distribution (Start of Game)

Each player receives:

- **3 hidden cards** (face-down on the table in front of the player)
- **3 visible cards** (face-up on top of the hidden cards)
- **5 hand cards** (in hand)

### Initial Card Swap

Before the first turn, players can swap cards between their hand and visible cards however they please, as long as they end with exactly 3 visible cards and 5 hand cards.

### Determining Starting Player

- Whoever has the lowest rank card starts (3 is lowest, since 2 is special and A is highest)
- **Tiebreaker**: If multiple players have the same lowest rank, whoever has the most of that rank starts
- **Continued tiebreaker**: If still tied, compare the next rank up (4, 5, 6, etc.)

## Turn Order

- **Clockwise** turn order
- Players **cannot skip their turn**

## Card Ranks

### Normal Ranks (Low to High)

3, 4, 5, 6, 7, 8, 9, J, Q, K, A

### Special Cards

- **2**: Can be played on any card
- **A**: Largest card (rank above K).
- **10**: Clears the play pile and allows the player to play again immediately (you must not draw from the deck between plays; you only draw after your turn is over). Can be played on any card.
- **4-of-a-Kind**: If 4 cards of the same rank are on top of each other at any time, it acts like a 10 and clears the play pile. Only the player who plays the 4th card gets to play again.

## Playing Cards

### Card Placement Rules

- You can only place cards of **higher or equal rank** on the play pile
- Exception: **2 and 10** can be placed on any card.
- You can play **any number of the same rank** in one turn, but you don't have to play all of them
- You cannot lie about being unable to play

### Playing Hidden Cards

- Hidden cards are played face-down; you cannot see what rank they are until played
- You choose which face-down hidden card to play (but don't know its rank beforehand)

### Card Play Sequence

Each turn:

1. Player plays one or more cards of the same rank (or must draw if unable)
2. If a 10 or 4-of-a-kind is played, the play pile clears and the player plays again
3. Drawing happens at the end of the turn.

## Drawing Cards

### Drawing Rules

- After your turn ends, you must draw cards from the deck until your hand has 5 cards again
- If you cannot play any cards on your turn, you must draw the **entire play pile** into your hand
- If the deck runs out of cards, you stop drawing and your turn simply ends

## Card Availability by State

Once your hand is empty, cards become available in this order:

1. **Hand cards** (5 cards)
2. **Visible cards** (3 cards) - once hand is empty
3. **Hidden cards** (3 cards) - once visible cards are empty (played face-down, unknown rank)

### Special Rule for Clearing Hand

If your last cards in your hand are the same rank as some cards in your visible cards, you can play all of them at once. This simultaneously clears your hand and plays from the visible cards.

Example: If you have 2 Kings in hand and there are 2 Kings in your visible cards, and the play pile has a rank lower than king, you can play all 4 Kings at once, which would clear the play pile and let you play again.

## Winning

- You win when you play all your cards. (hand cards, then visible, then hidden).
- There exist 2nd and 3rd place and so on. Whoever is last loses.

## Key Summary

- **Clockwise** play with **no skipping**
- Play cards of **equal or higher rank** (2 and 10 always playable)
- 10 and 4-of-a-kind **clear the pile and let you play again**
- Draw to 5 cards **at the end of your turn**
- Can't play? **Draw the entire play pile**
- Play hand → visible → hidden (face-down)
- **A is highest**, 3 is lowest (2 and 10 are special)

## Implementation Clarifications (Appendix)

- A player may only perform ONE play action per turn.

- A play action consists of placing one or more cards of the same rank onto the play pile in a single move.

- After completing a play action, the turn normally ends and the player draws up to 5 cards.

- Exception: If a player plays a 10 or completes a 4-of-a-kind, the play pile is cleared immediately and that player is granted ONE additional play action before the turn ends.

- Extra play actions do not stack. A player cannot chain multiple consecutive plays beyond what is explicitly granted by a 10 or a 4-of-a-kind.

- Hidden cards, visible cards, and hand cards are used in order (hand → visible → hidden), but only one play action may be performed per turn regardless of card source.

- If a player cannot make a legal play action, they must draw the entire play pile and end their turn.

- The play pile is cleared only by a 10 or a completed 4-of-a-kind, and otherwise persists between turns as a single stack.

- Drawing always occurs only at the end of a turn (including after any bonus action granted by a reset effect).
