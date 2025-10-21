const SESSION_KEY = "judgement_session";
const POLL_INTERVAL_MS = 2000;
const DEFAULT_BASE = 8;
const BASE_HAND_LIMITS = { 4: 12, 8: 6, 16: 3 };
const LOBBY_AUDIO_SRC = "/static/audio/lobby.wav";
const AUDIO_STORAGE_KEY = "judgement_audio_muted";

let pollTimer = null;
let latestState = null;
const storage = window.sessionStorage || window.localStorage;
let audioPlayer = null;
let audioMuted = false;

const els = {
  authSection: document.getElementById("auth-section"),
  gameSection: document.getElementById("game-section"),
  roomCode: document.getElementById("room-code-display"),
  playerName: document.getElementById("player-name-display"),
  playersList: document.getElementById("players-list"),
  startGameButton: document.getElementById("start-game-btn"),
  gameStatus: document.getElementById("game-status"),
  roundInfo: document.getElementById("round-info"),
  biddingControls: document.getElementById("bidding-controls"),
  bidOptions: document.getElementById("bid-options"),
  playControls: document.getElementById("play-controls"),
  playerHand: document.getElementById("player-hand"),
  currentTrick: document.getElementById("current-trick"),
  baseHandDisplay: document.getElementById("base-hand-display"),
  baseHandMax: document.getElementById("base-hand-max"),
  roundScoreboard: document.getElementById("round-scoreboard"),
  openHistoryButton: document.getElementById("open-history-btn"),
  closeHistoryButton: document.getElementById("close-history-btn"),
  historyOverlay: document.getElementById("history-overlay"),
  overlayBackdrop: document.getElementById("overlay-backdrop"),
  bidSummary: document.getElementById("bid-summary"),
  scoreboardEntries: document.getElementById("scoreboard-entries"),
  leaveRoomButton: document.getElementById("leave-room-btn"),
  toast: createToast(),
  audioToggle: document.getElementById("audio-toggle"),
};

init();

function init() {
  bindForms();
  els.leaveRoomButton.addEventListener("click", leaveRoom);
  els.startGameButton.addEventListener("click", startGame);
  els.openHistoryButton?.addEventListener("click", openHistoryOverlay);
  els.closeHistoryButton?.addEventListener("click", closeHistoryOverlay);
  els.overlayBackdrop?.addEventListener("click", closeHistoryOverlay);
  document.addEventListener("keydown", handleGlobalKeydown);
  setupAudio();

  const session = getSession();
  if (session) {
    enterGameView(session);
    fetchState();
  }
}

function bindForms() {
  const createForm = document.getElementById("create-room-form");
  const joinForm = document.getElementById("join-room-form");

  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const hostName = document.getElementById("create-host-name").value.trim();
    const baseSelect = document.getElementById("create-base-hand");
    const baseCards = parseInt(baseSelect?.value, 10) || DEFAULT_BASE;
    if (!hostName) return;
    try {
      const response = await apiPost("/api/create_room", {
        host_name: hostName,
        base_cards: baseCards,
      });
      const session = {
        roomCode: response.room_code,
        playerId: response.player_id,
        playerName: response.player_name,
      };
      saveSession(session);
      enterGameView(session);
      const responseBase = Number(response.base_cards) || baseCards;
      const responseMax =
        Number(response.max_players) || BASE_HAND_LIMITS[responseBase] || null;
      updateBaseHandInfo(responseBase, responseMax);
      showToast(`Room ${response.room_code} created. Share the code with friends.`);
      fetchState();
    } catch (error) {
      showToast(error.message || "Unable to create room");
    }
  });

  joinForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const roomCode = document.getElementById("join-room-code").value.trim().toUpperCase();
    const playerName = document.getElementById("join-player-name").value.trim();
    if (!roomCode || !playerName) return;
    try {
      const response = await apiPost("/api/join_room", {
        room_code: roomCode,
        player_name: playerName,
      });
      const session = {
        roomCode: response.room_code,
        playerId: response.player_id,
        playerName: response.player_name,
      };
      saveSession(session);
      enterGameView(session);
      const responseBase = Number(response.base_cards) || null;
      const responseMax =
        Number(response.max_players) || BASE_HAND_LIMITS[responseBase] || null;
      updateBaseHandInfo(responseBase, responseMax);
      showToast(`Joined room ${response.room_code}`);
      fetchState();
    } catch (error) {
      showToast(error.message || "Unable to join room");
    }
  });
}

