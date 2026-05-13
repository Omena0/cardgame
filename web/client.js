/* Card Game Web Client
 * Mirrors the pygame client state machine and server protocol.
 */

const COLORS = {
    TABLE_GREEN: '#1b4d3e',
    TABLE_GREEN_DARK: '#0e2c1e',
    PANEL: '#141c1e',
    PANEL_SOFT: '#232f30',
    GOLD: '#ddbb62',
    RED: '#dc5c5c',
    BLUE: '#5b8be4',
    WHITE: '#f5f5f0',
    MUTED: '#c0c6be',
    BLACK: '#121212',
};

const SCREEN = { width: 1600, height: 960 };
const FPS = 60;
const PLAY_CARD_SIZE = { width: 92, height: 132 };
const SMALL_CARD_SIZE = { width: 72, height: 104 };
const BACK_CARD_SIZE = { width: 92, height: 132 };
const MENU_BUTTON_SIZE = { width: 340, height: 68 };
const PLAY_ANIM_DURATION = 0.18;
const DRAW_ANIM_DURATION = 0.16;
const DRAG_THRESHOLD = 8;
const TOUCH_DRAG_THRESHOLD = 14;
const DOUBLE_CLICK_WINDOW = 0.35;

// Mobile / responsive support
let sc = 1.0; // current render scale factor – updated each frame
let isTouchInteraction = false;

function getScale(width, height) {
    return Math.min(1.0, Math.min(width, height) / 600);
}

// Scale a pixel value by the current render scale
function sz(n) {
    return Math.round(n * sc);
}

// Return a scaled CSS font string
function sfont(px, weight) {
    const s = Math.round(px * Math.max(0.78, sc));
    return weight ? `${weight} ${s}px "DejaVu Sans", sans-serif` : `${s}px "DejaVu Sans", sans-serif`;
}

const SUITS = ['C', 'D', 'S', 'H'];
const RANKS = ['A', '2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K'];
const HAND_SORT_ORDER = ['2', '3', '4', '5', '6', '7', '8', '9', 'J', 'Q', 'K', 'A', 'T'];
const HAND_SORT_INDEX = new Map(HAND_SORT_ORDER.map((rank, index) => [rank, index]));
const SUIT_INDEX = new Map(SUITS.map((suit, index) => [suit, index]));

const cardImages = new Map();
const backImage = new Image();
backImage.src = 'assets/1B.png';

const state = {
    phase: 'menu',
    menuScreen: 'main',
    roomCode: null,
    players: [],
    botSeats: new Set(),
    fillBotsEnabled: true,
    ready: false,
    reorderDone: false,
    readyByPlayer: {},
    autoReadyAfterJoin: false,
    autoDoneReorderOnce: false,
    pendingRoomJoin: false,
    pendingJoinCode: '',
    joinError: null,
    joinRoomCode: '',
    selfName: '',
    selfHand: [],
    selfVisible: [],
    selfHiddenCount: 3,
    visibleByPlayer: {},
    handCounts: {},
    hiddenCounts: {},
    pile: [],
    drawPileSize: 0,
    turnPlayer: null,
    turnDeadline: null,
    finished: {},
    gameOverText: null,
    selected: null,
    selectedAt: 0,
    dragging: null,
    lastCardClick: { token: null, selected: false, time: 0 },
    messages: [],
    playAnimations: [],
    cardMotion: new Map(),
    lastPositions: new Map(),
    cardRects: new Map(),
    hiddenRects: [],
    buttons: {},
    slotLayout: {},
    pendingAutoDoneReorder: false,
};

let canvas = null;
let ctx = null;
let ws = null;

function now() {
    return performance.now() / 1000;
}

function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
}

function easeOutQuad(t) {
    return 1 - (1 - t) * (1 - t);
}

function cardFace(token) {
    return token.split('~', 1)[0];
}

function cardRank(token) {
    return cardFace(token)[0];
}

function prettyRank(rank) {
    return rank === 'T' ? '10' : rank;
}

function tokenLabel(token) {
    const face = cardFace(token);
    return `${prettyRank(face[0])}${face[1]}`;
}

function parseCards(text) {
    if (!text) {
        return [];
    }
    return text.split(',').map((part) => part.trim()).filter(Boolean);
}

function formatCards(tokens) {
    return tokens.join(',');
}

function sortHand(tokens) {
    return [...tokens].sort((left, right) => {
        const leftFace = cardFace(left);
        const rightFace = cardFace(right);
        const rankDelta = HAND_SORT_INDEX.get(leftFace[0]) - HAND_SORT_INDEX.get(rightFace[0]);
        if (rankDelta !== 0) {
            return rankDelta;
        }
        const suitDelta = SUIT_INDEX.get(leftFace[1]) - SUIT_INDEX.get(rightFace[1]);
        if (suitDelta !== 0) {
            return suitDelta;
        }
        return left.localeCompare(right);
    });
}

function parseMessage(line) {
    if (line.includes(' | ')) {
        const parts = line.split(' | ');
        return [parts[0].toUpperCase(), ...parts.slice(1).map((part) => part.trim())];
    }
    const [command, ...rest] = line.split(' ');
    return [command.toUpperCase(), rest.join(' ').trim()];
}

function send(line) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(line);
    }
}

function pushMsg(text) {
    state.messages.push([now(), text]);
    if (state.messages.length > 7) {
        state.messages.shift();
    }
}

function currentTurnIsSelf() {
    return state.phase === 'game' && state.turnPlayer === state.selfName;
}

function resetForLobby() {
    state.phase = 'pregame';
    state.ready = false;
    state.reorderDone = false;
    state.turnPlayer = null;
    state.turnDeadline = null;
    state.readyByPlayer = {};
    state.pendingAutoDoneReorder = false;
    state.selfHand = [];
    state.selfVisible = [];
    state.selfHiddenCount = 3;
    state.visibleByPlayer = {};
    state.handCounts = {};
    state.hiddenCounts = {};
    state.pile = [];
    state.drawPileSize = 0;
    state.finished = {};
    state.gameOverText = null;
    state.selected = null;
    state.selectedAt = 0;
    state.dragging = null;
    state.lastCardClick = { token: null, selected: false, time: 0 };
    state.playAnimations = [];
}

function joinCurrentRoom() {
    send(`JOIN_GAME | ${state.selfName}`);
    resetForLobby();
    state.menuScreen = 'lobby';
}

function requestCreateRoom(fillWithBots) {
    state.fillBotsEnabled = fillWithBots;
    state.pendingRoomJoin = true;
    state.pendingJoinCode = '';
    state.joinError = null;
    send(`CREATE_ROOM | ${fillWithBots ? 1 : 0}`);
}

function requestJoinRoom(roomCode) {
    const code = roomCode.trim().toUpperCase();
    if (!code) {
        state.joinError = 'Enter room code.';
        return;
    }
    state.pendingRoomJoin = true;
    state.pendingJoinCode = code;
    state.joinError = null;
    send(`ROOM | ${code}`);
}

function startTutorial() {
    pushMsg('Open docs/RULES.md for the full rules.');
    window.open('about:blank', '_blank', 'noopener');
}

function connectModal(id, show) {
    const el = document.getElementById(id);
    if (!el) {
        return;
    }
    el.classList.toggle('hidden', !show);
}

