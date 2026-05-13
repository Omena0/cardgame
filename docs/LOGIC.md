
# Extra game logic

## Protocol

See [PROTO.txt](PROTO.txt)

## Phases

### Pre-game phase

Before all players have pressed READY.

You can join the game during this time.

### Reorder phase

You can reorder the visible cards during this phase.

### Game phase

The actual gameplay phase. You can play cards during this phase.

Messages except for QUIT are invalid from players except the one whose turn it is.

## Turn timer

There is a 60 second turn timer.

If the player has not played a card within the time limit they are kicked.

The timer resets on card play. So playing a 10 or 4-of-a-kind will reset it.

Same logic applies as disconnects.

## Invalid message handling

Whenever server send INVALID, the client must attempt again.

## Disconnect handling

The player is replaced with a bot.

## Bot logic

- Swap the visible cards to the highest cards in hand by score.
  - Order (highest to lowest, by reverse index): 10, A, K, Q, J, 2, 9, 8, 7, 6, 5, 4, 3
  - If there is a pair, score them as their product.
  - If there is a triple, score them as 100.
- Always play lowest legal card (excluding 2 and 10)
- Always play as many cards of a rank as possible
- If cant play anything:
  - If draw pile has more than 1 card, play 2
  - Otherwise play 10
  - If dont have 2 or a 10, draw pile
- If at any point the bot can complete a 4-of-a-kind, it will.
