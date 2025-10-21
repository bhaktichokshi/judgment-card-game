# Judgment Card Game

A lightweight Python web app for playing the Judgment (a.k.a. Hokm) trick-taking card game with friends online. Create a private room, invite players, and run through every hand from the opening max down to a single blind card and back again. Scores and winners are logged so you can keep bragging rights.

## Features

- Host chooses the starting hand size (4, 8, or 16) when creating a room, with deck-safe player caps (12, 6, or 3 players respectively)
- Standard 52-card deck with strict uniqueness—no card is ever dealt twice in a round
- Full round automation: rotating trump suit (♠ → ♦ → ♣ → ♥), enforced dealer-last bidding rule, blind bidding at single-card rounds, trick resolution, and scoring
- Live round-by-round scoreboard that shows current bids and converts them into points once a round ends, plus a history overlay for past games
- Score sheet tracks cumulative points (10 + 11×bid for correct calls) and total correct bids
- Persistent scoreboard (`data/scoreboard.json`) records date, winners, and highlights “mega” winners who top both categories (starred on the leaderboard)
- Browser UI to create/join rooms, manage rounds, bid, play cards, and review historical results

## Quick Start

1. **Install dependencies** – everything is pure standard-library Python.
2. **Run the server**
   ```bash
   python3 server.py
   ```
3. **Open the app** – visit [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.
4. **Create a room** – pick a host name, select the starting hand (4/8/16 cards), share the four-letter code, and wait for everyone to join.
5. **Start the game** – only the host can launch once at least two players are present.

## Gameplay Rules Implemented

- **Hand sizes**: Start from the host-selected base hand (4, 8, or 16 cards), count down to 1, then back up to the same base. Player caps ensure `players × base` stays below 52.
- **Trump rotation**: Spades → Diamonds → Clubs → Hearts, repeating every round.
- **Bidding**:
  - Always begins with the player to the dealer’s left and moves clockwise.
  - Dealer bids last and is prevented from making the total bids equal the number of cards dealt.
  - Single-card rounds force blind bids (hands stay hidden until play begins).
- **Play**:
  - Trick winner is highest trump; otherwise, highest of the led suit.
  - The leader of the first trick becomes the dealer for the next round.
- **Scoring**:
  - Fulfilling a bid awards **10 + 11×bid** points and increments the “correct bids” tally.
  - Score winner (highest points) and guess winner (most correct bids) are highlighted; a player achieving both is marked as the **mega winner** ⭐ on the scoreboard.

## Project Layout

```
server.py              # HTTP server, API, game engine, scoreboard persistence
app/static/index.html  # Single-page UI
app/static/app.js      # Room controls, polling, UI updates
app/static/styles.css  # Styling for panels, cards, scoreboard
data/scoreboard.json   # Created on first run; appends completed game logs
```

## Tips

- Hands and bids refresh automatically every couple of seconds; use the on-screen buttons to submit valid bids or play legal cards.
- The room panel shows the current base hand and the maximum seats available; once the cap is reached, no further joins (or starts) are allowed.
- If a player refreshes or disconnects, they can rejoin the room with the same name/code to continue.
- To wipe historical results, delete `data/scoreboard.json` while the server is stopped.

Enjoy the matches, and may the best bidder earn the ⭐! 
