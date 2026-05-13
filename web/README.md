# Web Client

Browser-based client for the Card Game. Connects via WebSocket to the same server as the desktop client.

## Quick Start

1. **Start the game server** (if not already running):
   ```bash
   python src/server.py
   ```
   Server runs on `ws://omena0.txx.fi:20069` by default.

2. **Serve the web client** (in another terminal):
   ```bash
   python serve_web.py
   ```
   This starts HTTP server at `http://localhost:8080`

3. **Open browser** → `http://localhost:8080`

4. **Enter credentials**:
   - Server Address: `omena0.txx.fi:20069` (or your server's IP:port)
   - Username: your player name

5. Click **Connect**

6. For "Play Vs. Computer": click the button → it creates a room with bots and joins you automatically. You'll see the lobby with READY and Fill With Bots options. Click READY to start.

## Files

- `web/index.html` – Main page
- `web/client.js` – Complete game client (~650 lines)
- `web/style.css` – UI styles
- `web/assets/` – Card images (54 PNGs)
- `serve_web.py` – Python HTTP server

## Features

- Full gameplay identical to desktop
- Drag-and-drop card reordering
- Real-time WebSocket communication
- Notifications, game over screen, player panels
- Works in any modern browser

## Troubleshooting

**Bots don't appear to join:** The server auto-fills bots when all human players are ready. Click READY and wait a moment.

**Cards not showing:** Ensure `web/assets/` contains all 54 card PNGs (AS, 2C, ..., KH, etc.)

**Connection refused:** Verify server is running on port 8765. Change the address in the connect modal if your server uses a different port.

**Can't reorder:** Make sure you're in the reorder phase (message appears: "Reorder cards, then press DONE."). Drag cards between hand and visible zones.

**Can't play:** Wait for "Your turn" message and gold-highlighted turn indicator.