function resizeCanvas() {
    if (!canvas || !ctx) {
        return;
    }
    const dpr = window.devicePixelRatio || 1;
    const width = window.innerWidth;
    const height = window.innerHeight;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function canvasPoint(event) {
    const rect = canvas.getBoundingClientRect();
    return {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
    };
}

function roundedRectPath(context, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    context.beginPath();
    context.moveTo(x + r, y);
    context.lineTo(x + width - r, y);
    context.quadraticCurveTo(x + width, y, x + width, y + r);
    context.lineTo(x + width, y + height - r);
    context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    context.lineTo(x + r, y + height);
    context.quadraticCurveTo(x, y + height, x, y + height - r);
    context.lineTo(x, y + r);
    context.quadraticCurveTo(x, y, x + r, y);
    context.closePath();
}

function drawRoundedPanel(x, y, width, height, color, radius = 16, alpha = 255) {
    ctx.save();
    ctx.globalAlpha = alpha / 255;
    ctx.fillStyle = color;
    roundedRectPath(ctx, x, y, width, height, radius);
    ctx.fill();
    ctx.restore();
}

function drawText(text, x, y, color, options = {}) {
    const {
        font = '20px "DejaVu Sans", sans-serif',
        align = 'left',
        baseline = 'alphabetic',
    } = options;
    ctx.save();
    ctx.fillStyle = color;
    ctx.font = font;
    ctx.textAlign = align;
    ctx.textBaseline = baseline;
    ctx.fillText(text, x, y);
    ctx.restore();
}

function centerPoint(rect) {
    return {
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
    };
}

function playPileCenter(width, height) {
    return {
        x: width / 2 + sz(20),
        y: height / 2,
    };
}

function playerSourceCenter(player) {
    const slot = state.slotLayout[player];
    if (slot) {
        return centerPoint({ x: slot.panel.x - sz(140), y: slot.panel.y - sz(43), width: sz(280), height: sz(86) });
    }
    const width = window.innerWidth;
    const height = window.innerHeight;
    return { x: width / 2, y: height / 2 };
}

function queuePlayAnimation(player, tokens, hidden) {
    if (!tokens.length) {
        return;
    }
    const width = window.innerWidth;
    const height = window.innerHeight;
    const target = playPileCenter(width, height);
    const sourceFallback = playerSourceCenter(player);
    const timeNow = now();
    const stagger = 0.04;

    tokens.forEach((token, index) => {
        const existing = state.cardRects.get(token);
        const source = existing
            ? centerPoint(existing)
            : {
                x: sourceFallback.x + index * 10,
                y: sourceFallback.y - index * 2 - (hidden ? 24 : 0),
            };
        state.playAnimations.push({
            token,
            start: source,
            end: {
                x: target.x + index * 6,
                y: target.y - index * 4,
            },
            startTime: timeNow + index * stagger,
            duration: PLAY_ANIM_DURATION,
            hidden,
            player,
        });
    });
}

function imageForFace(face) {
    return cardImages.get(face) || null;
}

function drawCardImage(face, x, y, width, height, angle = 0, alpha = 1) {
    const image = imageForFace(face);
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(x, y);
    ctx.rotate((angle * Math.PI) / 180);
    if (image && image.complete && image.naturalWidth > 0) {
        ctx.drawImage(image, -width / 2, -height / 2, width, height);
    } else {
        ctx.fillStyle = '#cbb68c';
        roundedRectPath(ctx, -width / 2, -height / 2, width, height, 10);
        ctx.fill();
        ctx.fillStyle = '#332b1d';
        ctx.font = 'bold 16px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(face, 0, 0);
    }
    ctx.restore();
}

function drawBackCard(x, y, width, height, angle = 0, alpha = 1) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(x, y);
    ctx.rotate((angle * Math.PI) / 180);
    if (backImage.complete && backImage.naturalWidth > 0) {
        ctx.drawImage(backImage, -width / 2, -height / 2, width, height);
    } else {
        ctx.fillStyle = '#2c4f4a';
        roundedRectPath(ctx, -width / 2, -height / 2, width, height, 10);
        ctx.fill();
    }
    ctx.restore();
}

function playerLayoutShell(playerCount, width, height) {
    const shell = [];
    const centerX = width * 0.5;
    const centerY = height * 0.5;
    const outerRx = width * 0.31;
    const outerRy = height * 0.29;
    const visibleGap = 52.0 * sc;
    const handGap = 142.0 * sc;
    const sideCardEdgePush = width * 0.08;
    const verticalPanelPull = height * 0.062;

    for (let index = 0; index < playerCount; index += 1) {
        const theta = Math.PI / 2 + ((Math.PI * 2) * index) / Math.max(1, playerCount);
        const radialX = Math.cos(theta);
        const radialY = Math.sin(theta);
        const tangentX = -radialY;
        const tangentY = radialX;

        const baseAnchor = {
            x: centerX + radialX * outerRx,
            y: centerY + radialY * outerRy,
        };

        const cardAnchor = {
            x: baseAnchor.x + radialX * sideCardEdgePush * Math.abs(radialX),
            y: baseAnchor.y + radialY * sideCardEdgePush * Math.abs(radialX),
        };

        const panelPull = verticalPanelPull * Math.abs(radialY);
        const panelCenter = {
            x: baseAnchor.x - radialX * panelPull,
            y: baseAnchor.y - radialY * panelPull,
        };

        const visibleCenter = {
            x: cardAnchor.x + radialX * visibleGap,
            y: cardAnchor.y + radialY * visibleGap,
        };

        const handCenter = {
            x: cardAnchor.x + radialX * handGap,
            y: cardAnchor.y + radialY * handGap,
        };

        // Clamp hand/visible positions so cards stay on screen
        const halfCardW = sz(PLAY_CARD_SIZE.width / 2) + 4;
        const halfCardH = sz(PLAY_CARD_SIZE.height / 2) + 4;
        handCenter.x = clamp(handCenter.x, halfCardW, width - halfCardW);
        handCenter.y = clamp(handCenter.y, halfCardH, height - halfCardH);
        visibleCenter.x = clamp(visibleCenter.x, halfCardW, width - halfCardW);
        visibleCenter.y = clamp(visibleCenter.y, halfCardH, height - halfCardH);

        shell.push({
            panel: panelCenter,
            visible: visibleCenter,
            hand: handCenter,
            angle: (theta * 180) / Math.PI - 90,
            spread: { x: tangentX, y: tangentY },
        });
    }

    return shell;
}

function emitReorder() {
    if (state.selfHand.length !== 5 || state.selfVisible.length !== 3) {
        return;
    }
    send(`REORDER | ${formatCards(state.selfHand)} | ${formatCards(state.selfVisible)}`);
}

function sendReady() {
    send('READY');
    state.ready = true;
    pushMsg('Ready sent.');
}

function sendReorderDone() {
    if (state.selfHand.length !== 5 || state.selfVisible.length !== 3) {
        pushMsg('Reorder must end with exactly 5 hand and 3 visible cards.');
        return;
    }
    send('REORDER_DONE');
    state.reorderDone = true;
    pushMsg('Reorder locked.');
}

function sendDraw() {
    send('DRAW');
}

function sendFillBots(enabled) {
    state.fillBotsEnabled = enabled;
    send(`SET_FILL_BOTS | ${enabled ? 1 : 0}`);
}

function selectedTokens() {
    if (!state.selected) {
        return [];
    }
    if (state.selected.kind === 'card' && Array.isArray(state.selected.tokens)) {
        return state.selected.tokens.filter(Boolean);
    }
    if (state.selected.kind === 'hidden' && Array.isArray(state.selected.tokens)) {
        return state.selected.tokens.filter(Boolean);
    }
    return [];
}

function selectedCardToken() {
    const tokens = selectedTokens();
    return tokens.length ? tokens[0] : null;
}

function selfHandRank() {
    if (!state.selfHand.length) {
        return null;
    }
    const ranks = new Set(state.selfHand.map((token) => cardRank(token)));
    if (ranks.size !== 1) {
        return null;
    }
    return ranks.values().next().value;
}

function sameRankTokens(token) {
    const rank = cardRank(token);
    const matchingHand = state.selfHand.filter((item) => cardRank(item) === rank);
    if (state.selfHand.length) {
        if (!matchingHand.length) {
            return [];
        }
        if (selfHandRank() === rank) {
            const matchingVisible = state.selfVisible.filter((item) => cardRank(item) === rank);
            return matchingHand.concat(matchingVisible);
        }
        return matchingHand;
    }
    return state.selfVisible.filter((item) => cardRank(item) === rank);
}

function toggleCardFocus(token) {
    const current = selectedTokens();
    const updated = current.includes(token)
        ? current.filter((item) => item !== token)
        : current.concat(token);
    if (updated.length) {
        state.selected = { kind: 'card', tokens: updated };
        state.selectedAt = now();
    } else {
        state.selected = null;
        state.selectedAt = 0;
    }
}

function hiddenIndexFromPoint(point) {
    if (!state.hiddenRects.length) {
        return 0;
    }
    let bestIndex = 0;
    let bestScore = Number.POSITIVE_INFINITY;
    state.hiddenRects.forEach((rect, index) => {
        const score = Math.abs(rect.x + rect.width / 2 - point.x) + Math.abs(rect.y + rect.height / 2 - point.y);
        if (score < bestScore) {
            bestScore = score;
            bestIndex = index;
        }
    });
    return bestIndex;
}

function moveTokenBetweenZones(token, toVisible) {
    if (state.selfHand.includes(token)) {
        state.selfHand = state.selfHand.filter((item) => item !== token);
    }
    if (state.selfVisible.includes(token)) {
        state.selfVisible = state.selfVisible.filter((item) => item !== token);
    }
    if (toVisible) {
        state.selfVisible.push(token);
    } else {
        state.selfHand.push(token);
        state.selfHand = sortHand(state.selfHand);
    }
}

function reorderTokenInList(tokens, token, point) {
    const ordered = tokens.slice();
    const index = ordered.indexOf(token);
    if (index === -1) {
        return ordered;
    }
    ordered.splice(index, 1);
    let insertIndex = 0;
    for (const other of ordered) {
        const rect = state.cardRects.get(other);
        if (rect && point.x > rect.x + rect.width / 2) {
            insertIndex += 1;
        }
    }
    ordered.splice(insertIndex, 0, token);
    return ordered;
}