async function startGame() {
  const session = getSession();
  if (!session) return;
  try {
    await apiPost("/api/start_game", {
      room_code: session.roomCode,
      player_id: session.playerId,
    });
    showToast("Game started");
    fetchState();
  } catch (error) {
    showToast(error.message || "Unable to start game");
  }
}

function enterGameView(session) {
  els.authSection.classList.add("hidden");
  els.gameSection.classList.remove("hidden");
  els.roomCode.textContent = session.roomCode;
  els.playerName.textContent = session.playerName;
  startPolling();
  ensureLobbyMusic();
}

function leaveRoom() {
  stopPolling();
  clearSession();
  latestState = null;
  els.gameSection.classList.add("hidden");
  els.authSection.classList.remove("hidden");
  els.playersList.innerHTML = "";
  els.roundInfo.innerHTML = "<p>Waiting for game to start…</p>";
  els.currentTrick.innerHTML = "";
  els.bidOptions.innerHTML = "";
  els.playerHand.innerHTML = "";
  els.bidSummary.innerHTML = "";
  els.bidSummary.classList.add("hidden");
  updateBaseHandInfo(null, null);
  closeHistoryOverlay();
}

function getSession() {
  try {
    const raw = storage.getItem(SESSION_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
    // eslint-disable-next-line no-empty
  } catch (_) {}
  return null;
}

function saveSession(session) {
  storage.setItem(SESSION_KEY, JSON.stringify(session));
}

function clearSession() {
  storage.removeItem(SESSION_KEY);
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(fetchState, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function fetchState() {
  const session = getSession();
  if (!session) {
    stopPolling();
    return;
  }
  try {
    const url = `/api/state?room=${encodeURIComponent(session.roomCode)}&player_id=${encodeURIComponent(
      session.playerId,
    )}`;
    const response = await fetch(url, {
      method: "GET",
      headers: { "Cache-Control": "no-store" },
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const message = (data && data.error) || `Failed to load state (${response.status})`;
      handleStateError(message);
      return;
    }
    latestState = data;
    updateUI(data);
  } catch (error) {
    handleStateError(error.message || "Connection issue");
  }
}

function updateUI(state) {
  const session = getSession();
  if (!session) return;
  const { room } = state;
  els.roomCode.textContent = room.code;
  els.playerName.textContent = session.playerName;
  updateBaseHandInfo(room.base_cards, room.max_players);
  renderPlayers(room, session);
  renderGame(state, session);
  renderScoreboard(state.scoreboard || []);
}

function renderPlayers(room, session) {
  els.playersList.innerHTML = "";
  const template = document.getElementById("player-row-template");
  const maxPlayers = room.max_players || BASE_HAND_LIMITS[room.base_cards] || null;

  room.players.forEach((player) => {
    const clone = template.content.cloneNode(true);
    const nameEl = clone.querySelector(".player-name");
    const scoreEl = clone.querySelector(".player-score");
    const bidsEl = clone.querySelector(".player-bids");

    nameEl.textContent = player.name;
    if (player.player_id === room.host_id) {
      nameEl.innerHTML += ' <span class="badge">Host</span>';
    }
    if (player.is_you) {
      nameEl.innerHTML += ' <span class="badge">You</span>';
    }

    scoreEl.textContent = `Score: ${player.total_score}`;
    bidsEl.textContent = `Correct bids: ${player.correct_bids}`;
    els.playersList.appendChild(clone);
  });

  const isHost = room.host_id === session.playerId;
  const canStart =
    room.status === "waiting" &&
    isHost &&
    room.players.length >= 2 &&
    (!maxPlayers || room.players.length <= maxPlayers);
  els.startGameButton.disabled = !canStart;
  els.startGameButton.classList.toggle("hidden", room.status !== "waiting");

  switch (room.status) {
    case "waiting":
      if (maxPlayers) {
        els.gameStatus.textContent = `${room.players.length}/${maxPlayers} player(s) ready. Waiting to start.`;
      } else {
        els.gameStatus.textContent = `${room.players.length} player(s) in room. Waiting to start.`;
      }
      break;
    case "playing":
      els.gameStatus.textContent = "Game in progress.";
      break;
    case "finished":
      els.gameStatus.textContent = "Game finished.";
      break;
    default:
      els.gameStatus.textContent = "";
  }
}

function renderGame(state, session) {
  const { game, room, last_result: lastResult } = state;
  if (!game) {
    renderRoundScoreboard(room, null);
    els.roundInfo.innerHTML = "<p>The host can start the game when everyone is ready.</p>";
    els.biddingControls.classList.add("hidden");
    els.playControls.classList.add("hidden");
    els.currentTrick.innerHTML = "";
    els.bidSummary.innerHTML = "";
    els.bidSummary.classList.add("hidden");
    return;
  }

  const roundLines = [];
  if (room.base_cards) {
    roundLines.push(`Base hand: ${room.base_cards} cards`);
  }
  roundLines.push(`Round ${game.current_round} of ${game.total_rounds}`);

  if (game.cards_per_player) {
    roundLines.push(`Cards per player: ${game.cards_per_player}`);
  }

  if (game.trump) {
    roundLines.push(`Trump suit: ${game.trump.symbol} ${game.trump.name}`);
  }

  if (game.current_turn) {
    const turnName = game.current_turn.player_name || "Unknown";
    roundLines.push(`Current turn: ${turnName}`);
  }

  roundLines.push(`Phase: ${game.phase}`);

  const dealerName = getPlayerName(room.players, game.dealer_id);
  const starterName = getPlayerName(room.players, game.starter_id);
  if (dealerName) {
    roundLines.push(`Dealer: ${dealerName}`);
  }
  if (starterName) {
    roundLines.push(`Starter: ${starterName}`);
  }

  if (game.blind_bidding) {
    roundLines.push("Blind round: bids must be placed without looking at cards.");
  }

  els.roundInfo.innerHTML = `<p>${roundLines.join("<br/>")}</p>`;

  renderBidding(game, session);
  renderHand(game, session);
  renderCurrentTrick(game);
  renderBidSummary(game, room, session);
  renderRoundScoreboard(room, game);

  if (room.status === "finished" && lastResult) {
    const winners = buildWinnersSummary(lastResult, room.players);
    els.gameStatus.textContent = `Game finished. ${winners}`;
  }
}

function renderBidding(game, session) {
  if (!game.allowed_bids || !Array.isArray(game.allowed_bids)) {
    els.biddingControls.classList.add("hidden");
    els.bidOptions.innerHTML = "";
    return;
  }

  els.biddingControls.classList.remove("hidden");
  els.bidOptions.innerHTML = "";
  game.allowed_bids.forEach((bid) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = bid.toString();
    button.addEventListener("click", () => submitBid(bid));
    els.bidOptions.appendChild(button);
  });
}

function renderHand(game, session) {
  const { hand = [], allowed_cards: allowedCards = [] } = game;
  els.playerHand.innerHTML = "";

  if (!hand.length && game.phase !== "playing") {
    els.playControls.classList.add("hidden");
    return;
  }

  els.playControls.classList.remove("hidden");

  if (hand.length === 1 && hand[0].card === "??") {
    const cardBack = document.createElement("div");
    cardBack.className = "card-back";
    els.playerHand.appendChild(cardBack);
    return;
  }

  const allowedSet = new Set(allowedCards || []);
  const canAct = Array.isArray(allowedCards) && allowedCards.length > 0;
  hand.forEach(({ card, display }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "card-btn";
    const { rank, suit, colorClass } = parseCardDisplay(display);
    button.classList.add(colorClass);
    button.innerHTML = createCardMarkup(rank, suit);
    button.dataset.card = card;
    const isAllowed = canAct && allowedSet.has(card);
    button.disabled = !isAllowed;
    if (isAllowed) {
      button.classList.add("allowed");
      button.addEventListener("click", () => playCard(card));
    } else {
      if (!canAct) {
        button.classList.add("waiting");
      }
    }
    els.playerHand.appendChild(button);
  });
}

function renderCurrentTrick(game) {
  if (!game.current_trick || !game.current_trick.length) {
    els.currentTrick.innerHTML = "";
    return;
  }
  const items = game.current_trick
    .map((play) => `<li>${play.player_name}: ${play.display}</li>`)
    .join("");
  els.currentTrick.innerHTML = `<h3>Current trick</h3><ul>${items}</ul>`;
}

function renderBidSummary(game, room, session) {
  const container = els.bidSummary;
  if (!container) return;
  const bids = game.bids || {};
  const tricks = game.tricks_won || {};

  if (!room.players.length) {
    container.innerHTML = "";
    container.classList.add("hidden");
    return;
  }

  const rows = room.players
    .map((player) => {
      const bidValue = bids[player.player_id];
      const tricksWon = tricks[player.player_id] ?? 0;
      const isYou = player.player_id === session.playerId;
      const isTurn =
        game.current_turn && game.current_turn.player_id === player.player_id;

      let bidText;
      let bidClass = "bid";
      if (typeof bidValue === "number") {
        bidText = bidValue;
      } else if (game.phase === "bidding") {
        bidText = "Pending";
        bidClass += " pending";
      } else {
        bidText = "—";
      }

      const rowClasses = [];
      if (isYou) rowClasses.push("you");
      if (isTurn && game.phase === "bidding") rowClasses.push("turn");

      return `
        <tr class="${rowClasses.join(" ")}">
          <td>${player.name}</td>
          <td class="${bidClass}">${bidText}</td>
          <td>${tricksWon}</td>
        </tr>
      `;
    })
    .join("");

  const totalBids = Object.values(bids).reduce(
    (sum, value) => (typeof value === "number" ? sum + value : sum),
    0,
  );
  const tricksAvailable = game.cards_per_player ?? 0;

  container.innerHTML = `
    <h3>Current bids</h3>
    <table>
      <thead>
        <tr><th>Player</th><th>Bid</th><th>Tricks</th></tr>
      </thead>
      <tbody>
        ${rows}
      </tbody>
      <tfoot>
        <tr class="totals">
          <td>Total bids</td>
          <td>${totalBids} / ${tricksAvailable}</td>
          <td>—</td>
        </tr>
      </tfoot>
    </table>
  `;
  container.classList.remove("hidden");
}

function renderRoundScoreboard(room, game) {
  const container = els.roundScoreboard;
  if (!container) return;

  const players = room.players || [];
  if (!players.length) {
    container.innerHTML = "<p>No players yet.</p>";
    return;
  }

  let rounds = [];
  if (game && Array.isArray(game.rounds)) {
    rounds = game.rounds;
  } else {
    const base = Number.parseInt(room.base_cards, 10) || DEFAULT_BASE;
    const sequence = buildRoundSequence(base);
    rounds = sequence.map((cards, idx) => ({
      round: idx + 1,
      cards,
      status: "pending",
    }));
  }

  if (!rounds.length) {
    container.innerHTML = "<p>No rounds configured.</p>";
    return;
  }

  const headerCells = [
    "<th>Round</th>",
    "<th>Cards</th>",
    "<th>Status</th>",
    ...players.map((player) => `<th>${player.name}</th>`),
  ];

  const rows = rounds
    .map((round) => {
      const rowClasses = [];
      if (round.is_current) rowClasses.push("current");

      const statusLabel = formatRoundStatus(round.status);
      const bidsByPlayer = round.bids || {};
      const pointsByPlayer = round.points || {};
      const resultsByPlayer = round.results || {};
      const playerCells = players
        .map((player) => {
          const pid = player.player_id;
          if (round.status === "complete") {
            const pointsValue = pointsByPlayer[pid];
            const points = typeof pointsValue === "number" ? pointsValue : 0;
            const result = resultsByPlayer[pid];
            const isHit = result === "hit" || points > 0;
            const cls = isHit ? "round-points hit" : "round-points miss";
            return `<td data-player-id="${pid}" class="${cls}">${points}</td>`;
          }
          if (round.status === "bidding" || round.status === "playing") {
            const bidValue = bidsByPlayer[pid];
            const display = typeof bidValue === "number" ? bidValue : "—";
            return `<td data-player-id="${pid}" class="round-bid">${display}</td>`;
          }
          return `<td data-player-id="${pid}" class="round-pending">—</td>`;
        })
        .join("");

      return `
        <tr class="${rowClasses.join(" ")}">
          <td class="round-label">${round.round}</td>
          <td>${round.cards}</td>
          <td>${statusLabel}</td>
          ${playerCells}
        </tr>
      `;
    })
    .join("");

  container.innerHTML = `
    <table>
      <thead>
        <tr>${headerCells.join("")}</tr>
      </thead>
      <tbody>
        ${rows}
      </tbody>
    </table>
  `;

  if (game?.current_turn?.player_id && game?.phase !== "complete") {
    const currentPlayerId = game.current_turn.player_id;
    container.querySelectorAll("tbody tr").forEach((row) => {
      const targetCell = row.querySelector(`td[data-player-id="${currentPlayerId}"]`);
      if (targetCell) {
        row.classList.add("player-turn");
      }
    });
  }
}

function renderScoreboard(entries) {
  els.scoreboardEntries.innerHTML = "";
  if (!entries.length) {
    els.scoreboardEntries.innerHTML = "<p>No games logged yet.</p>";
    return;
  }

  entries
    .slice()
    .reverse()
    .forEach((entry) => {
      const wrapper = document.createElement("div");
      wrapper.className = "scoreboard-entry";
      const date = new Date(entry.completed_at || entry.timestamp || Date.now());
      const baseCards = entry.base_cards || entry.base_hand || null;
      const headerSuffix = baseCards ? ` — Base ${baseCards}` : "";
      wrapper.innerHTML = `<h4>${date.toLocaleString()} — Room ${entry.room_code}${headerSuffix}</h4>`;

      const table = document.createElement("table");
      const header = document.createElement("tr");
      header.innerHTML =
        "<th>Player</th><th>Score</th><th>Correct bids</th><th>Honours</th>";
      table.appendChild(header);

      const scoreWinnerIds = entry.score_winners || [];
      const guessWinnerIds = entry.guess_winners || [];
      const megaWinnerIds = entry.mega_winners || [];

      (entry.players || []).forEach((player) => {
        const tr = document.createElement("tr");
        const isScoreWinner = scoreWinnerIds.includes(player.player_id);
        const isGuessWinner = guessWinnerIds.includes(player.player_id);
        const isMega = megaWinnerIds.includes(player.player_id);

        const honours = [];
        if (isScoreWinner) honours.push("Score winner");
        if (isGuessWinner) honours.push("Guess winner");
        if (isMega) honours.push("Mega winner");

        const scoreDisplay = isMega
          ? `⭐ ${player.total_score} ⭐`
          : player.total_score.toString();

        tr.innerHTML = `
          <td>${player.name}</td>
          <td class="${isMega ? "mega" : ""}">${scoreDisplay}</td>
          <td>${player.correct_bids}</td>
          <td>${honours.join(", ") || "—"}</td>
        `;
        table.appendChild(tr);
      });

      wrapper.appendChild(table);
      els.scoreboardEntries.appendChild(wrapper);
    });
}

function updateBaseHandInfo(baseCards, maxPlayers) {
  const parsedBase = Number.parseInt(baseCards, 10);
  const resolvedBase = Number.isNaN(parsedBase) ? null : parsedBase;
  const parsedMax = Number.parseInt(maxPlayers, 10);
  const resolvedMax =
    Number.isNaN(parsedMax)
      ? resolvedBase
        ? BASE_HAND_LIMITS[resolvedBase] || null
        : null
      : parsedMax;

  if (els.baseHandDisplay) {
    els.baseHandDisplay.textContent = resolvedBase ? resolvedBase : "—";
  }
  if (els.baseHandMax) {
    els.baseHandMax.textContent = resolvedMax ? resolvedMax : "—";
  }
}

function setupAudio() {
  try {
    audioMuted = storage.getItem(AUDIO_STORAGE_KEY) === "true";
  } catch (_) {
    audioMuted = false;
  }
  updateAudioToggle();
  els.audioToggle?.addEventListener("click", toggleAudio);
}

function prepareAudio() {
  if (audioPlayer) return;
  audioPlayer = new Audio(LOBBY_AUDIO_SRC);
  audioPlayer.loop = true;
  audioPlayer.volume = 0.18;
  audioPlayer.preload = "auto";
}

function ensureLobbyMusic() {
  if (audioMuted) return;
  prepareAudio();
  if (!audioPlayer) return;
  const playPromise = audioPlayer.play();
  if (playPromise && typeof playPromise.catch === "function") {
    playPromise.catch(() => {});
  }
}

function toggleAudio() {
  audioMuted = !audioMuted;
  try {
    storage.setItem(AUDIO_STORAGE_KEY, audioMuted ? "true" : "false");
  } catch (_) {}
  updateAudioToggle();
  if (audioMuted) {
    if (audioPlayer) {
      audioPlayer.pause();
    }
  } else {
    ensureLobbyMusic();
  }
}

function updateAudioToggle() {
  if (!els.audioToggle) return;
  els.audioToggle.textContent = audioMuted ? "🔇 Music Off" : "🔊 Music On";
  els.audioToggle.setAttribute("aria-pressed", audioMuted ? "true" : "false");
}

async function submitBid(bid) {
  const session = getSession();
  if (!session) return;
  try {
    await apiPost("/api/submit_bid", {
      room_code: session.roomCode,
      player_id: session.playerId,
      bid,
    });
    showToast(`Bid submitted: ${bid}`);
    fetchState();
  } catch (error) {
    showToast(error.message || "Unable to submit bid");
  }
}

async function playCard(card) {
  const session = getSession();
  if (!session) return;
  try {
    await apiPost("/api/play_card", {
      room_code: session.roomCode,
      player_id: session.playerId,
      card,
    });
    fetchState();
  } catch (error) {
    showToast(error.message || "Unable to play card");
  }
}

async function apiPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function createToast() {
  const toast = document.createElement("div");
  toast.className = "toast";
  document.body.appendChild(toast);
  return toast;
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  setTimeout(() => {
    els.toast.classList.remove("show");
  }, 2500);
}

function handleStateError(message) {
  showToast(message);
  if (!message) return;
  const normalized = message.toLowerCase();
  if (normalized.includes("room not found")) {
    leaveRoom();
  } else if (normalized.includes("game not started")) {
    // Nothing special, stay in room but inform user.
  } else if (normalized.includes("player limit")) {
    // informational only
  }
}

function getPlayerName(players, playerId) {
  const match = players.find((p) => p.player_id === playerId);
  return match ? match.name : "";
}

function buildWinnersSummary(result, players) {
  const name = (id) => getPlayerName(players, id) || id;
  const scoreWinners = result.score_winners.map(name);
  const guessWinners = result.guess_winners.map(name);
  const megaWinners = result.mega_winners.map(name);

  const parts = [];
  if (scoreWinners.length) {
    parts.push(`Score: ${scoreWinners.join(", ")}`);
  }
  if (guessWinners.length) {
    parts.push(`Best guess: ${guessWinners.join(", ")}`);
  }
  if (megaWinners.length) {
    parts.push(`Mega: ${megaWinners.join(", ")}`);
  }
  return parts.join(" | ");
}

function parseCardDisplay(display) {
  if (!display || display === "??") {
    return { rank: "?", suit: "?", colorClass: "black" };
  }
  const suit = display.slice(-1);
  const rank = display.slice(0, -1);
  const colorClass = suit === "♥" || suit === "♦" ? "red" : "black";
  return { rank, suit, colorClass };
}

function createCardMarkup(rank, suit) {
  return `
    <span class="corner top">
      <span class="rank">${rank}</span>
      <span class="suit">${suit}</span>
    </span>
    <span class="suit large">${suit}</span>
    <span class="corner bottom">
      <span class="rank">${rank}</span>
      <span class="suit">${suit}</span>
    </span>
  `;
}

function buildRoundSequence(base) {
  const numericBase = Number.parseInt(base, 10);
  const safeBase = Number.isNaN(numericBase) || numericBase <= 0 ? DEFAULT_BASE : numericBase;
  const descending = [];
  for (let value = safeBase; value >= 1; value -= 1) {
    descending.push(value);
  }
  const ascending = [];
  for (let value = 2; value <= safeBase; value += 1) {
    ascending.push(value);
  }
  return [...descending, ...ascending];
}

function formatRoundStatus(status) {
  switch (status) {
    case "bidding":
      return "In progress";
    case "playing":
      return "In progress";
    case "complete":
      return "Complete";
    case "waiting":
      return "Waiting";
    default:
      return "Pending";
  }
}

function openHistoryOverlay() {
  if (!els.historyOverlay || !els.overlayBackdrop) return;
  const scoreboardEntries = latestState && Array.isArray(latestState.scoreboard)
    ? latestState.scoreboard
    : [];
  renderScoreboard(scoreboardEntries);
  els.historyOverlay.classList.remove("hidden");
  els.historyOverlay.setAttribute("aria-hidden", "false");
  els.overlayBackdrop.classList.remove("hidden");
}

function closeHistoryOverlay() {
  if (!els.historyOverlay || !els.overlayBackdrop) return;
  els.historyOverlay.classList.add("hidden");
  els.historyOverlay.setAttribute("aria-hidden", "true");
  els.overlayBackdrop.classList.add("hidden");
}

function handleGlobalKeydown(event) {
  if (event.key === "Escape") {
    closeHistoryOverlay();
  }
}