function clickButton(name, point) {
    const rect = state.buttons[name];
    return Boolean(rect && point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height);
}

function dropInZone(point, name) {
    return clickButton(name, point);
}

function playSelected() {
    if (!state.selected || !currentTurnIsSelf()) {
        return;
    }
    if (state.selected.kind === 'hidden') {
        if (typeof state.selected.index === 'number') {
            send(`PLAY_HIDDEN ${state.selected.index}`);
        }
        return;
    }
    const tokens = selectedTokens();
    if (tokens.length) {
        send(`PLAY ${formatCards(tokens)}`);
    }
}

function onRoom(code) {
    state.roomCode = code;
    pushMsg(`Room ${code}`);
    if (state.pendingRoomJoin) {
        state.pendingRoomJoin = false;
        joinCurrentRoom();
    } else if (state.phase === 'menu') {
        state.menuScreen = 'lobby';
    }
}

function onReadyState(name, value) {
    state.readyByPlayer[name] = ['1', 'true', 'TRUE', 'on', 'ON'].includes(value);
    if (name === state.selfName) {
        state.ready = state.readyByPlayer[name];
    }
}

function onPlayers(list) {
    state.players = list.split(',').filter(Boolean);
    pruneState();
    if (state.phase === 'menu' && state.players.includes(state.selfName)) {
        state.phase = 'pregame';
        state.menuScreen = 'lobby';
    }
    if (state.phase === 'pregame' && state.autoReadyAfterJoin && state.players.includes(state.selfName) && !state.ready) {
        sendReady();
        state.autoReadyAfterJoin = false;
    }
}

function onVisible(name, text) {
    const cards = parseCards(text);
    state.visibleByPlayer[name] = cards;
    if (name === state.selfName) {
        state.selfVisible = cards;
        maybeAutoReorderDone();
    }
}

function onHand(text) {
    state.selfHand = sortHand(parseCards(text));
    maybeAutoReorderDone();
}

function onHiddenCount(name, value) {
    const count = Number(value) || 0;
    state.hiddenCounts[name] = count;
    if (name === state.selfName) {
        state.selfHiddenCount = count;
    }
}

function onPlay(player, text) {
    const cards = parseCards(text);
    queuePlayAnimation(player, cards, false);
    state.pile.push(...cards);
    if (player === state.selfName) {
        state.selfHand = state.selfHand.filter((token) => !cards.includes(token));
        state.selfVisible = state.selfVisible.filter((token) => !cards.includes(token));
        state.selected = null;
    }
}

function onPlayHidden(player, text) {
    const cards = parseCards(text);
    queuePlayAnimation(player, cards, true);
    state.pile.push(...cards);
    if (player === state.selfName) {
        state.selfHand = state.selfHand.filter((token) => !cards.includes(token));
        state.selfVisible = state.selfVisible.filter((token) => !cards.includes(token));
        state.selfHiddenCount = Math.max(0, state.selfHiddenCount - 1);
        state.selected = null;
    }
}

function onPlayerWin(name, place) {
    state.finished[name] = Number(place) || 0;
    pushMsg(`${name} #${place}`);
}

function onPlayerLeft(name) {
    state.players = state.players.filter((player) => player !== name);
    pruneState();
    pushMsg(`${name} left`);
}

function pruneState() {
    const allowed = new Set(state.players);
    allowed.add(state.selfName);
    state.visibleByPlayer = Object.fromEntries(Object.entries(state.visibleByPlayer).filter(([name]) => allowed.has(name)));
    state.handCounts = Object.fromEntries(Object.entries(state.handCounts).filter(([name]) => allowed.has(name)));
    state.hiddenCounts = Object.fromEntries(Object.entries(state.hiddenCounts).filter(([name]) => allowed.has(name)));
    state.botSeats = new Set([...state.botSeats].filter((name) => allowed.has(name)));
    state.finished = Object.fromEntries(Object.entries(state.finished).filter(([name]) => allowed.has(name)));
    state.readyByPlayer = Object.fromEntries(Object.entries(state.readyByPlayer).filter(([name]) => allowed.has(name)));
}

function maybeAutoReorderDone() {
    if (
        state.pendingAutoDoneReorder &&
        state.phase === 'reorder' &&
        state.selfHand.length === 5 &&
        state.selfVisible.length === 3 &&
        !state.reorderDone
    ) {
        sendReorderDone();
        state.pendingAutoDoneReorder = false;
    }
}

function handleNetworkMessage(line) {
    const parsed = parseMessage(line);
    const command = parsed[0];
    const args = parsed.slice(1);

    switch (command) {
        case 'ROOM':
            if (args[0]) {
                onRoom(args[0].trim().toUpperCase());
            }
            break;
        case 'LOBBY_FILL_BOTS':
            if (args[0] !== undefined) {
                state.fillBotsEnabled = ['1', 'true', 'TRUE', 'on', 'ON'].includes(args[0]);
            }
            break;
        case 'READY_STATE':
            if (args.length >= 2) {
                onReadyState(args[0], args[1]);
            }
            break;
        case 'PLAYERS':
            if (args[0] !== undefined) {
                onPlayers(args[0]);
            }
            break;
        case 'START_GAME':
            state.phase = 'reorder';
            state.reorderDone = false;
            state.pendingAutoDoneReorder = state.autoDoneReorderOnce;
            state.selected = null;
            state.gameOverText = null;
            pushMsg('Initial deal complete. Reorder your cards, then press Done.');
            break;
        case 'BEGIN':
            state.phase = 'game';
            state.selected = null;
            state.gameOverText = null;
            pushMsg('Game started.');
            break;
        case 'TURN_START':
            if (args[0]) {
                state.turnPlayer = args[0];
                state.turnDeadline = now() + 60.0;
                state.selected = null;
                pushMsg(state.turnPlayer === state.selfName ? 'Your turn.' : `${state.turnPlayer} is playing.`);
            }
            break;
        case 'VISIBLE':
            if (args.length >= 2) {
                onVisible(args[0], args[1]);
            }
            break;
        case 'HAND_COUNT':
            if (args.length >= 2) {
                state.handCounts[args[0]] = Number(args[1]) || 0;
            }
            break;
        case 'HIDDEN_COUNT':
            if (args.length >= 2) {
                onHiddenCount(args[0], args[1]);
            }
            break;
        case 'HAND':
            if (args[0] !== undefined) {
                onHand(args[0]);
            }
            break;
        case 'PLAY':
            if (args.length >= 2) {
                onPlay(args[0], args[1]);
            }
            break;
        case 'PLAY_HIDDEN':
            if (args.length >= 2) {
                onPlayHidden(args[0], args[1]);
            }
            break;
        case 'DRAW_ALL':
            if (args[0]) {
                const drawnCount = state.pile.length;
                if (args[0] === state.selfName) {
                    state.selfHand = sortHand(state.selfHand.concat(state.pile));
                } else {
                    state.handCounts[args[0]] = (state.handCounts[args[0]] || 0) + drawnCount;
                }
                state.pile = [];
            }
            break;
        case 'PILE_CLEAR':
            state.pile = [];
            break;
        case 'DRAW_PILE_SIZE':
            if (args[0] !== undefined) {
                state.drawPileSize = Number(args[0]) || 0;
            }
            break;
        case 'PLAYER_WIN':
            if (args.length >= 2) {
                onPlayerWin(args[0], args[1]);
            }
            break;
        case 'GAME_OVER':
            if (args[0]) {
                state.phase = 'finished';
                state.gameOverText = `Winner: ${args[0]}`;
            }
            break;
        case 'PLAYER_QUIT':
            if (args[0]) {
                state.botSeats.add(args[0]);
                pushMsg(`${args[0]} is now bot-controlled.`);
            }
            break;
        case 'PLAYER_LEFT':
            if (args[0]) {
                onPlayerLeft(args[0]);
            }
            break;
        case 'INVALID':
            if (args[0]) {
                if (state.pendingRoomJoin) {
                    state.pendingRoomJoin = false;
                    state.joinError = args[0];
                }
                pushMsg(`Error: ${args[0]}`);
            }
            break;
        case 'READY':
            state.ready = true;
            break;
        default:
            break;
    }
}

function drawBackground(width, height) {
    const gradient = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, width * 0.7);
    gradient.addColorStop(0, COLORS.TABLE_GREEN);
    gradient.addColorStop(1, COLORS.TABLE_GREEN_DARK);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    ctx.strokeStyle = 'rgba(30, 92, 61, 0.65)';
    ctx.lineWidth = 1;
    for (let x = 0; x < width; x += 32) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
    }
    for (let y = 0; y < height; y += 32) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
    }

    ctx.fillStyle = 'rgba(70, 180, 110, 0.20)';
    ctx.beginPath();
    ctx.arc(width / 2, height / 2, 420, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = 'rgba(255, 255, 255, 0.08)';
    ctx.beginPath();
    ctx.arc(width / 2, height / 2, 180, 0, Math.PI * 2);
    ctx.fill();
}

function drawPanel(rect, title, subtitle, active) {
    const shadow = { x: rect.x + 4, y: rect.y + 4, width: rect.width, height: rect.height };
    drawRoundedPanel(shadow.x, shadow.y, shadow.width, shadow.height, '#000000', 18, 90);
    drawRoundedPanel(rect.x, rect.y, rect.width, rect.height, active ? COLORS.PANEL_SOFT : COLORS.PANEL, 18, 240);
    ctx.strokeStyle = active ? COLORS.GOLD : '#5a6968';
    ctx.lineWidth = 2;
    roundedRectPath(ctx, rect.x, rect.y, rect.width, rect.height, 18);
    ctx.stroke();
    if (title) {
        drawText(title, rect.x + sz(16), rect.y + sz(28), COLORS.WHITE, { font: sfont(20) });
    }
    if (subtitle) {
        drawText(subtitle, rect.x + sz(16), rect.y + sz(52), COLORS.MUTED, { font: sfont(18) });
    }
}

function drawMenuButton(name, label, center, color) {
    const rect = {
        x: center.x - sz(MENU_BUTTON_SIZE.width) / 2,
        y: center.y - sz(MENU_BUTTON_SIZE.height) / 2,
        width: sz(MENU_BUTTON_SIZE.width),
        height: sz(MENU_BUTTON_SIZE.height),
    };
    state.buttons[name] = rect;
    drawRoundedPanel(rect.x, rect.y, rect.width, rect.height, color, sz(18), 242);
    ctx.strokeStyle = COLORS.GOLD;
    ctx.lineWidth = 2;
    roundedRectPath(ctx, rect.x, rect.y, rect.width, rect.height, sz(18));
    ctx.stroke();
    drawText(label, center.x, center.y + sz(10), COLORS.WHITE, {
        font: sfont(28, 'bold'),
        align: 'center',
        baseline: 'middle',
    });
}

function drawMenu(width, height) {
    state.buttons = {};
    const titleRect = { x: width / 2 - sz(290), y: sz(80), width: sz(580), height: sz(100) };
    drawPanel(titleRect, '', '', false);
    ctx.strokeStyle = COLORS.GOLD;
    ctx.lineWidth = 2;
    roundedRectPath(ctx, titleRect.x, titleRect.y, titleRect.width, titleRect.height, sz(22));
    ctx.stroke();
    drawText('Card Game', width / 2, titleRect.y + sz(50), COLORS.WHITE, {
        font: sfont(52, 'bold'),
        align: 'center',
        baseline: 'middle',
    });

    if (state.menuScreen === 'main') {
        drawMenuButton('menu_play', 'Play', { x: width / 2, y: sz(270) }, COLORS.BLUE);
        drawMenuButton('menu_bot', 'Play Vs. Computer', { x: width / 2, y: sz(364) }, COLORS.PANEL_SOFT);
        drawMenuButton('menu_tutorial', 'Tutorial', { x: width / 2, y: sz(458) }, COLORS.PANEL_SOFT);
        drawMenuButton('menu_quit', 'Quit', { x: width / 2, y: sz(552) }, COLORS.RED);
        return;
    }

    if (state.menuScreen === 'play') {
        drawText('Create or join a room', width / 2, sz(200), COLORS.MUTED, {
            font: sfont(20),
            align: 'center',
            baseline: 'middle',
        });
        drawMenuButton('play_join', 'Join Room', { x: width / 2, y: sz(320) }, COLORS.BLUE);
        drawMenuButton('play_create', 'Create Room', { x: width / 2, y: sz(414) }, COLORS.PANEL_SOFT);
        drawMenuButton('play_back', 'Back', { x: width / 2, y: sz(508) }, COLORS.PANEL);
        return;
    }

    if (state.menuScreen === 'join_room') {
        const panelW = Math.min(sz(620), width - 20);
        const panelH = sz(320);
        const panel = { x: (width - panelW) / 2, y: height / 2 - panelH / 2, width: panelW, height: panelH };
        drawPanel(panel, '', '', false);
        ctx.strokeStyle = COLORS.GOLD;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, panel.x, panel.y, panel.width, panel.height, sz(22));
        ctx.stroke();
        drawText('Join Room', width / 2, panel.y + sz(44), COLORS.WHITE, {
            font: sfont(28, 'bold'),
            align: 'center',
            baseline: 'middle',
        });
        drawText('Enter lobby code', width / 2, panel.y + sz(90), COLORS.MUTED, {
            font: sfont(20),
            align: 'center',
            baseline: 'middle',
        });

        const inputMargin = Math.min(sz(80), panelW * 0.12);
        const inputRect = { x: panel.x + inputMargin, y: panel.y + sz(126), width: panel.width - inputMargin * 2, height: sz(62) };
        state.buttons.join_input = inputRect;
        drawRoundedPanel(inputRect.x, inputRect.y, inputRect.width, inputRect.height, COLORS.PANEL_SOFT, sz(14), 245);
        ctx.strokeStyle = COLORS.BLUE;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, inputRect.x, inputRect.y, inputRect.width, inputRect.height, sz(14));
        ctx.stroke();
        drawText(state.joinRoomCode || '', inputRect.x + sz(16), inputRect.y + sz(34), COLORS.WHITE, {
            font: sfont(24),
            baseline: 'middle',
        });

        const btnW = Math.min(sz(210), (panelW - inputMargin * 2 - sz(10)) / 2);
        const confirmRect = { x: panel.x + inputMargin, y: panel.y + sz(214), width: btnW, height: sz(58) };
        const cancelRect = { x: panel.x + panel.width - inputMargin - btnW, y: panel.y + sz(214), width: btnW, height: sz(58) };
        state.buttons.join_confirm = confirmRect;
        state.buttons.join_cancel = cancelRect;
        drawRoundedPanel(confirmRect.x, confirmRect.y, confirmRect.width, confirmRect.height, COLORS.BLUE, sz(14), 245);
        drawRoundedPanel(cancelRect.x, cancelRect.y, cancelRect.width, cancelRect.height, COLORS.PANEL_SOFT, sz(14), 245);
        ctx.strokeStyle = COLORS.GOLD;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, confirmRect.x, confirmRect.y, confirmRect.width, confirmRect.height, sz(14));
        ctx.stroke();
        roundedRectPath(ctx, cancelRect.x, cancelRect.y, cancelRect.width, cancelRect.height, sz(14));
        ctx.stroke();
        drawText('Join', confirmRect.x + confirmRect.width / 2, confirmRect.y + sz(30), COLORS.WHITE, {
            font: sfont(20),
            align: 'center',
            baseline: 'middle',
        });
        drawText('Cancel', cancelRect.x + cancelRect.width / 2, cancelRect.y + sz(30), COLORS.WHITE, {
            font: sfont(20),
            align: 'center',
            baseline: 'middle',
        });

        if (state.joinError) {
            drawText(state.joinError, width / 2, panel.y + panel.height - sz(26), COLORS.RED, {
                font: sfont(18),
                align: 'center',
                baseline: 'middle',
            });
        }
    }
}

function sampleTween(tween, timeNow) {
    if (!tween || tween.duration <= 0) {
        return tween ? tween.end : { x: 0, y: 0 };
    }
    const t = clamp((timeNow - tween.startTime) / tween.duration, 0, 1);
    const eased = easeOutQuad(t);
    return {
        x: tween.start.x + (tween.end.x - tween.start.x) * eased,
        y: tween.start.y + (tween.end.y - tween.start.y) * eased,
    };
}

function ensureMotion(token, target, timeNow) {
    const current = state.cardMotion.get(token);
    const last = state.lastPositions.get(token);
    if (!current) {
        const start = last || target;
        const duration = 0.05 + Math.abs(target.x - start.x) * 0.0025;
        state.cardMotion.set(token, { start, end: target, startTime: timeNow, duration });
    } else if (Math.abs(current.end.x - target.x) > 1 || Math.abs(current.end.y - target.y) > 1) {
        const sampled = sampleTween(current, timeNow);
        const duration = 0.05 + Math.abs(target.x - sampled.x) * 0.0025;
        state.cardMotion.set(token, { start: sampled, end: target, startTime: timeNow, duration });
    }
    const motion = state.cardMotion.get(token);
    const value = sampleTween(motion, timeNow);
    if (timeNow - motion.startTime >= motion.duration) {
        state.lastPositions.set(token, motion.end);
        state.cardMotion.set(token, { start: motion.end, end: motion.end, startTime: timeNow, duration: 0 });
    }
    return value;
}

function drawNotifications(width, height) {
    const visible = state.messages.filter(([stamp]) => now() - stamp <= 3.2);
    state.messages = visible;
    let y = height - sz(118);
    for (const [stamp, message] of visible.slice(-4)) {
        const alpha = clamp(255 - (now() - stamp) * 180, 50, 255);
        ctx.save();
        const fsize = sfont(18);
        ctx.font = fsize;
        ctx.globalAlpha = alpha / 255;
        const textWidth = ctx.measureText(message).width;
        const boxWidth = Math.ceil(textWidth) + sz(24);
        const boxHeight = sz(30);
        drawRoundedPanel(sz(18), y - 2, boxWidth, boxHeight, '#14181a', sz(14), alpha);
        drawText(message, sz(30), y + sz(16), COLORS.WHITE, { font: fsize });
        ctx.restore();
        y -= sz(30);
    }
}

function drawTopBars(width) {
    const turnText = state.turnPlayer === state.selfName ? 'Your turn' : `Turn: ${state.turnPlayer || 'Waiting'}`;
    const turnW = Math.min(sz(260), width * 0.38);
    const barH = sz(54);
    const turnRect = { x: sz(20), y: sz(20), width: turnW, height: barH };
    drawPanel(turnRect, '', '', false);
    ctx.strokeStyle = state.turnPlayer === state.selfName ? COLORS.GOLD : COLORS.BLUE;
    ctx.lineWidth = 2;
    roundedRectPath(ctx, turnRect.x, turnRect.y, turnRect.width, turnRect.height, sz(18));
    ctx.stroke();
    drawText(turnText, turnRect.x + turnRect.width / 2, turnRect.y + barH / 2, COLORS.WHITE, {
        font: sfont(20),
        align: 'center',
        baseline: 'middle',
    });

    const timerW = sz(140);
    const timerRect = { x: width - timerW - sz(20), y: sz(20), width: timerW, height: barH };
    drawPanel(timerRect, '', '', false);
    const remaining = state.turnDeadline ? Math.max(0, Math.ceil(state.turnDeadline - now())) : 60;
    ctx.strokeStyle = remaining <= 10 ? COLORS.RED : COLORS.GOLD;
    ctx.lineWidth = 2;
    roundedRectPath(ctx, timerRect.x, timerRect.y, timerRect.width, timerRect.height, sz(18));
    ctx.stroke();
    drawText(String(remaining), timerRect.x + timerRect.width / 2, timerRect.y + barH / 2, COLORS.WHITE, {
        font: sfont(20),
        align: 'center',
        baseline: 'middle',
    });

    if (state.roomCode) {
        const roomW = Math.min(sz(220), width - turnW - timerW - sz(80));
        if (roomW > sz(80)) {
            const roomRect = { x: width / 2 - roomW / 2, y: sz(64), width: roomW, height: sz(44) };
            drawPanel(roomRect, '', '', false);
            ctx.strokeStyle = COLORS.GOLD;
            ctx.lineWidth = 2;
            roundedRectPath(ctx, roomRect.x, roomRect.y, roomRect.width, roomRect.height, sz(14));
            ctx.stroke();
            drawText(`Room ${state.roomCode}`, roomRect.x + roomRect.width / 2, roomRect.y + sz(28), COLORS.MUTED, {
                font: sfont(18),
                align: 'center',
                baseline: 'middle',
            });
        }
    }
}

function drawCenterArea(width, height) {
    const cardW = sz(BACK_CARD_SIZE.width);
    const cardH = sz(BACK_CARD_SIZE.height);
    const drawRect = { x: width / 2 - sz(201), y: height / 2 - sz(66), width: cardW, height: cardH };
    const playRect = { x: width / 2 + sz(149), y: height / 2 - sz(66), width: sz(PLAY_CARD_SIZE.width), height: sz(PLAY_CARD_SIZE.height) };
    const pileCenter = playPileCenter(width, height);
    state.buttons.draw = { x: drawRect.x - sz(14), y: drawRect.y - sz(14), width: drawRect.width + sz(28), height: drawRect.height + sz(28) };
    state.buttons.play = { x: playRect.x - sz(14), y: playRect.y - sz(14), width: playRect.width + sz(28), height: playRect.height + sz(28) };
    const timeNow = now();
    state.playAnimations = state.playAnimations.filter((item) => timeNow - item.startTime <= item.duration + 0.18);
    const animatingTokens = new Set(state.playAnimations.map((item) => item.token));

    const stackCount = Math.min(6, state.drawPileSize);
    if (stackCount) {
        for (let index = 0; index < stackCount; index += 1) {
            const offset = Math.min(10, index * 3);
            drawBackCard(drawRect.x + drawRect.width / 2 - offset, drawRect.y + drawRect.height / 2 - offset, cardW, cardH);
        }
    } else {
        ctx.strokeStyle = 'rgba(72, 107, 103, 0.95)';
        ctx.lineWidth = 2;
        roundedRectPath(ctx, drawRect.x - 4, drawRect.y - 4, drawRect.width + 8, drawRect.height + 8, sz(14));
        ctx.stroke();
    }

    if (state.pile.length) {
        const pileShow = state.pile.filter((token) => !animatingTokens.has(token)).slice(-6);
        const pileDraws = pileShow.map((token, index) => ({
            token,
            target: {
                x: pileCenter.x + index * sz(3),
                y: pileCenter.y - index * sz(3),
            },
        })).sort((left, right) => left.target.x - right.target.x);
        pileDraws.forEach(({ token, target }) => {
            const center = ensureMotion(token, target, timeNow);
            drawCardImage(cardFace(token), center.x, center.y, sz(PLAY_CARD_SIZE.width), sz(PLAY_CARD_SIZE.height));
            state.cardRects.set(token, { x: center.x - sz(PLAY_CARD_SIZE.width) / 2, y: center.y - sz(PLAY_CARD_SIZE.height) / 2, width: sz(PLAY_CARD_SIZE.width), height: sz(PLAY_CARD_SIZE.height) });
            state.lastPositions.set(token, center);
        });
    } else {
        drawBackCard(pileCenter.x, pileCenter.y, sz(BACK_CARD_SIZE.width), sz(BACK_CARD_SIZE.height));
    }

    ctx.strokeStyle = 'rgba(72, 107, 103, 0.95)';
    ctx.lineWidth = 2;
    roundedRectPath(ctx, playRect.x - 4, playRect.y - 4, playRect.width + 8, playRect.height + 8, sz(14));
    ctx.stroke();

    drawText('DRAW', drawRect.x + drawRect.width / 2, drawRect.y + drawRect.height / 2 - sz(18), COLORS.WHITE, {
        font: sfont(28, 'bold'),
        align: 'center',
        baseline: 'middle',
    });
    drawText(`cards: ${state.drawPileSize}`, drawRect.x + drawRect.width / 2, drawRect.y + drawRect.height / 2 + sz(28), COLORS.WHITE, {
        font: sfont(18),
        align: 'center',
        baseline: 'middle',
    });
    drawText('PLAY', playRect.x + playRect.width / 2, playRect.y + playRect.height / 2 - sz(18), COLORS.WHITE, {
        font: sfont(28, 'bold'),
        align: 'center',
        baseline: 'middle',
    });
    drawText(`cards: ${state.pile.length}`, playRect.x + playRect.width / 2, playRect.y + playRect.height / 2 + sz(28), COLORS.WHITE, {
        font: sfont(18),
        align: 'center',
        baseline: 'middle',
    });

    for (const item of [...state.playAnimations].sort((left, right) => {
        const xDelta = left.end.x - right.end.x;
        if (xDelta !== 0) {
            return xDelta;
        }
        return left.startTime - right.startTime;
    })) {
        const elapsed = timeNow - item.startTime;
        if (elapsed < 0) {
            continue;
        }
        const progress = clamp(elapsed / Math.max(0.001, item.duration), 0, 1);
        const x = item.start.x + (item.end.x - item.start.x) * progress;
        const y = item.start.y + (item.end.y - item.start.y) * progress;
        const token = item.token;
        const face = cardFace(token);
        const image = imageForFace(face);
        ctx.save();
        ctx.globalAlpha = 1;
        ctx.translate(x, y);
        const animW = sz(PLAY_CARD_SIZE.width);
        const animH = sz(PLAY_CARD_SIZE.height);
        if (image && image.complete && image.naturalWidth > 0) {
            ctx.drawImage(image, -animW / 2, -animH / 2, animW, animH);
        } else {
            ctx.fillStyle = '#cbb68c';
            roundedRectPath(ctx, -animW / 2, -animH / 2, animW, animH, sz(10));
            ctx.fill();
        }
        ctx.restore();
    }

    if (currentTurnIsSelf()) {
        ctx.strokeStyle = COLORS.BLUE;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, state.buttons.draw.x, state.buttons.draw.y, state.buttons.draw.width, state.buttons.draw.height, sz(18));
        ctx.stroke();
        ctx.strokeStyle = state.selected ? COLORS.GOLD : COLORS.BLUE;
        roundedRectPath(ctx, state.buttons.play.x, state.buttons.play.y, state.buttons.play.width, state.buttons.play.height, sz(18));
        ctx.stroke();
    }

    if (state.selected && currentTurnIsSelf()) {
        ctx.strokeStyle = COLORS.GOLD;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, playRect.x - sz(7), playRect.y - sz(7), playRect.width + sz(14), playRect.height + sz(14), sz(16));
        ctx.stroke();
    }
}

function drawPlayerZone(name, slot, timeNow) {
    const isSelf = name === state.selfName;
    const visible = isSelf ? state.selfVisible : (state.visibleByPlayer[name] || []);
    const hiddenCount = isSelf ? state.selfHiddenCount : (state.hiddenCounts[name] ?? 3);
    const handCount = isSelf ? state.selfHand.length : (state.handCounts[name] ?? 0);
    const hand = isSelf ? state.selfHand : [];
    const label = `${name}${name === state.selfName ? ' (you)' : ''}${state.botSeats.has(name) ? ' [bot]' : ''}`;

    const panelRect = { x: slot.panel.x - sz(140), y: slot.panel.y - sz(43), width: sz(280), height: sz(86) };
    drawPanel(panelRect, label, `Hand ${handCount}  Hidden ${hiddenCount}`, state.turnPlayer === name && state.phase === 'game');

    const hiddenShow = Math.min(hiddenCount, 3);
    const hiddenScale = isSelf ? 1.0 : 0.72;
    const hiddenWidth = sz(Math.round(BACK_CARD_SIZE.width * hiddenScale));
    const hiddenHeight = sz(Math.round(BACK_CARD_SIZE.height * hiddenScale));
    const hiddenSpacing = Math.max(sz(20), Math.round(hiddenWidth * 0.58));
    const hiddenOffset = (hiddenShow - 1) / 2;
    const hiddenDraws = [];

    for (let index = 0; index < hiddenShow; index += 1) {
        const offsetIndex = (index - hiddenOffset) * hiddenSpacing;
        const center = {
            x: slot.visible.x + slot.spread.x * offsetIndex,
            y: slot.visible.y + slot.spread.y * offsetIndex,
        };
        const rect = { x: center.x - hiddenWidth / 2, y: center.y - hiddenHeight / 2, width: hiddenWidth, height: hiddenHeight };
        if (isSelf) {
            state.hiddenRects.push(rect);
        }
        hiddenDraws.push({ rect, center });
    }
    hiddenDraws.sort((a, b) => a.rect.x - b.rect.x || a.rect.y - b.rect.y).forEach(({ rect, center }) => {
        drawBackCard(center.x, center.y, rect.width, rect.height, slot.angle, isSelf ? 1 : 0.95);
    });

    if (isSelf && state.selected && state.selected.kind === 'hidden' && state.hiddenRects.length) {
        const union = state.hiddenRects.reduce((acc, rect) => ({
            x: Math.min(acc.x, rect.x),
            y: Math.min(acc.y, rect.y),
            right: Math.max(acc.right, rect.x + rect.width),
            bottom: Math.max(acc.bottom, rect.y + rect.height),
        }), { x: Infinity, y: Infinity, right: -Infinity, bottom: -Infinity });
        const focus = { x: union.x - 5, y: union.y - 5, width: union.right - union.x + 10, height: union.bottom - union.y + 10 };
        ctx.strokeStyle = COLORS.GOLD;
        ctx.lineWidth = 3;
        roundedRectPath(ctx, focus.x, focus.y, focus.width, focus.height, sz(14));
        ctx.stroke();
    }

    if (visible.length) {
        const spacing = sz(isSelf ? (visible.length <= 4 ? 64 : visible.length <= 7 ? 58 : 50) : 40);
        const offset = (visible.length - 1) / 2;
        const renderCards = visible.map((token, index) => {
            const target = {
                x: slot.visible.x + slot.spread.x * (index - offset) * spacing,
                y: slot.visible.y + slot.spread.y * (index - offset) * spacing,
            };
            return { token, index, target };
        }).sort((left, right) => {
            const leftX = left.target.x;
            const rightX = right.target.x;
            if (leftX !== rightX) {
                return leftX - rightX;
            }
            return left.index - right.index;
        });
        renderCards.forEach(({ token, target }) => {
            const center = ensureMotion(token, target, timeNow);
            const baseSize = isSelf ? PLAY_CARD_SIZE : SMALL_CARD_SIZE;
            const otherScale = isSelf ? 1 : 0.72;
            const w = sz(Math.round(baseSize.width * otherScale));
            const h = sz(Math.round(baseSize.height * otherScale));
            const rect = { x: center.x - w / 2, y: center.y - h / 2, width: w, height: h };
            if (state.dragging && state.dragging.token === token) {
                const drag = state.dragging.current || state.dragging.start;
                rect.x += drag.x - state.dragging.start.x;
                rect.y += drag.y - state.dragging.start.y;
            }
            if (selectedTokens().includes(token)) {
                const progress = clamp((timeNow - state.selectedAt) / 0.14, 0, 1);
                rect.y -= Math.round(sz(18) * (1 - (1 - progress) * (1 - progress)));
            }
            state.cardRects.set(token, rect);
            state.lastPositions.set(token, { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
            drawCardImage(cardFace(token), rect.x + rect.width / 2, rect.y + rect.height / 2, rect.width, rect.height, slot.angle || 0);
        });
    }

    if (isSelf) {
        state.buttons.hand_zone = { x: slot.hand.x - sz(230), y: slot.hand.y - sz(80), width: sz(460), height: sz(160) };
        state.buttons.visible_zone = { x: slot.visible.x - sz(230), y: slot.visible.y - sz(70), width: sz(460), height: sz(140) };
        if (hand.length) {
            let spacing = sz(74);
            if (hand.length > 5 && hand.length <= 8) {
                spacing = sz(68);
            } else if (hand.length > 8 && hand.length <= 11) {
                spacing = sz(60);
            } else if (hand.length > 11) {
                spacing = sz(52);
            }
            const offset = (hand.length - 1) / 2;
            const handCardScale = clamp(1.0 - Math.max(0, hand.length - 8) * 0.04, 0.72, 1.0);
            const widthScaled = sz(Math.round(PLAY_CARD_SIZE.width * handCardScale));
            const heightScaled = sz(Math.round(PLAY_CARD_SIZE.height * handCardScale));
            const renderCards = hand.map((token, index) => {
                const target = {
                    x: slot.hand.x + slot.spread.x * (index - offset) * spacing,
                    y: slot.hand.y + slot.spread.y * (index - offset) * spacing,
                };
                return { token, index, target };
            }).sort((left, right) => {
                const leftX = left.target.x;
                const rightX = right.target.x;
                if (leftX !== rightX) {
                    return leftX - rightX;
                }
                return left.index - right.index;
            });

            renderCards.forEach(({ token, target }) => {
                const center = ensureMotion(token, target, timeNow);
                const rect = { x: center.x - widthScaled / 2, y: center.y - heightScaled / 2, width: widthScaled, height: heightScaled };
                if (selectedTokens().includes(token)) {
                    const progress = clamp((timeNow - state.selectedAt) / 0.14, 0, 1);
                    rect.y -= Math.round(sz(20) * (1 - (1 - progress) * (1 - progress)));
                }
                if (state.dragging && state.dragging.token === token) {
                    const drag = state.dragging.current || state.dragging.start;
                    rect.x += drag.x - state.dragging.start.x;
                    rect.y += drag.y - state.dragging.start.y;
                }
                state.cardRects.set(token, rect);
                state.lastPositions.set(token, { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 });
                drawCardImage(cardFace(token), rect.x + rect.width / 2, rect.y + rect.height / 2, rect.width, rect.height);
            });
        }
    } else if (handCount) {
        const backShow = Math.min(handCount, 5);
        const backScale = 0.65;
        const backWidth = sz(Math.round(BACK_CARD_SIZE.width * backScale));
        const backHeight = sz(Math.round(BACK_CARD_SIZE.height * backScale));
        const backSpacing = Math.max(sz(18), Math.round(backWidth * 0.56));
        const backOffset = (backShow - 1) / 2;
        const backCards = Array.from({ length: backShow }, (_unused, index) => {
            const offsetIndex = (index - backOffset) * backSpacing;
            return {
                index,
                center: {
                    x: slot.hand.x + slot.spread.x * offsetIndex,
                    y: slot.hand.y + slot.spread.y * offsetIndex,
                },
            };
        }).sort((left, right) => {
            if (left.center.x !== right.center.x) {
                return left.center.x - right.center.x;
            }
            return left.index - right.index;
        });
        backCards.forEach(({ center }) => {
            drawBackCard(center.x, center.y, backWidth, backHeight, slot.angle);
        });
    }
}

function drawGameOver(width, height) {
    if (state.phase !== 'finished' && !state.gameOverText) {
        return;
    }
    ctx.save();
    ctx.fillStyle = 'rgba(8, 18, 14, 0.60)';
    ctx.fillRect(0, 0, width, height);
    ctx.restore();
    const rankings = Object.entries(state.finished).sort((left, right) => left[1] - right[1]);
    const winnerName = rankings.length ? rankings[0][0] : (state.gameOverText ? state.gameOverText.replace('Winner: ', '') : '');
    const bannerHeight = sz(140) + Math.max(0, rankings.length - 1) * sz(32);
    const bannerW = Math.min(sz(460), width - 20);
    const banner = { x: (width - bannerW) / 2, y: height / 2 - bannerHeight / 2, width: bannerW, height: bannerHeight };
    drawPanel(banner, '', '', false);
    ctx.strokeStyle = COLORS.GOLD;
    ctx.lineWidth = 3;
    roundedRectPath(ctx, banner.x, banner.y, banner.width, banner.height, sz(22));
    ctx.stroke();
    drawText(`Winner: ${winnerName}`, width / 2, banner.y + sz(60), COLORS.WHITE, {
        font: sfont(52, 'bold'),
        align: 'center',
        baseline: 'middle',
    });
    rankings.slice(1).forEach(([name, place], index) => {
        const suffix = { 1: 'st', 2: 'nd', 3: 'rd' }[place] || 'th';
        drawText(`${place}${suffix}: ${name}`, width / 2, banner.y + sz(100) + index * sz(32), COLORS.MUTED, {
            font: sfont(20),
            align: 'center',
            baseline: 'middle',
        });
    });
}

function drawGame() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    sc = getScale(width, height);
    drawBackground(width, height);

    if (state.phase === 'menu') {
        drawMenu(width, height);
        drawNotifications(width, height);
        return;
    }

    state.cardRects = new Map();
    state.hiddenRects = [];
    state.buttons = {};
    const layout = playerLayoutShell(Math.max(1, state.players.length || 1), width, height);
    const names = state.players.length ? state.players.slice() : [state.selfName];
    if (state.selfName && names.includes(state.selfName)) {
        names.splice(names.indexOf(state.selfName), 1);
    }
    if (state.selfName) {
        names.unshift(state.selfName);
    }
    state.slotLayout = {};
    names.forEach((name, index) => {
        state.slotLayout[name] = layout[index] || layout[0];
    });

    drawCenterArea(width, height);
    names.forEach((name) => {
        const slot = state.slotLayout[name];
        if (slot) {
            drawPlayerZone(name, slot, now());
        }
    });
    drawTopBars(width);
    drawNotifications(width, height);

    if (state.phase === 'pregame') {
        const readyW = sz(200);
        const readyH = sz(54);
        const readyRect = { x: width / 2 - readyW / 2, y: sz(18), width: readyW, height: readyH };
        state.buttons.ready = readyRect;
        drawRoundedPanel(readyRect.x, readyRect.y, readyRect.width, readyRect.height, state.ready ? COLORS.PANEL : COLORS.GOLD, sz(18), 240);
        ctx.strokeStyle = COLORS.GOLD;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, readyRect.x, readyRect.y, readyRect.width, readyRect.height, sz(18));
        ctx.stroke();
        drawText('READY', readyRect.x + readyRect.width / 2, readyRect.y + readyH / 2, state.ready ? COLORS.WHITE : COLORS.BLACK, {
            font: sfont(28, 'bold'),
            align: 'center',
            baseline: 'middle',
        });

        const fillW = sz(236);
        const fillH = sz(50);
        const fillRect = { x: width / 2 + sz(122), y: sz(20), width: fillW, height: fillH };
        state.buttons.fill_bots = fillRect;
        drawRoundedPanel(fillRect.x, fillRect.y, fillRect.width, fillRect.height, COLORS.PANEL, sz(14), 240);
        ctx.strokeStyle = COLORS.BLUE;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, fillRect.x, fillRect.y, fillRect.width, fillRect.height, sz(14));
        ctx.stroke();
        drawText(`${state.fillBotsEnabled ? '[x]' : '[ ]'} Fill Bots`, fillRect.x + fillRect.width / 2, fillRect.y + fillH / 2, COLORS.WHITE, {
            font: sfont(18),
            align: 'center',
            baseline: 'middle',
        });
    } else if (state.phase === 'reorder') {
        const doneW = sz(200);
        const doneH = sz(54);
        const doneRect = { x: width / 2 - doneW / 2, y: sz(18), width: doneW, height: doneH };
        state.buttons.done = doneRect;
        drawRoundedPanel(doneRect.x, doneRect.y, doneRect.width, doneRect.height, state.reorderDone ? COLORS.PANEL : COLORS.BLUE, sz(18), 240);
        ctx.strokeStyle = COLORS.BLUE;
        ctx.lineWidth = 2;
        roundedRectPath(ctx, doneRect.x, doneRect.y, doneRect.width, doneRect.height, sz(18));
        ctx.stroke();
        drawText('DONE', doneRect.x + doneRect.width / 2, doneRect.y + doneH / 2, COLORS.WHITE, {
            font: sfont(28, 'bold'),
            align: 'center',
            baseline: 'middle',
        });
    }

    if (state.selected && currentTurnIsSelf()) {
        const focusW = Math.min(sz(300), width - 20);
        const focusH = sz(42);
        const focusRect = { x: (width - focusW) / 2, y: height / 2 - sz(220), width: focusW, height: focusH };
        drawRoundedPanel(focusRect.x, focusRect.y, focusRect.width, focusRect.height, COLORS.PANEL, sz(14), 220);
        if (state.selected.kind === 'hidden') {
            drawText('Hidden card selected', focusRect.x + focusRect.width / 2, focusRect.y + sz(27), COLORS.WHITE, {
                font: sfont(18),
                align: 'center',
                baseline: 'middle',
            });
        } else {
            const token = selectedCardToken();
            drawText(token ? `Selected: ${tokenLabel(token)} — tap PLAY` : 'Tap PLAY to play', focusRect.x + focusRect.width / 2, focusRect.y + sz(27), COLORS.WHITE, {
                font: sfont(18),
                align: 'center',
                baseline: 'middle',
            });
        }
    }

    drawGameOver(width, height);
}

function onMouseDown(event) {
    if (event.button !== 0) {
        return;
    }
    if (!event._isTouch) {
        isTouchInteraction = false;
    }
    const point = canvasPoint(event);

    if (state.phase === 'menu') {
        if (state.menuScreen === 'main') {
            if (clickButton('menu_play', point)) {
                state.menuScreen = 'play';
                return;
            }
            if (clickButton('menu_bot', point)) {
                state.autoReadyAfterJoin = true;
                state.autoDoneReorderOnce = false;
                requestCreateRoom(true);
                return;
            }
            if (clickButton('menu_tutorial', point)) {
                startTutorial();
                return;
            }
            if (clickButton('menu_quit', point)) {
                pushMsg('Close the tab to quit.');
                return;
            }
            return;
        }

        if (state.menuScreen === 'play') {
            if (clickButton('play_join', point)) {
                state.menuScreen = 'join_room';
                state.joinError = null;
                state.joinRoomCode = '';
                return;
            }
            if (clickButton('play_create', point)) {
                state.autoReadyAfterJoin = false;
                state.autoDoneReorderOnce = false;
                requestCreateRoom(true);
                return;
            }
            if (clickButton('play_back', point)) {
                state.menuScreen = 'main';
                return;
            }
            return;
        }

        if (state.menuScreen === 'join_room') {
            if (clickButton('join_input', point)) {
                // Use a native prompt so mobile keyboards appear
                const code = window.prompt('Enter room code:', state.joinRoomCode);
                if (code !== null) {
                    state.joinRoomCode = code.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
                }
                return;
            }
            if (clickButton('join_confirm', point)) {
                state.autoReadyAfterJoin = false;
                state.autoDoneReorderOnce = false;
                requestJoinRoom(state.joinRoomCode);
                return;
            }
            if (clickButton('join_cancel', point)) {
                state.menuScreen = 'play';
                state.joinError = null;
                return;
            }
        }
        return;
    }

    if (state.phase === 'reorder' && state.reorderDone) {
        return;
    }

    if (state.phase === 'pregame' && clickButton('fill_bots', point)) {
        sendFillBots(!state.fillBotsEnabled);
        return;
    }
    if (clickButton('ready', point)) {
        sendReady();
        return;
    }
    if (clickButton('done', point)) {
        sendReorderDone();
        return;
    }
    if (clickButton('play', point)) {
        playSelected();
        return;
    }
    if (clickButton('draw', point)) {
        sendDraw();
        return;
    }

    const allowedTokens = new Set(state.selfHand.concat(state.selfVisible));
    for (const [token, rect] of state.cardRects.entries()) {
        if (!allowedTokens.has(token)) {
            continue;
        }
        if (point.x < rect.x || point.x > rect.x + rect.width || point.y < rect.y || point.y > rect.y + rect.height) {
            continue;
        }
        if (state.phase === 'reorder') {
            state.dragging = { token, start: point, origin: { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 } };
            return;
        }
        if (currentTurnIsSelf()) {
            state.dragging = { token, start: point, origin: { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 } };
            return;
        }
    }

    if (state.phase !== 'reorder' && currentTurnIsSelf() && !state.selfHand.length && !state.selfVisible.length && state.selfHiddenCount > 0) {
        state.selected = { kind: 'hidden', index: hiddenIndexFromPoint(point) };
        state.selectedAt = now();
        pushMsg(`Selected hidden card ${state.selected.index + 1}.`);
        return;
    }

    if (state.phase !== 'reorder' && currentTurnIsSelf() && !state.selfVisible.length && state.selfHiddenCount > 0 && state.selfHand.length && selfHandRank() !== null) {
        state.selected = { kind: 'hidden', index: hiddenIndexFromPoint(point), tokens: [...state.selfHand] };
        state.selectedAt = now();
        pushMsg(`Selected hidden card ${state.selected.index + 1} with matching hand.`);
        return;
    }
}

function onMouseMove(event) {
    if (!state.dragging) {
        return;
    }
    state.dragging.current = canvasPoint(event);
}

function onMouseUp(event) {
    if (event.button !== 0 || !state.dragging) {
        return;
    }
    const point = canvasPoint(event);
    const token = state.dragging.token;
    const moved = Math.hypot(point.x - state.dragging.start.x, point.y - state.dragging.start.y);

    if (state.phase === 'reorder' && state.reorderDone) {
        state.dragging = null;
        return;
    }

    const dragThreshold = isTouchInteraction ? TOUCH_DRAG_THRESHOLD : DRAG_THRESHOLD;
    if (moved < dragThreshold) {
        if (state.phase === 'reorder') {
            moveTokenBetweenZones(token, state.selfHand.includes(token));
            emitReorder();
            state.selected = null;
            state.selectedAt = 0;
        } else if (currentTurnIsSelf()) {
            const last = state.lastCardClick;
            const isDoubleClick = last.token === token && (now() - last.time) <= DOUBLE_CLICK_WINDOW;
            if (isDoubleClick) {
                const tokens = sameRankTokens(token);
                if (tokens.length) {
                    state.selected = { kind: 'card', tokens };
                    state.selectedAt = now();
                    send(`PLAY ${formatCards(tokens)}`);
                } else {
                    toggleCardFocus(token);
                }
            } else {
                toggleCardFocus(token);
            }
            state.lastCardClick = { token, selected: selectedTokens().includes(token), time: now() };
        }
        state.dragging = null;
        return;
    }

    if (state.phase === 'reorder') {
        if (dropInZone(point, 'visible_zone')) {
            moveTokenBetweenZones(token, true);
            emitReorder();
        } else if (dropInZone(point, 'hand_zone')) {
            moveTokenBetweenZones(token, false);
            emitReorder();
        }
        state.dragging = null;
        return;
    }

    if (currentTurnIsSelf() && state.selfHand.includes(token)) {
        if (dropInZone(point, 'play')) {
            const tokens = selectedTokens();
            if (tokens.length) {
                send(`PLAY ${formatCards(tokens)}`);
            } else {
                state.selected = { kind: 'card', tokens: [token] };
                state.selectedAt = now();
                send(`PLAY ${formatCards([token])}`);
            }
        } else if (dropInZone(point, 'hand_zone')) {
            state.selfHand = reorderTokenInList(state.selfHand, token, point);
            emitReorder();
        } else if (dropInZone(point, 'visible_zone')) {
            state.selfHand = reorderTokenInList(state.selfHand, token, point);
            emitReorder();
        }
    }

    state.dragging = null;
}

function touchCanvasPoint(touch) {
    const rect = canvas.getBoundingClientRect();
    return {
        x: touch.clientX - rect.left,
        y: touch.clientY - rect.top,
    };
}

function makeSyntheticMouseEvent(touch) {
    return { button: 0, clientX: touch.clientX, clientY: touch.clientY, _isTouch: true };
}

function onTouchStart(event) {
    event.preventDefault();
    if (event.touches.length === 1) {
        isTouchInteraction = true;
        onMouseDown(makeSyntheticMouseEvent(event.touches[0]));
    }
}

function onTouchMove(event) {
    event.preventDefault();
    if (event.touches.length === 1) {
        onMouseMove(makeSyntheticMouseEvent(event.touches[0]));
    }
}

function onTouchEnd(event) {
    event.preventDefault();
    if (event.changedTouches.length) {
        onMouseUp(makeSyntheticMouseEvent(event.changedTouches[0]));
    }
}

function onKeyDown() {
    if (state.phase === 'menu' && state.menuScreen === 'join_room') {
        if (event.key === 'Enter') {
            state.autoReadyAfterJoin = false;
            state.autoDoneReorderOnce = false;
            requestJoinRoom(state.joinRoomCode);
            event.preventDefault();
            return;
        }
        if (event.key === 'Escape') {
            state.menuScreen = 'play';
            state.joinError = null;
            event.preventDefault();
            return;
        }
        if (event.key === 'Backspace') {
            state.joinRoomCode = state.joinRoomCode.slice(0, -1);
            event.preventDefault();
            return;
        }
        if (/^[a-z0-9]$/i.test(event.key)) {
            state.joinRoomCode = (state.joinRoomCode + event.key).toUpperCase().slice(0, 8);
            event.preventDefault();
        }
        return;
    }
}

async function loadImages() {
    const promises = [];
    for (const suit of SUITS) {
        for (const rank of RANKS) {
            const face = `${rank}${suit}`;
            const image = new Image();
            const promise = new Promise((resolve) => {
                image.onload = resolve;
                image.onerror = resolve;
            });
            image.src = `assets/${face}.png`;
            cardImages.set(face, image);
            promises.push(promise);
        }
    }
    const backLoaded = new Promise((resolve) => {
        backImage.onload = resolve;
        backImage.onerror = resolve;
    });
    promises.push(backLoaded);
    await Promise.all(promises);
}

function animate() {
    drawGame();
    requestAnimationFrame(animate);
}

function onConnect() {
    const addrInput = document.getElementById('serverAddr');
    const userInput = document.getElementById('user');
    if (!addrInput || !userInput) {
        return;
    }
    const addr = addrInput.value.trim();
    const user = userInput.value.trim();
    if (!addr || !user) {
        pushMsg('Enter both fields.');
        return;
    }
    const parts = addr.split(':');
    if (parts.length !== 2) {
        pushMsg('Format: ip:port');
        return;
    }
    const host = parts[0].trim();
    const port = Number(parts[1]);
    if (!host || Number.isNaN(port)) {
        pushMsg('Invalid address');
        return;
    }

    state.selfName = user;
    const url = `ws://${host}:${port}`;
    pushMsg(`Connecting to ${url}...`);

    try {
        ws = new WebSocket(url);
        ws.onopen = () => {
            connectModal('connModal', false);
            pushMsg('Connected.');
        };
        ws.onmessage = (event) => handleNetworkMessage(event.data);
        ws.onclose = () => {
            pushMsg('Disconnected.');
            connectModal('connModal', true);
        };
        ws.onerror = () => {
            pushMsg('Connection error.');
        };
    } catch (error) {
        console.error(error);
        pushMsg('Failed to create connection.');
    }
}

async function init() {
    canvas = document.getElementById('gameCanvas');
    if (!canvas) {
        return;
    }
    ctx = canvas.getContext('2d');
    if (!ctx) {
        return;
    }

    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
    window.addEventListener('keydown', onKeyDown);
    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('mouseleave', onMouseUp);
    // Touch support
    canvas.addEventListener('touchstart', onTouchStart, { passive: false });
    canvas.addEventListener('touchmove', onTouchMove, { passive: false });
    canvas.addEventListener('touchend', onTouchEnd, { passive: false });
    canvas.addEventListener('touchcancel', onTouchEnd, { passive: false });
    // Prevent context menu on long-press
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());

    const connectBtn = document.getElementById('connectBtn');
    if (connectBtn) {
        connectBtn.addEventListener('click', onConnect);
    }
    const userInput = document.getElementById('user');
    if (userInput) {
        userInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                onConnect();
            }
        });
    }

    await loadImages();
    connectModal('connModal', true);
    requestAnimationFrame(animate);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}